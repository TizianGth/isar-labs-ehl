"""Submission: the model fills the bid form on the website with the computed answer, signs with the TAN from the
mailbox and confirms the browser dialog. Before the final click the model-read review page is compared field by
field with our answer (review.py); afterwards success is only reported when the website confirms the receipt.
States: prepared (dry run) / attempted / confirmed / failed / unknown. Set RFQ_DRY_RUN=1 to stop before the binding
submission (local tests).
"""
import asyncio
import os
import re
import time
from decimal import Decimal

from .. import llm
from ..log import log
from ..rules import parse
from .explorer import TOOLS, Explorer, Mission, _tool
from .prompts import EXTRACT_SYSTEM, KINDS
from .review import validate_review

SUBMIT_TOOLS = [t for t in TOOLS if t["function"]["name"] not in
                ("read_all_pages", "read_collection", "extract", "extract_urls", "read_document")] + [
    _tool("get_tan", "AFTER you clicked the website's button that requests/sends the signing TAN: fetch the newest "
          "TAN from the bidder's mailbox. Returns the TAN.", {}, []),
    _tool("submit_bid", "Final binding submission: clicks the given button and confirms the browser dialog. Only on "
          "the final review/sign page with the TAN entered.", {"ref": {"type": "string"}}, ["ref"]),
]


def de(value, digits: int = 2) -> str:
    """German number format: 1234.5 -> '1.234,50'."""
    text = f"{Decimal(str(value)):,.{digits}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def answer_table(answer: dict, skipped: list[str], breakdown: dict | None = None) -> str:
    lines = [f"tender_reference: {answer['tender_reference']}", f"decision: {answer['decision']}",
             f"discount percent: {de(answer['discount_percent'])} (type it as {_plain(answer['discount_percent'])})",
             f"net total EUR: {de(answer['total_net'])}"]
    for p in answer["positions"]:
        lines.append(f"- position {p['position']}: material {p['material_number']}; quantity {p['quantity']} pieces; "
                     f"unit price net {de(p['unit_price'])}; base price per piece {de(p['base_unit_price'])}; "
                     f"surcharges per piece {de(p['surcharge_per_unit'])}; packaging per piece {de(p['packaging_per_unit'])}; "
                     f"project bonus for the whole position {de(p['rebate_total'])}; lead time {p['lead_time_weeks']} weeks")
        filters = ((breakdown or {}).get(p["position"]) or {}).get("filters")
        if filters:
            lines.append("    product filters for this material: " + ", ".join(f"{k} {v}" for k, v in filters.items() if v)
                         + " (certification 'none' = without certification)")
    for pid in skipped:
        lines.append(f"- position {pid}: SKIP (data incomplete) - use the form's 'omit position' option")
    return "\n".join(lines)


def _plain(value) -> str:
    text = f"{Decimal(str(value)):f}"
    return text.rstrip("0").rstrip(".").replace(".", ",") if "." in text else text


class SubmitExplorer(Explorer):
    orchestrator = None
    tan: str | None = None
    state = "not_started"           # prepared | attempted | confirmed | failed | unknown
    receipt: str | None = None
    review_blocks = 0

    @property
    def submitted(self) -> bool:
        return self.state in ("confirmed", "prepared")

    async def _call(self, name: str, args: dict) -> str:
        if name == "get_tan":
            return await self._get_tan()
        if name == "submit_bid":
            return await self._submit(str(args.get("ref", "")))
        return await super()._call(name, args)

    async def _get_tan(self) -> str:
        orch = self.orchestrator
        before = len(orch.bb.items.get("mail_message", []))

        def ours(m: dict) -> bool:                    # a TAN for another tender must never sign this bid
            return not m.get("reference") or _mentions(m.get("reference"), orch.brief.get("tender_reference"))

        def tan_progress() -> tuple[str, bool]:
            found = [i["data"] for i in orch.bb.items.get("mail_message", [])[before:]
                     if (i["data"].get("data") or {}).get("tan") and ours(i["data"])]
            return (f"TAN messages for {orch.brief.get('tender_reference')} extracted since the request: {len(found)}", bool(found))

        mission = Mission("tan", f"""A signing TAN for tender {orch.brief.get('tender_reference')} was just requested and
arrives as a new message in the inbox (no search needed, maybe reload the inbox). Find the NEWEST TAN message
(highest request number / most recent) and extract it with extract_urls kind 'mail_message'.""",
                          {"mail_message": "one opened e-mail message"}, max_steps=10,
                          deadline=min(orch.end - 20, time.time() + 120), progress=tan_progress)
        site = orch.site_for("mail")
        site.fresh_start()
        await site.goto(site.url)
        await Explorer(site, mission, orch.extract).run()
        best, problem = pick_tan([i["data"] for i in orch.bb.items.get("mail_message", [])[before:]],
                                 orch.brief.get("tender_reference"))
        if problem == "foreign":
            log("tan_foreign")
            return (f"ERROR: the new TAN messages belong to other tenders, not {orch.brief.get('tender_reference')}; "
                    "reload the inbox and call get_tan again")
        if best is None:
            return "ERROR: no TAN message found yet; wait a moment and call get_tan again"
        self.tan = str(best["data"]["tan"]).strip()
        log("tan", tan_found=True)
        return f"TAN: {self.tan} (valid for a few minutes; type it into the TAN field now, then submit_bid)"

    async def _read(self, kind: str, text: str) -> dict:
        spec = KINDS[kind]
        return await llm.ask_json(EXTRACT_SYSTEM, f"KIND: {kind} ({spec['describe']})\nReturn JSON with this shape:\n"
                                  f"{spec['schema']}\n<<<PAGE TEXT>>>\n{text[:40000]}\n<<<END>>>", tag=f"submit:{kind}")

    async def _submit(self, ref: str) -> str:
        if self.state in ("confirmed", "prepared", "unknown"):
            return f"ERROR: submission already {self.state}; call finish"
        orch = self.orchestrator
        answer, skipped = orch.result.answer, orch.result.skipped
        page_text = await self.site.full_text()
        review = validate_review(answer, await self._read("bid_review", page_text), skipped)
        log("review_check", blocking=review["blocking"], mismatches=review["mismatches"], missing=review["missing"],
            verified=len(review["verified"]))
        left = orch.end - time.time()
        wrong_reference = any(m.startswith("tender_reference") for m in review["mismatches"])
        # A wrong reference makes the bid ineligible anyway: keep fixing it until the last minute. Other critical
        # mismatches get three fixing rounds while time allows; afterwards a partial bid beats no bid.
        if review["blocking"] and ((wrong_reference and left > 60) or (self.review_blocks < 3 and left > 150)):
            self.review_blocks += 1
            return ("ERROR: the review page does not match our bid; fix before signing (use the form's back/edit "
                    "functions, keep the TAN step for last):\n- " + "\n- ".join(review["mismatches"] + review["missing"]))
        if os.environ.get("RFQ_DRY_RUN") == "1":
            self.state, self.stop = "prepared", True
            log("dry_run_stop", review=review, page=page_text[:2000])
            return "DRY RUN: everything is ready; not submitted. Call finish."
        self.site.accept_next_dialog = True
        try:
            result = await self.site.act("click", {"ref": ref})
        finally:
            await asyncio.sleep(0.5)
            self.site.accept_next_dialog = False     # never leave the next dialog pre-approved
        if result.startswith("ERROR"):
            self.state = "failed"
            log("submit_click_failed", result=result)
            return f"{result}. Nothing was submitted; fix it and call submit_bid again."
        self.state = "attempted"
        outcome = await self._outcome()
        if outcome.get("confirmed"):
            self.state, self.receipt, self.stop = "confirmed", outcome.get("receipt"), True
            log("submitted", state=self.state, receipt=self.receipt)
            return f"CONFIRMED: the website confirmed the bid (receipt {self.receipt}). Call finish."
        if outcome.get("errors"):
            self.state = "failed"
            log("submit_rejected", errors=outcome.get("errors"))
            return "ERROR: the website did not accept the bid: " + "; ".join(map(str, outcome["errors"])) + \
                   ". Fix it and call submit_bid again."
        self.state, self.stop = "unknown", True
        log("submit_unknown", outcome=outcome)
        return "The outcome is unclear; not retrying to avoid a double submission. Call finish."

    async def _outcome(self) -> dict:
        """Read the page after the final click (twice if the first look is inconclusive)."""
        for attempt in range(2):
            try:
                await self.site.act("wait_for", {"condition": "change", "timeout_s": 4})
                text = await self.site.observe(limit=6000)
            except Exception as error:     # the run may end right after acceptance (logins stop working)
                log("submit_observe_failed", error=str(error)[:200])
                return {}
            outcome = await self._read("submission_result", text)
            log("submit_outcome", attempt=attempt + 1, outcome=outcome, page=text[:1500])
            if outcome.get("confirmed") or outcome.get("errors"):
                return outcome
        return outcome


def _mentions(text, reference) -> bool:
    """'Ausschreibung VG-1' mentions VG-1; 'VG-11' does not."""
    t, r = parse.clean(text).upper(), parse.clean(reference).upper()
    return bool(r) and re.search(rf"(?<![A-Z0-9]){re.escape(r)}(?![A-Z0-9])", t) is not None


def pick_tan(messages: list[dict], reference) -> tuple[dict | None, str]:
    """The newest TAN message (highest request number) for our tender. A TAN of another tender never signs this
    bid; a TAN message that names no tender counts as ours. Returns (message, "") or (None, "none" | "foreign")."""
    tans = [m for m in messages if (m.get("data") or {}).get("tan")]
    mine = ([m for m in tans if parse.norm_id(m.get("reference")) == parse.norm_id(reference)]
            or [m for m in tans if _mentions(m.get("reference"), reference)]
            or [m for m in tans if not m.get("reference")])
    if not mine:
        return None, "foreign" if tans else "none"
    return max(mine, key=lambda m: int(re.sub(r"\D", "", str((m.get("data") or {}).get("request_number") or 0)) or 0)), ""


async def submit_bid(orch, result) -> None:
    orch.result = result
    brief = orch.brief
    answer = result.answer
    mail_identity = ""
    for item in orch.bb.items.get("mail_list", []):
        if item["data"].get("mailbox_address"):
            mail_identity = item["data"]["mailbox_address"]
    goal = f"""Submit our bid for tender {answer['tender_reference']} on this website (bid area, e.g. "Angebotsabgabe").
Submission instructions from the brief: {brief.get('submission_steps')}
Bidder: company "{brief.get('bidder_company')}", VAT id "{brief.get('bidder_vat_id')}"{f', contact e-mail "{mail_identity}"' if mail_identity else ''}.

THE BID (enter exactly these values; German number format as shown):
{answer_table(answer, result.skipped, result.breakdown)}

Procedure and traps:
- If participation/interest must be registered first, do it. Each form step is saved only with "Weiter"/next;
  going back clears later steps. If the form is already in a later step, check the values and go back only if needed.
- Offer type: decision 'bid' = main offer (Hauptangebot), NOT an alternative/secondary offer (Nebenangebot, may be
  preselected!). Decision 'no_bid' = declaration of non-participation (Verzichtserklärung): it carries NO positions;
  if the form still lists position rows (maybe with prefilled quantities), mark every one as omitted / remove it.
- Positions may come in any order: read the position number on the page and use THAT position's line of the table.
  The product filters of a position depend on each other (the next one stays disabled until the choice is applied):
  set ALL of them in ONE fill_fields step, one field per filter in page order, each with "apply": <ref of the form's
  apply button, e.g. "Auswahl übernehmen">. If that step stops, continue from where it stopped. When our catalogue
  number is selectable, use ONE fill_fields for catalogue number, quantity, unit price, the price breakdown fields
  (base, surcharges, packaging, project bonus; they may sit in a collapsed section) and the lead time, with
  then_click on the button that saves the position / goes to the next one.
- Never use "add position" (extra positions cost points). Use "omit position" only for positions marked SKIP.
- Discount and NET total: type our values; ignore any displayed gross amount incl. VAT or other suggested totals.
- Declarations: acknowledge the tender documents; do NOT accept/declare your own terms and conditions (AGB).
- On the final review page compare everything with the table above. Then click the button that requests the TAN,
  call get_tan, type the TAN, and call submit_bid with the ref of the final binding submit button. submit_bid checks
  the review page against our bid and only reports success when the website confirms the receipt.
- If the page reports validation errors, fix them and continue."""
    mission = Mission("submit", goal, {}, max_steps=170, deadline=orch.end - 5, max_blocked=10_000, max_visual=2)
    site = orch.site_for("tender")
    site.fresh_start()
    if not await site.responsive():          # never start the decisive phase on a hung tab
        await site.recover_tab("before the submission")
    explorer = SubmitExplorer(site, mission, orch.extract)
    explorer.orchestrator = orch
    explorer.mission.kinds = {}
    started = time.time()
    await explorer.run(SUBMIT_TOOLS)
    log("submit_phase_done", seconds=round(time.time() - started, 1), state=explorer.state, receipt=explorer.receipt,
        mission_status=explorer.status)

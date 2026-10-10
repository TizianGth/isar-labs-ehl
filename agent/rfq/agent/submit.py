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

from .. import flags, llm, metrics
from ..log import log
from .explorer import TOOLS, Explorer, Mission, _tool
from .prompts import EXTRACT_SYSTEM, KINDS
from .review import validate_review

SUBMIT_TOOLS = [t for t in TOOLS if t["function"]["name"] not in
                ("read_all_pages", "read_collection", "extract", "extract_urls", "read_document")] + [
    _tool("get_tan", "AFTER you clicked the website's button that requests/sends the signing TAN: fetch the newest "
          "TAN from the bidder's mailbox. Returns the TAN.", {}, []),
    _tool("steps", "Several form actions in ONE step, done in order (e.g. all product filters of a position, each "
          "with its apply button, then the position's fields and lead time). Refs are from the CURRENT observation; "
          "after each action the page may re-render, and later refs are matched to the same controls again. Stops "
          "at the first error and reports what was done.",
          {"steps": {"type": "array", "items": {"type": "object", "properties": {
              "action": {"type": "string", "enum": ["select", "type", "check", "click", "fill_fields"]},
              "ref": {"type": "string"}, "option": {"type": "string", "description": "select: option text"},
              "text": {"type": "string", "description": "type: the text"},
              "checked": {"type": "boolean"}, "then_click": {"type": "string"},
              "fields": {"type": "array", "description": "fill_fields: [{ref, value}]", "items": {"type": "object",
                         "properties": {"ref": {"type": "string"}, "value": {"type": "string"}}}}},
              "required": ["action"]}}}, ["steps"]),
    _tool("submit_bid", "Final binding submission: clicks the given button and confirms the browser dialog. Only on "
          "the final review/sign page with the TAN entered.", {"ref": {"type": "string"}}, ["ref"]),
]


STEPS_GUIDANCE = """  Do a whole position with ONE `steps` call: each product filter as a select with then_click=<the form's apply
  button, e.g. "Auswahl übernehmen"> (the next filter stays disabled until the choice is applied), then a fill_fields
  for catalogue number, quantity, unit price, the price breakdown fields (base, surcharges, packaging, project
  bonus; they may sit in a collapsed section) and the lead time, and finally the click that goes to the next step.
  If `steps` stops early, look at the page and continue from where it stopped."""
SINGLE_GUIDANCE = """  Choose the product filters of that position one at a time with select(..., then_click=<the form's apply button,
  e.g. "Auswahl übernehmen">) — the next filter stays disabled until the choice is applied. When our catalogue number
  is selectable, use fill_fields once for catalogue number, quantity, unit price, the price breakdown fields (base,
  surcharges, packaging, project bonus; they may sit in a collapsed section) and the lead time; then go to the next step."""


def submit_tools() -> list[dict]:
    """The submission's tools; without `steps` when that switch is off."""
    return [t for t in SUBMIT_TOOLS if flags.STEPS_TOOL or t["function"]["name"] != "steps"]


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

    review_prefetch: tuple[str, asyncio.Task] | None = None

    async def _get_tan(self) -> str:
        orch = self.orchestrator
        metrics.mark("tan_requested")
        # The page that requested the TAN is the review page: read it while the TAN arrives (off the critical path).
        # submit_bid uses this reading only if the page is unchanged apart from the TAN typed into it.
        if self.review_prefetch is not None and not self.review_prefetch[1].done():
            self.review_prefetch[1].cancel()
        if flags.REVIEW_PREFETCH:
            text = await self.site.full_text()
            reading = asyncio.ensure_future(self._read("bid_review", text))
            reading.add_done_callback(lambda t: t.cancelled() or t.exception())   # an unused failure is no warning
            self.review_prefetch = (text, reading)
        before = len(orch.bb.items.get("mail_message", []))

        def tan_progress() -> tuple[str, bool]:
            found = [i["data"] for i in orch.bb.items.get("mail_message", [])[before:] if (i["data"].get("data") or {}).get("tan")]
            return (f"TAN messages extracted since the request: {len(found)}", bool(found))

        mission = Mission("tan", f"""A signing TAN for tender {orch.brief.get('tender_reference')} was just requested and
arrives as a new message in the inbox (no search needed, maybe reload the inbox). Find the NEWEST TAN message
(highest request number / most recent) and extract it with extract_urls kind 'mail_message'.""",
                          {"mail_message": "one opened e-mail message"}, max_steps=10,
                          deadline=min(orch.end - 20, time.time() + 120), progress=tan_progress)
        site = orch.site_for("mail")
        site.fresh_start()
        await site.goto(site.url)
        await Explorer(site, mission, orch.extract).run()
        tans = [i["data"] for i in orch.bb.items.get("mail_message", [])[before:]
                if (i["data"].get("data") or {}).get("tan")]
        if not tans:
            return "ERROR: no TAN message found yet; wait a moment and call get_tan again"
        best = max(tans, key=lambda m: int(re.sub(r"\D", "", str((m.get("data") or {}).get("request_number") or 0)) or 0))
        self.tan = str(best["data"]["tan"]).strip()
        metrics.mark("tan_received")
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
        review = validate_review(answer, await self._review_reading(page_text), skipped)
        log("review_check", blocking=review["blocking"], mismatches=review["mismatches"], missing=review["missing"],
            verified=len(review["verified"]))
        if review["blocking"] and self.review_blocks < 2 and orch.end - time.time() > 150:
            self.review_blocks += 1
            return ("ERROR: the review page does not match our bid; fix before signing (use the form's back/edit "
                    "functions, keep the TAN step for last):\n- " + "\n- ".join(review["mismatches"] + review["missing"]))
        if os.environ.get("RFQ_DRY_RUN") == "1":
            self.state, self.stop = "prepared", True
            log("dry_run_stop", review=review, page=page_text[:2000])
            return "DRY RUN: everything is ready; not submitted. Call finish."
        self.site.accept_next_dialog = True
        metrics.mark("submit_click")
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
            metrics.mark("submit_confirmed")
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

    async def _review_reading(self, page_text: str) -> dict:
        """The model's reading of the review page: the one made while the TAN arrived if the page has not changed
        since (except for the TAN now typed in), otherwise a fresh one."""
        prefetch, self.review_prefetch = self.review_prefetch, None
        if prefetch is not None:
            text, task = prefetch
            if not task.cancelled() and _unchanged(text, page_text, self.tan):
                try:
                    data = await task
                    if data:
                        log("review_prefetch", used=True)
                        return data
                except Exception as error:
                    log("review_prefetch", used=False, error=f"{type(error).__name__}: {error}"[:200])
            else:
                task.cancel()
                log("review_prefetch", used=False, reason="page changed")
        return await self._read("bid_review", page_text)

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


def _unchanged(before: str, after: str, tan: str | None) -> bool:
    """Same page content, ignoring refs (renumbered on every observation) and the TAN typed in meanwhile."""
    def norm(text: str) -> str:
        text = re.sub(r"\[(?:f\d+)?e\d+\]", "", text)
        if tan:
            text = text.replace(tan, "")
        return " ".join(text.split())
    return norm(before) == norm(after)


async def submit_bid(orch, result) -> None:
    metrics.mark("submit_start")
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
  preselected!). Decision 'no_bid' = declaration of non-participation (Verzichtserklärung).
- Positions may come in any order: read the position number on the page and use THAT position's line of the table.
{STEPS_GUIDANCE if flags.STEPS_TOOL else SINGLE_GUIDANCE}
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
    await explorer.run(submit_tools())
    log("submit_phase_done", seconds=round(time.time() - started, 1), state=explorer.state, receipt=explorer.receipt,
        mission_status=explorer.status)

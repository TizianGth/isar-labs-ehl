"""Submission: the model fills the bid form on the website with the computed answer, signs with the TAN from the
mailbox and confirms the browser dialog. Code checks the summary against the answer before the final click.
Set RFQ_DRY_RUN=1 to stop before the binding submission (local tests).
"""
import os
import re
import time
from decimal import Decimal

from .. import llm
from ..log import log
from .explorer import TOOLS, Explorer, Mission, _tool

SUBMIT_TOOLS = [t for t in TOOLS if t["function"]["name"] not in ("read_all_pages", "extract", "extract_urls")] + [
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
    submitted = False

    async def _call(self, name: str, args: dict) -> str:
        if name == "get_tan":
            return await self._get_tan()
        if name == "submit_bid":
            return await self._submit(str(args.get("ref", "")))
        return await super()._call(name, args)

    async def _get_tan(self) -> str:
        orch = self.orchestrator
        before = len(orch.bb.items.get("mail_message", []))
        mission = Mission("tan", f"""A signing TAN for tender {orch.brief.get('tender_reference')} was just requested and
arrives as a new message in the inbox (no search needed, maybe reload the inbox). Find the NEWEST TAN message
(highest request number / most recent) and extract it with extract_urls kind 'mail_message'. Then finish.""",
                          {"mail_message": "one opened e-mail message"}, max_steps=10, deadline=orch.end - 20)
        site = orch.site_for("mail")
        await site.goto(site.url)
        await Explorer(site, mission, orch.extract).run()
        tans = [i["data"] for i in orch.bb.items.get("mail_message", [])[before:]
                if (i["data"].get("data") or {}).get("tan")]
        if not tans:
            return "ERROR: no TAN message found yet; wait a moment and call get_tan again"
        best = max(tans, key=lambda m: int(re.sub(r"\D", "", str((m.get("data") or {}).get("request_number") or 0)) or 0))
        self.tan = str(best["data"]["tan"]).strip()
        log("tan", tan_found=True)
        return f"TAN: {self.tan} (valid for a few minutes; type it into the TAN field now, then submit_bid)"

    async def _submit(self, ref: str) -> str:
        if self.submitted:
            return "ERROR: already submitted"
        answer = self.orchestrator.result.answer
        page_text = await self.site.full_text()
        problems = []
        if answer["tender_reference"] not in page_text:
            problems.append(f"the page does not show the reference {answer['tender_reference']}")
        if answer["decision"] == "bid" and de(answer["total_net"]) not in page_text:
            problems.append(f"the page does not show the net total {de(answer['total_net'])}")
        if problems:
            return "ERROR: check before submitting failed: " + "; ".join(problems) + ". Go to the review/sign page or fix the values."
        if os.environ.get("RFQ_DRY_RUN") == "1":
            self.submitted = self.stop = True
            log("dry_run_stop", page=page_text[:3000])
            return "DRY RUN: everything is ready; not submitted. Call finish."
        self.site.accept_next_dialog = True
        result = await self.site.act("click", {"ref": ref})
        self.submitted = True
        self.stop = True
        after = await self.site.observe()
        log("submitted", result=result, page=after[:2000])
        return f"{result}. Page now:\n{after[:1500]}"


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
  preselected!). Decision 'no_bid' = declaration of non-participation (Verzichtserklärung).
- Positions may come in any order: read the position number on the page and use THAT position's line of the table.
  Choose the product filters of that position one at a time; after each choice press the form's apply button
  (e.g. "Auswahl übernehmen") — the next filter stays disabled until then. When our catalogue number is selectable,
  use fill_fields once for catalogue number, quantity, unit price, the price breakdown fields (base, surcharges,
  packaging, project bonus; they may sit in a collapsed section) and the lead time; then go to the next step.
- Never use "add position" (extra positions cost points). Use "omit position" only for positions marked SKIP.
- Discount and NET total: type our values; ignore any displayed gross amount incl. VAT or other suggested totals.
- Declarations: acknowledge the tender documents; do NOT accept/declare your own terms and conditions (AGB).
- On the final review page compare everything with the table above. Then click the button that requests the TAN,
  call get_tan, type the TAN, and call submit_bid with the ref of the final binding submit button.
- If the page reports validation errors, fix them and continue."""
    mission = Mission("submit", goal, {}, max_steps=110, deadline=orch.end - 5, max_blocked=10_000)
    site = orch.site_for("tender")
    explorer = SubmitExplorer(site, mission, orch.extract)
    explorer.orchestrator = orch
    explorer.mission.kinds = {}
    started = time.time()
    await explorer.run(SUBMIT_TOOLS)
    log("submit_phase_done", seconds=round(time.time() - started, 1), submitted=explorer.submitted)

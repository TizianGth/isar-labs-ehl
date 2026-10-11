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
from ..log import log, register_secret
from ..profile import span
from ..rules import parse
from .explorer import TOOLS, Explorer, Mission, _tool
from .fused import FusedExplorer
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


class SubmitExplorer(FusedExplorer):
    orchestrator = None
    tan: str | None = None
    state = "not_started"           # prepared | attempted | confirmed | failed | unknown
    receipt: str | None = None
    review_blocks = 0

    @property
    def submitted(self) -> bool:
        return self.state in ("confirmed", "prepared")

    def values(self):
        orch = self.orchestrator
        identity = next((i['data'].get('mailbox_address') for i in orch.bb.get('mail_list')
                         if i['data'].get('mailbox_address')), '')
        return {'answer': orch.result.answer,
                'positions': {p['position']: p for p in orch.result.answer['positions']},
                'filters': {pid: values.get('filters', {}) for pid, values in orch.result.breakdown.items()},
                'bidder': {'company': orch.brief.get('bidder_company'), 'vat_id': orch.brief.get('bidder_vat_id'), 'email': identity},
                'signing': {'tan': self.tan}}

    def decision_context(self):
        values = self.values()
        visible = self.snapshot.text if self.snapshot else ''
        selected = {pid: row for pid, row in values['positions'].items() if _mentions(visible, pid)}
        if not selected:
            selected = values['positions']
        summary = {k: v for k, v in values['answer'].items() if k != 'positions'}
        # Value handles replace repeated model copying; the model still chooses
        # the field meaning and the position, including filters and declarations.
        return ('BINDABLE DATA (JSON pointer paths; bind_fields does value copying and readback):\n' +
                __import__('json').dumps({'answer': summary, 'positions': selected,
                                        'filters': {pid: values['filters'].get(pid, {}) for pid in selected},
                                        'bidder': values['bidder'], 'signing': {'tan': 'available' if self.tan else 'request then get_tan'}},
                                       ensure_ascii=False, default=str) +
                '\nFormats de=decimal comma, en=decimal dot, integer=whole number, text=exact string. '
                'Use /positions/<exact position>/unit_price, /filters/<position>/<filter>, /answer/total_net, '
                '/bidder/company, /signing/tan. Bind one observed control per field. Use apply only for a dependent filter.')

    async def bind_fields(self, args):
        fields = []
        for binding in args.get('fields') or []:
            path = binding.get('path', '')
            if not isinstance(path, str) or not path.startswith('/'):
                return 'ERROR: binding path must be an absolute JSON pointer'
            value = self.values()
            try:
                for component in path[1:].split('/'):
                    component = component.replace('~1', '/').replace('~0', '~')
                    value = value[int(component)] if isinstance(value, list) else value[component]
            except (KeyError, IndexError, ValueError, TypeError):
                return 'ERROR: unknown bid data path ' + path
            if value is None or isinstance(value, (dict, list)):
                return 'ERROR: selected value is unavailable or not scalar'
            fmt = binding.get('format', 'text')
            if fmt not in ('text', 'de', 'en', 'integer'):
                return 'ERROR: unsupported field format'
            try:
                if fmt == 'de':
                    value = de(value)
                elif fmt == 'en':
                    value = f'{Decimal(str(value)):f}'
                elif fmt == 'integer':
                    number = Decimal(str(value))
                    if number != number.to_integral_value():
                        return 'ERROR: integer binding would discard fractional data'
                    value = str(int(number))
            except Exception:
                return 'ERROR: value is not numeric in the chosen format'
            if path == '/signing/tan':
                register_secret(value)
            field = {'ref': binding.get('ref'), 'value': str(value)}
            if binding.get('apply'):
                field['apply'] = binding['apply']
            if hasattr(self.site, 'field_identity'):
                field['identity'] = await self.site.field_identity(field['ref'])
            fields.append(field)
        with span('submission_bindings', agent='submit', category='browser', count=len(fields)):
            result = await self.site.act('fill_fields', {'fields': fields, 'then_click': args.get('then_click')})
        return result

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
            return parse.norm_id(m.get('reference')) == parse.norm_id(orch.brief.get('tender_reference'))

        def tan_progress() -> tuple[str, bool]:
            found = [i["data"] for i in orch.bb.items.get("mail_message", [])[before:]
                     if (i["data"].get("data") or {}).get("tan") and ours(i["data"])]
            return (f"TAN messages for {orch.brief.get('tender_reference')} extracted since the request: {len(found)}", bool(found))

        mission = Mission("tan", f"""A signing TAN for tender {orch.brief.get('tender_reference')} was just requested and
arrives as a new message. Read the relevant signing-message list completely, including all pages; do not finish
on the first TAN newly extracted into memory. Extract ALL exact-reference TAN candidates with extract_urls kind mail_message.
Compare dated messages and request numbers to identify the newest actual signing request. Finish only after that coverage.""",
                          {"mail_message": "one opened e-mail message"}, max_steps=10,
                          deadline=min(orch.end - 20, time.time() + 120))
        site = orch.site_for("mail")
        await site.wait_started()
        site.fresh_start()
        await site.goto(site.url)
        tan_explorer = await FusedExplorer(site, mission, orch.extract).run()
        if tan_explorer.status not in ("finished", "complete"):
            return "ERROR: signing-message coverage is incomplete; inspect current messages before using a TAN"
        best, problem = pick_tan([i["data"] for i in orch.bb.items.get("mail_message", [])[before:]],
                                 orch.brief.get("tender_reference"))
        if problem == "foreign":
            log("tan_foreign")
            return (f"ERROR: the new TAN messages belong to other tenders, not {orch.brief.get('tender_reference')}; "
                    "reload the inbox and call get_tan again")
        if best is None:
            return "ERROR: no TAN message found yet; wait a moment and call get_tan again"
        self.tan = str(best["data"]["tan"]).strip()
        register_secret(self.tan)
        log("tan", tan_found=True)
        return "TAN available through /signing/tan; bind it to the observed TAN field, then submit_bid."

    async def _read(self, kind: str, text: str) -> dict:
        spec = KINDS[kind]
        return await llm.ask_json(EXTRACT_SYSTEM, f"KIND: {kind} ({spec['describe']})\nReturn JSON with this shape:\n"
                                  f"{spec['schema']}\n<<<PAGE TEXT>>>\n{text}\n<<<END>>>", tag=f"submit:{kind}", deadline=self.orchestrator.end - 5, max_tokens=4096)

    async def _submit(self, ref: str) -> str:
        if self.state in ("confirmed", "prepared", "unknown"):
            return f"ERROR: submission already {self.state}; call finish"
        orch = self.orchestrator
        answer, skipped = orch.result.answer, orch.result.skipped
        page_text = await self.site.full_text()
        with span('submission_review', agent='submit', category='model'):
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
        if result.startswith("NOT_DISPATCHED:"):
            return "ERROR: " + result + "; observe and choose the current submit control"
        self.state = "attempted"
        # Even a click timeout may have applied a write. Inspect its outcome
        # before any retry; a missing receipt is never proof of non-submission.
        with span('submission_receipt', agent='submit', category='signing'):
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
                text = await self.site.full_text()
            except Exception as error:     # the run may end right after acceptance (logins stop working)
                log("submit_observe_failed", error=str(error)[:200])
                return {}
            outcome = await self._read("submission_result", text)
            log("submit_outcome", attempt=attempt + 1, outcome=outcome, page=text[:1500])
            if outcome.get("confirmed") or outcome.get("errors"):
                return outcome
            await asyncio.sleep(.4)
        return outcome


def _mentions(text, reference) -> bool:
    """'Ausschreibung VG-1' mentions VG-1; 'VG-11' does not."""
    t, r = parse.clean(text).upper(), parse.clean(reference).upper()
    return bool(r) and re.search(rf"(?<![A-Z0-9]){re.escape(r)}(?![A-Z0-9])", t) is not None


def pick_tan(messages: list[dict], reference) -> tuple[dict | None, str]:
    """The newest TAN message (highest request number) for our exact tender. A message without a correlated
    tender reference does not authorize signing. Returns (message, "") or (None, "none" | "foreign")."""
    tans = [m for m in messages if (m.get("data") or {}).get("tan")]
    mine = ([m for m in tans if parse.norm_id(m.get("reference")) == parse.norm_id(reference)]
            )
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

Use bind_fields to copy the computed data into model-selected fields. Skipped positions: {result.skipped}.
The per-page bindable data contains exact values and filters; do not recalculate prices.

Procedure and traps:
- If participation/interest must be registered first, do it. Each form step is saved only with "Weiter"/next;
  going back clears later steps. If the form is already in a later step, check the values and go back only if needed.
- Offer type: decision 'bid' = main offer (Hauptangebot), NOT an alternative/secondary offer (Nebenangebot, may be
  preselected!). Decision 'no_bid' = declaration of non-participation (Verzichtserklärung): it carries NO positions;
  if the form still lists position rows (maybe with prefilled quantities), mark every one as omitted / remove it.
- Positions may come in any order: read the position number on the page and use THAT position's data path.
  The product filters of a position depend on each other (the next one stays disabled until the choice is applied):
  set ALL of them in ONE fill_fields step, one field per filter in page order, each with "apply": <ref of the form's
  apply button, e.g. "Auswahl übernehmen">. If that step stops, continue from where it stopped. When our catalogue
  number is selectable, use ONE fill_fields for catalogue number, quantity, unit price, the price breakdown fields
  (base, surcharges, packaging, project bonus; they may sit in a collapsed section) and the lead time, with
  then_click on the button that saves the position / goes to the next one.
- Never use "add position" (extra positions cost points). Use "omit position" only for positions marked SKIP.
- Discount and NET total: type our values; ignore any displayed gross amount incl. VAT or other suggested totals.
- Declarations: acknowledge the tender documents; do NOT accept/declare your own terms and conditions (AGB).
- On the final review page compare everything with the computed answer. Then click the button that requests the TAN,
  call get_tan, bind /signing/tan, and call submit_bid with the ref of the final binding submit button. submit_bid checks
  the review page against our bid and only reports success when the website confirms the receipt.
- If the page reports validation errors, fix them and continue."""
    mission = Mission("submit", goal, {}, max_steps=170, deadline=orch.end - 5, max_blocked=10_000, max_visual=2)
    site = orch.site_for("tender")
    await site.wait_started()
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

"""The binding submission on other websites: a confirmation asked for on the page (instead of a browser dialog) is
confirmed exactly once, never by starting a second submission (synthetic data only)."""
import asyncio
import time
from types import SimpleNamespace

from rfq.agent import submit
from rfq.agent.explorer import Mission
from rfq.agent.submit import SubmitExplorer


class _Site:
    site_id, url = "portal", "http://portal.test"

    def __init__(self):
        self.clicks, self.accept_next_dialog = [], False
        self.page = SimpleNamespace(url="http://portal.test/review")

    async def act(self, name, args):
        if name == "click":
            self.clicks.append(args["ref"])
            return "clicked"
        return "the page changed"

    async def full_text(self):
        return "review page"

    async def observe(self, limit=0):
        return "page after the click"


def _explorer(monkeypatch, outcomes: list[dict]):
    reviews = []
    monkeypatch.setattr(submit, "validate_review", lambda answer, page, skipped: reviews.append(1) or
                        {"blocking": False, "mismatches": [], "missing": [], "verified": []})
    explorer = SubmitExplorer(_Site(), Mission("submit", "goal", {}), None)
    explorer.orchestrator = SimpleNamespace(result=SimpleNamespace(answer={}, skipped=[]), end=time.time() + 600)

    async def read(kind, text):
        return outcomes.pop(0) if kind == "submission_result" else {}
    explorer._read = read
    return explorer, reviews


def test_a_confirmation_asked_on_the_page_is_confirmed_with_its_button(monkeypatch):
    explorer, _ = _explorer(monkeypatch, [{"confirmed": False, "errors": [], "needs_confirmation": True, "confirm_ref": "[e9]"},
                                          {"confirmed": True, "receipt": "Q-1", "errors": []}])
    message = asyncio.run(explorer._submit("e50"))
    assert explorer.site.clicks == ["e50", "e9"] and explorer.state == "confirmed" and "CONFIRMED" in message


def test_without_a_recognised_button_the_model_confirms_and_the_bid_is_not_sent_twice(monkeypatch):
    explorer, reviews = _explorer(monkeypatch, [{"confirmed": False, "errors": [], "needs_confirmation": True},
                                                {"confirmed": True, "receipt": "Q-2", "errors": []}])
    first = asyncio.run(explorer._submit("e50"))
    assert explorer.state == "awaiting_confirmation" and "confirm button" in first and not explorer.stop
    second = asyncio.run(explorer._submit("e61"))                     # the model presses the page's confirm button
    assert explorer.site.clicks == ["e50", "e61"] and explorer.state == "confirmed" and "CONFIRMED" in second
    assert len(reviews) == 1                                          # the review check ran once, before sending


def test_a_browser_dialog_site_still_needs_one_click(monkeypatch):
    explorer, _ = _explorer(monkeypatch, [{"confirmed": True, "receipt": "Q-3", "errors": []}])
    asyncio.run(explorer._submit("e50"))
    assert explorer.site.clicks == ["e50"] and explorer.state == "confirmed"


def test_the_bid_table_gives_both_number_formats():
    """#6: another configurator may expect '2468.13' instead of '2.468,13': the table offers both."""
    answer = {"tender_reference": "XT-1", "decision": "bid", "discount_percent": 5.5, "total_net": 12345.6,
              "positions": [{"position": "1", "material_number": "M-1", "quantity": 1250, "unit_price": 2468.13,
                             "base_unit_price": 2000.0, "surcharge_per_unit": 1.5, "packaging_per_unit": 0.0,
                             "rebate_total": 0.0, "lead_time_weeks": 3}]}
    table = submit.answer_table(answer, [], {})
    assert "2.468,13 [2468.13]" in table and "12.345,60 [12345.60]" in table
    assert "5,5 or 5.5" in table and "quantity 1250 pieces" in table

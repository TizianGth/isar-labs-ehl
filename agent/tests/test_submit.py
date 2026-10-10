"""Submission safety: the right TAN, and no signature on a review page that contradicts our bid."""
import asyncio
import time
from types import SimpleNamespace

from rfq.agent.submit import SubmitExplorer, pick_tan


def _tan(number, tan, reference=None):
    return {"reference": reference, "data": {"tan": tan, "request_number": number}}


def test_tan_of_another_tender_never_signs_our_bid():
    best, problem = pick_tan([_tan("REQ-7", "111111", "XT-1"), _tan("REQ-9", "999999", "XT-2")], "XT-1")
    assert best["data"]["tan"] == "111111" and not problem                # higher number, but another tender
    assert pick_tan([_tan("REQ-9", "999999", "XT-2")], "XT-1") == (None, "foreign")
    assert pick_tan([], "XT-1") == (None, "none")
    best, _ = pick_tan([_tan("REQ-2", "222222"), _tan("REQ-3", "333333")], "XT-1")   # no reference named: ours
    assert best["data"]["tan"] == "333333"
    best, _ = pick_tan([_tan("REQ-4", "444444", "Ausschreibung XT-1"), _tan("REQ-5", "555555", "XT-11")], "XT-1")
    assert best["data"]["tan"] == "444444"


def _explorer(observed: dict, seconds_left: float):
    ex = SubmitExplorer.__new__(SubmitExplorer)
    ex.state, ex.review_blocks = "not_started", 0
    answer = {"tender_reference": "XT-1", "decision": "bid", "discount_percent": 0, "total_net": 100,
              "positions": [{"position": "1", "material_number": "M-1", "quantity": 1, "unit_price": 100}]}
    ex.orchestrator = SimpleNamespace(result=SimpleNamespace(answer=answer, skipped=[]), end=time.time() + seconds_left)

    async def full_text():
        return "review page"

    async def read(kind, text):
        return observed
    ex.site, ex._read = SimpleNamespace(full_text=full_text), read
    return ex


GOOD = {"tender_reference": "XT-1", "offer_type": "main", "discount_percent": "0", "total_net": "100,00",
        "positions": [{"position": "1", "material_number": "M-1", "quantity": "1", "unit_price": "100,00"}]}


def test_a_wrong_reference_is_not_signed_while_time_allows(monkeypatch):
    monkeypatch.setenv("RFQ_DRY_RUN", "1")
    ex = _explorer({**GOOD, "tender_reference": "XT-2"}, seconds_left=400)
    for _ in range(5):
        assert asyncio.run(ex._submit("e9")).startswith("ERROR: the review page does not match")
    ex.orchestrator.end = time.time() + 30                                   # the last minute: a bid beats none
    assert asyncio.run(ex._submit("e9")).startswith("DRY RUN")


def test_other_critical_mismatches_get_three_fixing_rounds(monkeypatch):
    monkeypatch.setenv("RFQ_DRY_RUN", "1")
    wrong = {**GOOD, "positions": [{"position": "1", "material_number": "", "quantity": "1", "unit_price": "100,00"}]}
    ex = _explorer(wrong, seconds_left=400)
    for _ in range(3):
        assert asyncio.run(ex._submit("e9")).startswith("ERROR")
    assert asyncio.run(ex._submit("e9")).startswith("DRY RUN")              # partial bid instead of none

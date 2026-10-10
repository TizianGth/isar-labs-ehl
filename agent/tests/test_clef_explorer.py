"""Experimental routing cannot act on stale pages or signing/form screens."""
import asyncio
import time
from types import SimpleNamespace

from rfq import clef
from rfq.agent.explorer import Explorer, Mission


PAGE = 'URL: https://fixture.invalid/\n[e1] link "Current requirements" -> /opaque/source'


def explorer(page=PAGE, mission="specification"):
    async def observe(): return page
    return Explorer(SimpleNamespace(observe=observe),
                    Mission(mission, "Read current requirements", {}, deadline=time.time() + 30), None)


def test_valid_choice_requires_qwen_for_followup(monkeypatch):
    monkeypatch.setenv("RFQ_CLEF_NAV", "1")
    calls = []
    async def choose(goal, observation, candidates, **kwargs):
        calls.append(goal)
        return candidates[0]
    monkeypatch.setattr(clef, "choose_navigation", choose)
    async def scenario():
        e = explorer()
        assert await e._navigation_choice(PAGE, "requirements missing") == ("click", {"ref": "e1"})
        assert await e._navigation_choice(PAGE, "requirements missing") is None
        assert len(calls) == 1
    asyncio.run(scenario())


def test_changed_page_rejects_choice(monkeypatch):
    monkeypatch.setenv("RFQ_CLEF_NAV", "1")
    async def choose(goal, observation, candidates, **kwargs): return candidates[0]
    monkeypatch.setattr(clef, "choose_navigation", choose)
    e = explorer(PAGE + '\n[e2] link "New revision" -> /opaque/new')
    assert asyncio.run(e._navigation_choice(PAGE, "requirements missing")) is None


def test_forms_signing_and_errors_never_call_clef(monkeypatch):
    monkeypatch.setenv("RFQ_CLEF_NAV", "1")
    async def forbidden(*args, **kwargs): raise AssertionError("unsafe decision sent to Clef")
    monkeypatch.setattr(clef, "choose_navigation", forbidden)
    for mission in ("submit", "tan", "recheck"):
        assert asyncio.run(explorer(mission=mission)._navigation_choice(PAGE, "")) is None
    assert asyncio.run(explorer()._navigation_choice(PAGE + '\n[e9] input[text] "Query"', "")) is None
    e = explorer()
    e.last_result = "ERROR: previous action failed"
    assert asyncio.run(e._navigation_choice(PAGE, "")) is None

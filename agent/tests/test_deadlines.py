"""B08 + B02: deadlines hold through slots, hung calls and retries; ended missions wake the ERP phase."""
import asyncio
import time

import pytest

from rfq import llm
from rfq.agent import orchestrator as orch_mod
from rfq.agent.orchestrator import Orchestrator


class _HangingCompletions:
    async def create(self, **kwargs):
        await asyncio.sleep(kwargs.get("timeout", 60))      # behaves like a request that never answers in time
        raise TimeoutError("hung")


class _HangingClient:
    chat = type("C", (), {"completions": _HangingCompletions()})()


def test_hung_model_call_respects_the_deadline(monkeypatch):
    monkeypatch.setattr(llm, "client", lambda: _HangingClient())
    started = time.time()
    with pytest.raises(llm.DeadlineExceeded):
        asyncio.run(llm.chat([{"role": "user", "content": "x"}], deadline=time.time() + 6))
    assert time.time() - started < 9


def test_busy_slots_respect_the_deadline(monkeypatch):
    async def scenario():
        slots = asyncio.Semaphore(1)
        monkeypatch.setattr(llm, "_slots", slots)
        await slots.acquire()                                  # another call holds the only slot
        await llm.chat([{"role": "user", "content": "x"}], deadline=time.time() + 4)
    started = time.time()
    with pytest.raises(llm.DeadlineExceeded):
        asyncio.run(scenario())
    assert time.time() - started < 6


def _orchestrator() -> Orchestrator:
    run = {"expires_at": (time.time() + 7200) * 1000, "brief": ""}
    o = Orchestrator(run, {"erp": object()}, started=time.time())
    o.brief = {"tender_reference": "XT-1", "sites": {"erp": "erp"}}
    return o


def test_budget_is_capped_whatever_the_run_allows():
    o = _orchestrator()
    assert o.end - time.time() <= orch_mod.RUN_BUDGET + 1


def test_failed_specification_wakes_erp_without_waiting():
    async def scenario():
        o = _orchestrator()
        o.done["specification"].set()      # e.g. mission_loop_abort: ended without requirements
        o.done["tender"].set()
        return await asyncio.wait_for(o.erp_phase(), timeout=5)
    assert asyncio.run(scenario()) == []


def test_duplicate_catalogue_groups_start_one_mission(monkeypatch):
    calls = []

    async def fake_run_mission(self, site, mission):
        calls.append(mission.name)
        self.status[mission.name] = "complete"
        return None

    monkeypatch.setattr(Orchestrator, "run_mission", fake_run_mission)

    async def scenario():
        o = _orchestrator()
        o.bb.add("positions", {"positions": [{"position": "1", "requirement_ref": "A-1"},
                                             {"position": "2", "requirement_ref": "A-2"}]}, "u")
        o.bb.add("spec_revisions", {"revisions": [{"revision": "V2", "status_text": "gültig"}]}, "u")
        o.bb.add("requirements", {"revision": "V2", "requirements": [{"ref": "A-1", "catalog_group": "G-7"},
                                                                     {"ref": "A-2", "catalog_group": "g-7 "}]}, "u")
        o.done["specification"].set()
        o.done["tender"].set()
        await asyncio.wait_for(o.erp_phase(), timeout=5)
        return o
    o = asyncio.run(scenario())
    assert calls == ["erp:G-7"] and o.erp_complete == {"G-7"}

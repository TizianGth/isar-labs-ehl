"""Independent recovery sites overlap; aliases never dispatch concurrent writes."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from rfq import llm
from rfq.agent.orchestrator import Orchestrator


@pytest.mark.parametrize('alias', [False, True])
def test_recovery_overlaps_only_independent_sites(monkeypatch, alias):
    async def scenario():
        observing, peak_observing, counts, peak_writes = set(), 0, {}, 0
        async def observe(site):
            nonlocal peak_observing
            assert id(site) not in observing
            observing.add(id(site))
            peak_observing = max(peak_observing, len(observing))
            await asyncio.sleep(.01)
            observing.remove(id(site))
            return 'Synthetic current source register'
        sites = {}
        for role in ('tender', 'specification', 'erp', 'mail'):
            site = SimpleNamespace()
            site.observe = lambda limit, site=site: observe(site)
            sites[role] = site
        if alias:
            sites['specification'] = sites['tender']
        o = Orchestrator({'expires_at': (time.time()+1000)*1000}, sites)
        o.brief = {}
        o.site_for = lambda role: sites[role]
        async def route(*args, **kwargs):
            return {'routes': [{'role': role, 'goal': 'Read selected register'}
                               for role in ('tender', 'specification')]}
        monkeypatch.setattr(llm, 'ask_json', route)
        async def mission(site, mission, attempts):
            nonlocal peak_writes
            counts[id(site)] = counts.get(id(site), 0)+1
            assert counts[id(site)] == 1
            peak_writes = max(peak_writes, sum(counts.values()))
            await asyncio.sleep(.01)
            counts[id(site)] -= 1
            o.evidence_end = 0  # Stop after this routing round.
        o.run_mission = mission
        await o.route_missing_records()
        assert peak_observing == (3 if alias else 4)
        assert peak_writes == (1 if alias else 2)
        assert sum(counts.values()) == 0
    asyncio.run(scenario())


def test_navigation_can_use_all_idle_model_slots(monkeypatch):
    assert llm._interactive._value == llm.PARALLEL
    async def scenario():
        release, full = asyncio.Event(), asyncio.Event()
        entered = 0
        async def create(**kwargs):
            nonlocal entered
            entered += 1
            if entered == llm.PARALLEL:
                full.set()
            await release.wait()
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{}', tool_calls=[]), finish_reason='stop')], usage=None)
        monkeypatch.setattr(llm, '_slots', asyncio.Semaphore(llm.PARALLEL))
        monkeypatch.setattr(llm, '_interactive', asyncio.Semaphore(llm.PARALLEL))
        monkeypatch.setattr(llm, 'MAX_HEDGES', 0)
        monkeypatch.setattr(llm, '_run_deadline', float('inf'))
        monkeypatch.setattr(llm, 'client', lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        tasks = [asyncio.create_task(llm.chat([], tag='erp@synthetic', deadline=time.time()+10))
                 for _ in range(llm.PARALLEL)]
        try:
            await asyncio.wait_for(full.wait(), .5)
            release.set()
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())

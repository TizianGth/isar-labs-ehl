"""Actual proxy capacity, control headroom and usage on hedged requests."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from rfq import llm


def response():
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}", tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=13, completion_tokens=7,
                              prompt_tokens_details=SimpleNamespace(cached_tokens=3)))


@pytest.fixture
def gates(monkeypatch):
    monkeypatch.setattr(llm, "_slots", asyncio.Semaphore(10))
    monkeypatch.setattr(llm, "_extract_slots", asyncio.Semaphore(8))
    monkeypatch.setattr(llm, "_hedging", 0)
    monkeypatch.setattr(llm, "_recent", {True: [], False: []})
    monkeypatch.setattr(llm, "HEDGE_AFTER", {True: 0.01, False: 0.01})
    monkeypatch.setattr(llm, "_run_deadline", float("inf"))
    monkeypatch.setattr(llm, "usage", {key: 0 for key in llm.usage})
    events = []
    monkeypatch.setattr(llm, "log", lambda event, **kwargs: events.append((event, kwargs)))
    return events


def install(monkeypatch, create):
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))


async def wait_until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


async def all_free(gate, count):
    for _ in range(count):
        await asyncio.wait_for(gate.acquire(), 0.1)
    assert gate.locked()
    for _ in range(count):
        gate.release()


def test_ten_actual_requests_include_hedges(monkeypatch, gates):
    running = peak = 0

    async def create(**kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.Event().wait()
        finally:
            running -= 1

    install(monkeypatch, create)

    async def scenario():
        tasks = [asyncio.create_task(llm.chat([], tag="navigate", deadline=time.time() + 60)) for _ in range(10)]
        try:
            await wait_until(lambda: running == 10)
            await wait_until(lambda: llm._hedging == 2)
            await asyncio.sleep(0.02)
            assert running == 10  # twins cannot create request eleven
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert running == 0 and llm._hedging == 0
        await all_free(llm._slots, 10)

    asyncio.run(scenario())
    assert peak == 10 and llm.usage["requests"] == 10
    assert sum(event == "llm_hedge" for event, _ in gates) == 2
    assert sum(data["status"] == "cancelled_waiting" for event, data in gates if event == "llm_request") == 2


def test_eight_extracts_leave_two_requests_for_control(monkeypatch, gates):
    active = {"extract": 0, "control": 0}

    async def create(**kwargs):
        kind = kwargs["messages"][0]["content"]
        active[kind] += 1
        try:
            await asyncio.Event().wait()
        finally:
            active[kind] -= 1

    install(monkeypatch, create)

    async def scenario():
        tasks = [asyncio.create_task(llm.chat([{"content": "extract"}], thinking=True,
                                             tag="extract:synthetic", deadline=time.time() + 60)) for _ in range(12)]
        try:
            await wait_until(lambda: active["extract"] == 8)
            tasks += [asyncio.create_task(llm.chat([{"content": "control"}], thinking=True,
                                                  tag=tag, deadline=time.time() + 60))
                      for tag in ("navigate", "review")]
            await wait_until(lambda: active["control"] == 2)
            assert active == {"extract": 8, "control": 2}
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await all_free(llm._slots, 10)
        await all_free(llm._extract_slots, 8)

    asyncio.run(scenario())
    assert llm.usage["requests"] == 10


def test_sdk_timeout_is_enforced_and_records_retry_timings(monkeypatch, gates):
    cancelled = []

    async def create(**kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    install(monkeypatch, create)

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await llm._request({}, 0.02, tag="extract:synthetic", attempt=4, end=time.time() + 60)
        await all_free(llm._slots, 10)
        await all_free(llm._extract_slots, 8)

    asyncio.run(scenario())
    event, data = gates[-1]
    assert event == "llm_request" and data["retry"] == 4 and data["request_class"] == "extract"
    assert data["request_s"] >= 0.015 and data["slot_wait_s"] >= 0
    assert data["status"] == "error" and not data["usage_available"] and cancelled == [True]


def test_completed_hedge_tokens_are_both_metered(monkeypatch, gates):
    started = 0
    release = asyncio.Event()

    async def create(**kwargs):
        nonlocal started
        started += 1
        await release.wait()
        return response()

    install(monkeypatch, create)

    async def scenario():
        task = asyncio.create_task(llm.chat([], tag="navigate", deadline=time.time() + 60))
        await wait_until(lambda: started == 2)
        release.set()
        await task
        await all_free(llm._slots, 10)

    asyncio.run(scenario())
    assert llm.usage["calls"] == 1 and llm.usage["requests"] == 2
    assert llm.usage["input"] == 26 and llm.usage["output"] == 14 and llm.usage["cached"] == 6
    assert sum(data["status"] == "completed" for event, data in gates if event == "llm_request") == 2


def test_capacity_queue_does_not_start_a_hedge_clock(monkeypatch, gates):
    monkeypatch.setattr(llm, "_slots", asyncio.Semaphore(1))
    called = []

    async def create(**kwargs):
        called.append(True)
        return response()

    install(monkeypatch, create)

    async def scenario():
        await llm._slots.acquire()
        task = asyncio.create_task(llm.chat([], tag="navigate", deadline=time.time() + 60))
        try:
            await asyncio.sleep(0.04)  # longer than the provider's hedge threshold
            assert called == [] and llm._hedging == 0 and llm.usage["hedges"] == 0
        finally:
            llm._slots.release()
        await task
        await all_free(llm._slots, 1)

    asyncio.run(scenario())
    assert called == [True]
    data = next(data for event, data in gates if event == "llm_request")
    assert data["slot_wait_s"] >= 0.03 and data["request_s"] < data["slot_wait_s"]

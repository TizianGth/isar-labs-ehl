"""A stuck model request gets a twin after a while; the first answer wins, nothing is left running."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from rfq import llm


def _client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _response(text="{}"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=[]))], usage=None)


@pytest.fixture
def fast_hedge(monkeypatch):
    monkeypatch.setattr(llm, "MAX_HEDGES", 2)
    monkeypatch.setattr(llm, "HEDGE_AFTER", {True: 0.1, False: 0.1})
    monkeypatch.setattr(llm, "_recent", {True: [], False: []})
    monkeypatch.setattr(llm, "_hedging", 0)
    monkeypatch.setattr(llm, "_slots", asyncio.Semaphore(2))
    monkeypatch.setattr(llm, "_run_deadline", float("inf"))


def _install(monkeypatch, *behaviours):
    """Request i behaves like behaviours[i]: ('hang',), ('answer', delay) or ('fail', delay)."""
    started, cancelled = [], []

    async def create(**kwargs):
        index = len(started)
        started.append(index)
        kind, *rest = behaviours[min(index, len(behaviours) - 1)]
        try:
            if kind == "hang":
                await asyncio.Event().wait()
            await asyncio.sleep(rest[0])
            if kind == "fail":
                raise RuntimeError("503 from the proxy")
            return _response(f'{{"by": {index}}}')
        except asyncio.CancelledError:
            cancelled.append(index)
            raise
    monkeypatch.setattr(llm, "client", lambda: _client(create))
    return started, cancelled


def test_a_stuck_request_is_answered_by_its_twin(monkeypatch, fast_hedge):
    started, cancelled = _install(monkeypatch, ("hang",), ("answer", 0.05))

    async def scenario():
        t0 = time.monotonic()
        reply = await llm.chat([], deadline=time.time() + 60)
        await asyncio.sleep(0.05)
        return reply, time.monotonic() - t0
    reply, seconds = asyncio.run(scenario())
    assert reply["content"] == '{"by": 1}' and seconds < 1.0
    assert started == [0, 1] and cancelled == [0] and llm._hedging == 0


def test_a_normal_request_gets_no_twin(monkeypatch, fast_hedge):
    started, _ = _install(monkeypatch, ("answer", 0.01))
    asyncio.run(llm.chat([], deadline=time.time() + 60))
    assert started == [0]


def test_twins_are_bounded(monkeypatch, fast_hedge):
    monkeypatch.setattr(llm, "_hedging", llm.MAX_HEDGES)       # other twins are already running
    started, _ = _install(monkeypatch, ("answer", 0.3))
    reply = asyncio.run(llm.chat([], deadline=time.time() + 60))
    assert started == [0] and reply["content"] == '{"by": 0}'


def test_the_twin_wins_when_the_first_request_fails_late(monkeypatch, fast_hedge):
    started, _ = _install(monkeypatch, ("fail", 0.2), ("answer", 0.2))
    reply = asyncio.run(llm.chat([], deadline=time.time() + 60))
    assert reply["content"] == '{"by": 1}' and started == [0, 1]


def test_cancelling_a_hedged_call_cancels_both_requests_and_frees_the_slot(monkeypatch, fast_hedge):
    started, cancelled = _install(monkeypatch, ("hang",), ("hang",))

    async def scenario():
        task = asyncio.create_task(llm.chat([], deadline=time.time() + 60))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        await asyncio.wait_for(llm._slots.acquire(), 0.1)      # both slots are free again
        await asyncio.wait_for(llm._slots.acquire(), 0.1)
    asyncio.run(scenario())
    assert sorted(cancelled) == [0, 1] and llm._hedging == 0


def test_thinking_calls_are_not_hedged(monkeypatch, fast_hedge):
    started, _ = _install(monkeypatch, ("answer", 0.3))
    asyncio.run(llm.chat([], thinking=True, deadline=time.time() + 60))
    assert started == [0]


def test_a_short_model_outage_is_waited_out(monkeypatch, fast_hedge):
    """Fake portal legacy/blocked: the model proxy answered 502 for a while and the submission gave up after six
    attempts with minutes left. Service errors are retried while the deadline allows; refused requests are not."""
    class Unavailable(Exception):
        status_code = 502

    class Refused(Exception):
        status_code = 400

    calls = []

    async def create(**kwargs):
        calls.append(1)
        if len(calls) <= 8:
            raise Unavailable("Cannot reach the platform")
        return _response('{"ok": true}')

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(llm, "client", lambda: _client(create))
    monkeypatch.setattr(llm.asyncio, "sleep", no_wait)
    reply = asyncio.run(llm.chat([], deadline=time.time() + 120))
    assert reply["content"] == '{"ok": true}' and len(calls) == 9

    refused = []

    async def create_refused(**kwargs):
        refused.append(1)
        raise Refused("context too long")

    monkeypatch.setattr(llm, "client", lambda: _client(create_refused))
    with pytest.raises(llm.DeadlineExceeded):
        asyncio.run(llm.chat([], deadline=time.time() + 120))
    assert len(refused) == 3

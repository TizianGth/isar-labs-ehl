"""Cancellation must preserve the slots needed by the submission phase."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from rfq import llm


def _client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _response():
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}', tool_calls=[]))], usage=None)


async def _assert_one_slot(slots):
    await asyncio.wait_for(slots.acquire(), timeout=0.1)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slots.acquire(), timeout=0.02)
    finally:
        slots.release()


def test_cancelled_request_releases_acquired_slot(monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        slots = asyncio.Semaphore(1)
        async def create(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(llm, '_slots', slots)
        monkeypatch.setattr(llm, '_run_deadline', float('inf'))
        monkeypatch.setattr(llm, 'client', lambda: _client(create))
        task = asyncio.create_task(llm.chat([], deadline=time.time() + 10))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_one_slot(slots)
    asyncio.run(scenario())


def test_cancellation_while_waiting_does_not_release(monkeypatch):
    async def scenario():
        waiting = asyncio.Event()
        class ObservedSemaphore(asyncio.Semaphore):
            async def acquire(self):
                waiting.set()
                return await super().acquire()
        slots = ObservedSemaphore(1)
        await slots.acquire()
        waiting.clear()
        async def create(**kwargs):
            pytest.fail('request must not start without a slot')
        monkeypatch.setattr(llm, '_slots', slots)
        monkeypatch.setattr(llm, '_run_deadline', float('inf'))
        monkeypatch.setattr(llm, 'client', lambda: _client(create))
        task = asyncio.create_task(llm.chat([], deadline=time.time() + 10))
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slots.acquire(), timeout=0.02)
        slots.release()
        await _assert_one_slot(slots)
    asyncio.run(scenario())


def test_success_and_retry_release_before_sleep(monkeypatch):
    async def scenario():
        slots = asyncio.Semaphore(1)
        attempts = []
        async def create(**kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise TimeoutError('synthetic transient error')
            return _response()
        async def retry_sleep(delay):
            await _assert_one_slot(slots)
        monkeypatch.setattr(llm, '_slots', slots)
        monkeypatch.setattr(llm, '_run_deadline', float('inf'))
        monkeypatch.setattr(llm, 'client', lambda: _client(create))
        monkeypatch.setattr(llm.asyncio, 'sleep', retry_sleep)
        monkeypatch.setattr(llm, 'usage', dict(llm.usage))
        assert (await llm.chat([], deadline=time.time() + 10))['content'] == '{}'
        assert len(attempts) == 2
        await _assert_one_slot(slots)
    asyncio.run(scenario())


def test_release_uses_acquired_semaphore_instance(monkeypatch):
    async def scenario():
        acquired = asyncio.Semaphore(1)
        replacement = asyncio.Semaphore(0)
        async def create(**kwargs):
            llm._slots = replacement
            return _response()
        monkeypatch.setattr(llm, '_slots', acquired)
        monkeypatch.setattr(llm, '_run_deadline', float('inf'))
        monkeypatch.setattr(llm, 'client', lambda: _client(create))
        monkeypatch.setattr(llm, 'usage', dict(llm.usage))
        await llm.chat([], deadline=time.time() + 10)
        await _assert_one_slot(acquired)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(replacement.acquire(), timeout=0.02)
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 403, 413, 422])
def test_repeated_refusal_is_bounded(monkeypatch, status):
    """A revoked token or invalid request stops after three consecutive refusals."""
    calls = []

    class Refused(Exception):
        status_code = status

    async def create(**kwargs):
        calls.append(1)
        raise Refused("token revoked")

    monkeypatch.setattr(llm, '_slots', asyncio.Semaphore(1))
    monkeypatch.setattr(llm, '_run_deadline', float('inf'))
    monkeypatch.setattr(llm, 'client', lambda: _client(create))
    monkeypatch.setattr(llm.asyncio, 'sleep', _no_sleep)
    with pytest.raises(llm.DeadlineExceeded):
        asyncio.run(llm.chat([], deadline=time.time() + 30))
    assert len(calls) == 3


async def _no_sleep(delay):
    return None


@pytest.mark.parametrize("codes", [[502, 403, 409, 503], [403, 403, 502, 403, 403], [404, 404, 404]])
def test_transient_proxy_refusals_are_retried(monkeypatch, codes):
    calls = []
    class Status(Exception):
        def __init__(self, status):
            self.status_code = status
    async def scenario():
        slots = asyncio.Semaphore(1)
        async def create(**kwargs):
            calls.append(1)
            if len(calls) <= len(codes):
                raise Status(codes[len(calls) - 1])
            return _response()
        async def sleep(delay):
            await _assert_one_slot(slots)
        monkeypatch.setattr(llm, '_slots', slots)
        monkeypatch.setattr(llm, '_run_deadline', float('inf'))
        monkeypatch.setattr(llm, 'client', lambda: _client(create))
        monkeypatch.setattr(llm.asyncio, 'sleep', sleep)
        monkeypatch.setattr(llm, 'usage', dict(llm.usage))
        reply = await llm.chat([], deadline=time.time() + 60)
        assert reply['content'] == '{}' and len(calls) == len(codes) + 1
        await _assert_one_slot(slots)
    asyncio.run(scenario())


def test_retry_does_not_extend_the_deadline(monkeypatch):
    async def scenario():
        clock, calls = [100.0], []
        async def failed(*args, **kwargs):
            calls.append(1)
            clock[0] += 2
            raise TimeoutError('synthetic outage')
        async def sleep(delay):
            clock[0] += delay
        monkeypatch.setattr(llm.time, 'time', lambda: clock[0])
        monkeypatch.setattr(llm, '_run_deadline', 106.0)
        monkeypatch.setattr(llm, '_first_answer', failed)
        monkeypatch.setattr(llm.asyncio, 'sleep', sleep)
        with pytest.raises(llm.DeadlineExceeded):
            await llm.chat([], deadline=106.0)
        assert len(calls) == 2
    asyncio.run(scenario())

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


def test_refused_access_is_not_retried(monkeypatch):
    """After an accepted answer the platform revokes the run token: 401 must end the call at once."""
    calls = []

    class Refused(Exception):
        status_code = 401

    async def create(**kwargs):
        calls.append(1)
        raise Refused("token revoked")

    monkeypatch.setattr(llm, '_slots', asyncio.Semaphore(1))
    monkeypatch.setattr(llm, '_run_deadline', float('inf'))
    monkeypatch.setattr(llm, 'client', lambda: _client(create))
    monkeypatch.setattr(llm.asyncio, "sleep", _no_sleep)
    with pytest.raises(llm.DeadlineExceeded):
        asyncio.run(llm.chat([], deadline=time.time() + 30))
    assert len(calls) == 3                     # three refusals in a row, not sixty attempts


async def _no_sleep(seconds):
    return None


def test_a_single_403_of_a_proxy_in_trouble_is_retried(monkeypatch):
    """Fake portal workbench/shared-stock: the model proxy answered 502, 503, 403, 409 while it recovered; the one
    403 ended the whole tender mission after 41 s."""
    class Status(Exception):
        def __init__(self, code):
            super().__init__(f"Error code: {code}")
            self.status_code = code

    codes = iter([502, 403, 409, 503])
    calls = []

    async def create(**kwargs):
        calls.append(1)
        code = next(codes, None)
        if code:
            raise Status(code)
        return _response()

    monkeypatch.setattr(llm, '_slots', asyncio.Semaphore(1))
    monkeypatch.setattr(llm, '_run_deadline', float('inf'))
    monkeypatch.setattr(llm, 'client', lambda: _client(create))
    monkeypatch.setattr(llm.asyncio, "sleep", _no_sleep)
    reply = asyncio.run(llm.chat([], deadline=time.time() + 60))
    assert reply["content"] == '{}' and len(calls) == 5

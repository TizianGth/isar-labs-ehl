"""Profiling stays causal, counts all metered work, and excludes source values."""
import asyncio
import importlib.util
from pathlib import Path

import pytest

from rfq import profile

_spec = importlib.util.spec_from_file_location(
    "profile_ledger", Path(__file__).resolve().parents[2] / "dev" / "profile_ledger.py")
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(profile, "ENABLED", True)
    monkeypatch.setattr(profile, "log", lambda event, **fields: captured.append({"event": event, **fields}))
    return captured


def test_parallel_nested_spans_keep_task_parents_and_cancellation(events):
    async def scenario():
        async def worker():
            async with profile.span("decision", category="decision") as decision:
                async with profile.span("request", category="model") as request:
                    await asyncio.sleep(0)
                assert request.parent == decision.id
        with profile.span("run") as run:
            await asyncio.gather(worker(), worker())
            task = asyncio.create_task(cancelled())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert all(event["parent"] == run.id for event in events if event["name"] == "decision")

    async def cancelled():
        async with profile.span("cancelled_request"):
            await asyncio.Event().wait()

    asyncio.run(scenario())
    assert next(event for event in events if event["name"] == "cancelled_request")["status"] == "cancelled"
    assert profile._parent.get() is None
    assert all(event["seconds"] >= 0 for event in events)


def test_disabled_spans_do_not_read_clocks_or_emit(monkeypatch, events):
    monkeypatch.setattr(profile, "ENABLED", False)
    monkeypatch.setattr(profile.time, "monotonic_ns", lambda: pytest.fail("disabled profiler read the clock"))
    with profile.span("disabled"):
        pass
    assert events == []


def test_timed_operation_preserves_errors_and_excludes_arguments(events):
    @profile.timed("browser_action", agent="browser", category="action")
    async def operation(secret):
        assert secret == "private-value"
        raise RuntimeError("private-value")

    with pytest.raises(RuntimeError, match="private-value"):
        asyncio.run(operation("private-value"))
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert "private-value" not in str(events)
    assert operation.__name__ == "operation"


def test_metadata_rejects_source_values_and_prompt_counts_do_not_copy(events):
    with pytest.raises(ValueError):
        profile.span("form", secret="hidden")
    with pytest.raises(ValueError):
        profile.span("form", rows="hidden")
    messages = [{"role": "user", "content": [{"type": "text", "text": "synthetic"},
                                               {"type": "image_url", "image_url": {"url": "hidden"}}]}]
    assert profile.prompt_metrics(messages) == {"characters": 9, "images": 1}
    assert events == []


def test_metering_counts_cached_input_once_and_speculative_work(events):
    profile.model_usage(attempt_id=10, model="synthetic", input_tokens=1000, output_tokens=20,
                        cached_tokens=800, reasoning_tokens=5)
    profile.model_usage(attempt_id=11, model="synthetic", input_tokens=500, output_tokens=10,
                        cached_tokens=400, reasoning_tokens=7, reasoning_in_output=False, speculative=True)
    profile.model_usage(attempt_id=12, model="synthetic", known=False, speculative=True)
    result = ledger.token_ledger(events + [events[0]])
    assert result["attempts"] == 3
    assert result["total_tokens"] == 1537
    assert result["cached_tokens"] == 1200
    assert result["speculative_tokens"] == 517
    assert result["unknown_usage_attempts"] == 1 and not result["complete"]
    with pytest.raises(ValueError):
        profile.model_usage(attempt_id=13, model="synthetic", input_tokens=3, output_tokens=1, cached_tokens=4)


def test_overlapping_spans_are_not_summed_as_wall_time():
    events = [dict(event="profile_span", id=1, name="request", category="model", start=2, seconds=5),
              dict(event="profile_span", id=2, name="request", category="model", start=4, seconds=7)]
    result = ledger.span_ledger(events, wall_seconds=15)
    assert result["categories"]["model"] == {"count": 2, "summed_seconds": 12, "union_seconds": 9}
    assert result["uninstrumented_gaps"] == [[11, 15], [0, 2]]


def test_incomplete_platform_capture_cannot_pass_budget(tmp_path):
    capture = tmp_path / "capture.json"
    capture.write_text('{"content":[{"type":"text","text":"Status running\\n'
                       'LLM: 10 calls, $0.01, 90,000 in / 10,000 out tokens\\n'
                       'model: 90,000 in (80,000 cached) / 10,000 out"}]}')
    result = ledger.analyze([capture])
    assert result["observed_token_lower_bound"] == 180_000
    assert result["within_200k"] is None
    assert result["platform_snapshots"][0]["cached_tokens"] == 80_000


def test_platform_raw_uncached_counts_are_normalized_without_sdk_double_count(tmp_path):
    import json
    capture = tmp_path / "calls.json"
    capture.write_text(json.dumps({"run_id": "synthetic", "total": 2, "next": None, "finished_at": 123,
                                  "calls": [{"id": 1, "status": 200, "input_tokens": 100,
                                             "cache_read_tokens": 900, "cache_write_tokens": 0, "output_tokens": 20},
                                            {"id": 2, "status": 200, "input_tokens": 10,
                                             "cache_read_tokens": 800, "cache_write_tokens": 100, "output_tokens": 30}]}))
    events, _ = ledger.read_capture(capture)
    events.append(dict(event="profile_usage", attempt_id=42, known=True,
                       input_tokens=1000, cached_tokens=900, output_tokens=20))
    result = ledger.token_ledger(events)
    assert result["total_tokens"] == 1960
    assert result["input_tokens"] == 1910 and result["uncached_tokens"] == 110
    assert result["cached_tokens"] == 1700 and result["cache_write_tokens"] == 100
    assert result["attempts"] == 2 and result["complete"]


def test_cli_stdout_wrapper_preserves_sdk_total_prompt_count(tmp_path):
    import json
    capture = tmp_path / "logs.jsonl"
    capture.write_text(json.dumps({"at": 1, "stream": "stdout", "text": json.dumps({
        "event": "profile_usage", "attempt_id": 1, "known": True,
        "input_tokens": 1000, "output_tokens": 20, "cached_tokens": 900}) + "\n"}))
    events, _ = ledger.read_capture(capture)
    assert ledger.token_ledger(events)["total_tokens"] == 1020

"""Constant-memory timing and metering; aggregation belongs outside the agent.

One line is emitted per completed span or metered request. No polling, background
tasks, prompt contents, URLs, answer values, credentials, or per-row events.
"""
from contextvars import ContextVar
from functools import wraps
from itertools import count
import os
import time

from .log import elapsed, log

ENABLED = os.environ.get("RFQ_PROFILE", "1").lower() not in {"0", "false", "off"}
_ids = count(1)
_parent: ContextVar[int | None] = ContextVar("profile_parent", default=None)
_SAFE_FIELDS = frozenset({
    "attempt", "step", "count", "bytes", "characters", "images", "rows",
    "chunks", "cache_hit", "speculative", "input_tokens", "output_tokens",
    "cached_tokens", "reasoning_tokens", "status_code", "remaining_seconds",
})


class Span:
    """Use with sync or async ``with``; cancellation closes the span as well.

    Names, category and agent must be developer-defined labels, never page text.
    Only numeric counters and booleans can be attached, preventing value leaks.
    """
    __slots__ = ("name", "agent", "category", "id", "parent", "start", "clock",
                 "fields", "token", "closed")

    def __init__(self, name: str, *, agent: str = "", category: str = "", **fields):
        self.name, self.agent, self.category = name, agent, category
        self.id = self.parent = self.token = None
        self.start = self.clock = 0
        self.closed = False
        self.fields = {}
        self.set(**fields)

    def set(self, **fields):
        for key, value in fields.items():
            if key not in _SAFE_FIELDS or not isinstance(value, (bool, int, float)):
                raise ValueError(f"unsupported profiling field: {key}")
        self.fields.update(fields)
        return self

    def __enter__(self):
        if ENABLED:
            self.id = next(_ids)
            self.parent = _parent.get()
            self.start, self.clock = elapsed(), time.monotonic_ns()
            self.token = _parent.set(self.id)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if ENABLED and self.token is not None and not self.closed:
            seconds = (time.monotonic_ns() - self.clock) / 1_000_000_000
            _parent.reset(self.token)
            self.closed = True
            status = "ok" if exc_type is None else (
                "cancelled" if exc_type.__name__ == "CancelledError" else "error")
            log("profile_span", id=self.id, parent=self.parent, name=self.name,
                agent=self.agent, category=self.category, start=round(self.start, 6),
                seconds=round(seconds, 6), status=status, **self.fields)
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, traceback):
        return self.__exit__(exc_type, exc, traceback)


def span(name: str, *, agent: str = "", category: str = "", **fields) -> Span:
    return Span(name, agent=agent, category=category, **fields)


def timed(name: str, *, agent: str = "", category: str = ""):
    """Time an async operation without recording its arguments or return value."""
    def decorate(operation):
        @wraps(operation)
        async def measured(*args, **kwargs):
            with span(name, agent=agent, category=category):
                return await operation(*args, **kwargs)
        return measured
    return decorate


def model_usage(*, attempt_id: int, model: str, input_tokens: int | None = None,
                output_tokens: int | None = None, cached_tokens: int | None = None,
                reasoning_tokens: int = 0, reasoning_in_output: bool = True,
                speculative: bool = False, known: bool = True) -> None:
    """Meter every provider response, including speculative losers.

    Cached input is a subset of input. Reasoning normally belongs to output;
    add it only when the provider explicitly reports it separately. Cancelled
    requests with no usage must emit known=False, never fabricated zero usage.
    """
    if not ENABLED:
        return
    values = (input_tokens, output_tokens, cached_tokens, reasoning_tokens)
    if any(value is not None and (not isinstance(value, int) or value < 0) for value in values):
        raise ValueError("token counts must be nonnegative integers")
    if known and (input_tokens is None or output_tokens is None):
        raise ValueError("known usage requires input and output counts")
    if cached_tokens is not None and input_tokens is not None and cached_tokens > input_tokens:
        raise ValueError("cached input exceeds total input")
    log("profile_usage", attempt_id=attempt_id, model=model, known=known,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cached_tokens=cached_tokens, reasoning_tokens=reasoning_tokens,
        reasoning_in_output=reasoning_in_output, speculative=speculative)


def prompt_metrics(messages: list[dict]) -> dict:
    """Count characters and image parts without copying or recording content.

    Character counts describe pruning, not estimated tokens. Platform metering
    remains authoritative for acceptance and cancelled-request usage.
    """
    characters = images = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            characters += len(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    characters += len(part.get("text", ""))
                elif part.get("type") in {"image_url", "input_image"}:
                    images += 1
    return {"characters": characters, "images": images}

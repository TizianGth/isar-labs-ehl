"""Model access: the hosted Qwen through the run's metering proxy (OpenAI-compatible).

Every call is retried on transient failures, limited by a semaphore (the team shares 32 Qwen slots across all runs)
and logged with tokens and latency. `thinking=False` turns Qwen's reasoning off: about 3x faster for navigation.
All waiting (semaphore, request, retry pauses) is bounded by a deadline: the caller's, and never later than the run's.
"""
import asyncio
import base64
import json
import os
import re
import time

from openai import AsyncOpenAI

from .log import log, register_secret
from .profile import span, model_usage, prompt_metrics

MODEL = os.environ.get("RFQ_MODEL", "qwen3.8-flash-next")
_client: AsyncOpenAI | None = None
PARALLEL = max(1, int(os.environ.get("RFQ_LLM_PARALLEL", "8")))
_slots = asyncio.Semaphore(PARALLEL)
# Background extraction stays capped; navigation can use otherwise idle slots.
_interactive = asyncio.Semaphore(PARALLEL)
_background = asyncio.Semaphore(4)
usage = {"calls": 0, "input": 0, "cached": 0, "output": 0, "seconds": 0.0, "images": 0, "hedges": 0}
_run_deadline = float("inf")   # epoch seconds; set by the orchestrator
# Hedged requests ("The Tail at Scale", Dean & Barroso 2013): a request that is still silent long after a normal one
# would have answered is probably stuck in a queue, so an identical twin is sent and the first answer wins.
# Measured over 3,000+ calls of our runs: tool calls p50 2.2 s / p99 9.7 s, JSON extractions p50 7.9 s / p99 30 s;
# a stuck request otherwise blocks a mission for the full per-call timeout (60 s / 150 s).
HEDGE_AFTER = {True: 12.0, False: 30.0}   # tools / no tools; slower when recent calls are slower (3x their median)
MAX_HEDGES = int(os.environ.get("RFQ_LLM_HEDGES", "0"))
_hedging = 0
MAX_ATTEMPTS = 60   # a model outage of a few minutes must not end a mission: 5xx, 429, timeouts are retried (≤ 8 s apart)
QUIET_CALLS = bool(os.environ.get("CHALLENGE_RUN_TOKEN"))   # in a run the platform keeps only the end of the log
_recent: dict[bool, list[float]] = {True: [], False: []}


class DeadlineExceeded(RuntimeError):
    """Not enough time left for a model call."""


def set_run_deadline(epoch_seconds: float) -> None:
    global _run_deadline
    _run_deadline = epoch_seconds


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=os.environ["QWEN_BASE_URL"], api_key=os.environ["QWEN_API_KEY"],
                              timeout=float(os.environ.get("RFQ_LLM_TIMEOUT", "180")), max_retries=0)
    return _client


async def chat(messages: list[dict], *, tools: list[dict] | None = None, thinking: bool = False,
               json_mode: bool = False, temperature: float = 0.1, tag: str = "", timeout: float | None = None,
               deadline: float | None = None, max_tokens: int | None = None) -> dict:
    """One chat completion; returns {"content": str, "tool_calls": [(name, args_dict)]}.
    Raises DeadlineExceeded when the deadline does not leave room for another attempt."""
    kwargs: dict = {"model": MODEL, "messages": messages, "temperature": temperature,
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": thinking}}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "required"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    end = min(deadline or float("inf"), _run_deadline)
    per_call = timeout or (60 if tools else 150)   # a hung call must not eat minutes
    delay, refusals = 1.0, 0
    for attempt in range(MAX_ATTEMPTS):     # transient failures are retried while the deadline allows
        left = end - time.time()
        if left < 3:
            log("llm_deadline", tag=tag, attempt=attempt)
            raise DeadlineExceeded(tag)
        started = time.monotonic()
        queue_profile = span("llm_queue", agent=tag.split(":")[0].split("@")[0], category="queue", attempt=attempt)
        queue_profile.__enter__()
        slots = _slots
        lane = _background if tag.startswith(("extract", "brief", "vision", "submit:")) else _interactive
        try:
            await asyncio.wait_for(lane.acquire(), timeout=left - 2)
            try:
                await asyncio.wait_for(slots.acquire(), timeout=max(.1, end - time.time() - 2))
            except BaseException:
                lane.release()
                raise
        except asyncio.TimeoutError:
            log("llm_deadline", tag=tag, attempt=attempt, waiting="slot")
            raise DeadlineExceeded(tag)
        finally:
            queue_profile.__exit__(None, None, None)
        try:
            try:
                requested = time.monotonic()
                response = await _first_answer(kwargs, per_call, end, None if thinking else _hedge_after(bool(tools)), tag)
                _recent[bool(tools)][:] = _recent[bool(tools)][-29:] + [time.monotonic() - requested]
            finally:
                slots.release()
                lane.release()
        except Exception as error:
            log("llm_error", tag=tag, attempt=attempt, error=f"{type(error).__name__}: {str(error)[:200]}")
            status = getattr(error, "status_code", None)
            # Refusals: the request (400/413/422) or the access (401/403, e.g. the run token after the accepted
            # answer). A proxy in trouble also answers 403 once in a while, so only the third refusal in a row ends
            # the call; 404/409/429/5xx and timeouts are hiccups and are retried while the deadline allows.
            refusals = refusals + 1 if status in (400, 401, 403, 413, 422) else 0
            if refusals >= 3:
                raise DeadlineExceeded(f"{tag}: request refused ({status})")
            await asyncio.sleep(max(0.0, min(delay, end - time.time() - 3)))
            delay = min(delay * 2, 8)
            continue
        seconds = time.monotonic() - started
        message = response.choices[0].message
        tokens = response.usage
        cached = getattr(getattr(tokens, "prompt_tokens_details", None), "cached_tokens", 0) or 0 if tokens else 0
        usage["seconds"] += seconds
        calls = []
        for call in message.tool_calls or []:
            calls.append((call.function.name, _loads(call.function.arguments) or {}))
        if tools and not calls:  # some replies put the call into the text
            calls = _calls_from_text(message.content or "")
        log("llm", tag=tag, s=round(seconds, 2),
            tokens=[getattr(tokens, "prompt_tokens", None), getattr(tokens, "completion_tokens", None), cached])
        finish = getattr(response.choices[0], 'finish_reason', None)
        parsed = _loads(message.content)
        def register_private(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key.lower() in ('tan', 'password', 'token'):
                        register_secret(value)
                    else:
                        register_private(value)
            elif isinstance(obj, list):
                for value in obj:
                    register_private(value)
        register_private(parsed)
        log('llm_reply', tag=tag, finish_reason=finish, data=parsed if isinstance(parsed, dict) else None,
            tool_calls=calls, characters=len(message.content or ''))
        return {"content": message.content or "", "tool_calls": calls, "finish_reason": finish}
    raise DeadlineExceeded(f"LLM unavailable after retries ({tag})")


def _hedge_after(tools: bool) -> float:
    recent = sorted(_recent[tools])
    typical = recent[len(recent) // 2] if len(recent) >= 5 else 0.0
    return max(HEDGE_AFTER[tools], 3 * typical)


async def _request(kwargs: dict, timeout: float, speculative=False):
    usage["attempts"] = usage.get("attempts", 0) + 1
    with span('llm_request', agent='qwen', category='model', speculative=speculative,
              **prompt_metrics(kwargs.get('messages', []))) as request:
        try:
            response = await client().chat.completions.create(**kwargs, timeout=timeout)
        except BaseException:
            model_usage(attempt_id=request.id, model=MODEL, speculative=speculative, known=False)
            raise
        tokens = response.usage
        if tokens:
            usage["input"] += tokens.prompt_tokens or 0
            usage["output"] += tokens.completion_tokens or 0
            cached = getattr(getattr(tokens, "prompt_tokens_details", None), "cached_tokens", 0) or 0
            reasoning = getattr(getattr(tokens, 'completion_tokens_details', None), 'reasoning_tokens', 0) or 0
            usage["cached"] += cached
            model_usage(attempt_id=request.id, model=MODEL, input_tokens=tokens.prompt_tokens or 0,
                        output_tokens=tokens.completion_tokens or 0, cached_tokens=cached,
                        reasoning_tokens=reasoning, speculative=speculative)
        else:
            model_usage(attempt_id=request.id, model=MODEL, speculative=speculative, known=False)
        usage["calls"] += 1
        return response


async def _first_answer(kwargs: dict, per_call: float, end: float, hedge_after: float | None, tag: str):
    """The first successful answer of the request or, when it stays silent for `hedge_after` seconds, of its twin.
    The other request is cancelled. Errors are raised only when every request failed."""
    global _hedging
    if hedge_after is not None and hedge_after >= per_call - 5:
        hedge_after = None                          # the request's own timeout comes first
    tasks = [asyncio.ensure_future(_request(kwargs, max(2.0, min(per_call, end - time.time()))))]
    twin = False
    try:
        done, _ = await asyncio.wait(tasks, timeout=hedge_after)
        if hedge_after is not None and not done and _hedging < MAX_HEDGES and end - time.time() > 10:
            _hedging += 1
            twin = True
            usage["hedges"] += 1
            log("llm_hedge", tag=tag, after=round(hedge_after, 1))
            tasks.append(asyncio.ensure_future(_request(kwargs, max(2.0, min(per_call, end - time.time())), speculative=True)))
        pending, error = set(tasks), None
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception() is None:
                    if twin:
                        log("llm_hedge_won", tag=tag, by="twin" if task is tasks[-1] else "first")
                    return task.result()
                error = task.exception()
        raise error
    finally:
        if twin:
            _hedging -= 1
        for task in tasks:
            if not task.done():
                task.cancel()
            elif not task.cancelled():
                task.exception()   # a failed loser: retrieved, no warning
        await asyncio.gather(*tasks, return_exceptions=True)


async def ask_json(system: str, user: str | list, *, thinking: bool = False, tag: str = "",
                   deadline: float | None = None, max_tokens: int | None = None) -> dict:
    """A JSON object from the model; one repair round if the reply does not parse. `user` may be multimodal parts."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        reply = await chat(messages, thinking=thinking, json_mode=True, temperature=0, tag=tag, deadline=deadline,
                           max_tokens=max_tokens * (attempt + 1) if max_tokens else None)
        data = _loads(reply["content"])
        if reply.get("finish_reason") == "length":
            log("llm_truncated", tag=tag, output_limit=max_tokens, attempt=attempt)
        if isinstance(data, dict) and reply.get("finish_reason") != "length":
            return data
        messages += [{"role": "assistant", "content": reply["content"][:2000]},
                     {"role": "user", "content": "The reply was invalid or truncated. Return one complete JSON object; use fewer actions and URLs, sparse facts, no explanations."}]
    log("llm_json_failed", tag=tag)
    return {}


def image_part(jpeg: bytes) -> dict:
    """An image content part (OpenAI format) for multimodal messages."""
    usage["images"] += 1
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def _loads(text: str | None):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except ValueError:
                return None
    return None


def _calls_from_text(text: str) -> list[tuple[str, dict]]:
    data = _loads(text)
    if isinstance(data, dict) and isinstance(data.get("name"), str):
        return [(data["name"], data.get("arguments") or data.get("args") or {})]
    return []

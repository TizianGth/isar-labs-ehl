"""Model access: the hosted Qwen through the run's metering proxy (OpenAI-compatible).

Every call is retried on transient failures, limited by a semaphore (the team shares 32 Qwen slots across all runs)
and logged with tokens and latency. `thinking=False` turns Qwen's reasoning off: about 3x faster for navigation.
"""
import asyncio
import json
import os
import re
import time

from openai import AsyncOpenAI

from .log import log

MODEL = os.environ.get("RFQ_MODEL", "qwen3.8-flash-next")
_client: AsyncOpenAI | None = None
_slots = asyncio.Semaphore(int(os.environ.get("RFQ_LLM_PARALLEL", "10")))
usage = {"calls": 0, "input": 0, "output": 0, "seconds": 0.0}


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=os.environ["QWEN_BASE_URL"], api_key=os.environ["QWEN_API_KEY"],
                              timeout=float(os.environ.get("RFQ_LLM_TIMEOUT", "180")), max_retries=0)
    return _client


async def chat(messages: list[dict], *, tools: list[dict] | None = None, thinking: bool = False,
               json_mode: bool = False, temperature: float = 0.1, tag: str = "", timeout: float | None = None) -> dict:
    """One chat completion; returns {"content": str, "tool_calls": [(name, args_dict)]}."""
    kwargs: dict = {"model": MODEL, "messages": messages, "temperature": temperature,
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": thinking}}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "required"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    delay = 1.0
    for attempt in range(6):
        started = time.monotonic()
        try:
            async with _slots:
                # a hung call must not eat minutes: short for navigation steps, longer for big extractions
                response = await client().chat.completions.create(
                    **kwargs, timeout=timeout or (60 if tools else 150))
        except Exception as error:  # network, 5xx, timeouts, queue: retry with backoff
            log("llm_error", tag=tag, attempt=attempt, error=f"{type(error).__name__}: {str(error)[:200]}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)
            continue
        seconds = time.monotonic() - started
        message = response.choices[0].message
        if response.usage:
            usage["input"] += response.usage.prompt_tokens or 0
            usage["output"] += response.usage.completion_tokens or 0
        usage["calls"] += 1
        usage["seconds"] += seconds
        calls = []
        for call in message.tool_calls or []:
            calls.append((call.function.name, _loads(call.function.arguments) or {}))
        if tools and not calls:  # some replies put the call into the text
            calls = _calls_from_text(message.content or "")
        log("llm", tag=tag, s=round(seconds, 2),
            tokens=[getattr(response.usage, "prompt_tokens", None), getattr(response.usage, "completion_tokens", None)])
        return {"content": message.content or "", "tool_calls": calls}
    raise RuntimeError(f"LLM unavailable after retries ({tag})")


async def ask_json(system: str, user: str, *, thinking: bool = True, tag: str = "") -> dict:
    """A JSON object from the model; one repair round if the reply does not parse."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(3):
        reply = await chat(messages, thinking=thinking, json_mode=True, temperature=0, tag=tag)
        data = _loads(reply["content"])
        if isinstance(data, dict):
            return data
        messages += [{"role": "assistant", "content": reply["content"][:2000]},
                     {"role": "user", "content": "That was not one valid JSON object. Reply with the JSON object only."}]
    log("llm_json_failed", tag=tag)
    return {}


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

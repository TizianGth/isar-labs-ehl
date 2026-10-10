"""Quick decisions by the hosted Clef model (POST $CLEF_BASE_URL/v1/decide), for guards that run out of rules.

Off when CLEF_BASE_URL is unset or RFQ_CLEF=0. Never blocks the agent for long: a short timeout, and after a few
failures (e.g. a cold model) it stays off for the rest of the run — the browser model then handles the case itself.
"""
from __future__ import annotations

import os

import httpx

TIMEOUT_S = 10
MAX_FAILURES = 3
MIN_CONFIDENCE = 0.6
NONE = "none"

_failures = 0


def enabled(env=os.environ) -> bool:
    return bool(env.get("CLEF_BASE_URL")) and env.get("RFQ_CLEF", "1") != "0" and _failures < MAX_FAILURES


async def choose(state: dict, instructions: str, options: dict[str, str], env=os.environ) -> str | None:
    """One of `options` (id -> description), or None for "none of them", low confidence or no answer."""
    global _failures
    if not enabled(env) or not options:
        return None
    body = {"state": state, "questions": {"q": {"type": "choice", "instructions": instructions,
                                                 "criteria": {**options, NONE: "None of these"}}}}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(env["CLEF_BASE_URL"].rstrip("/") + "/v1/decide", json=body,
                                  headers={"Authorization": f"Bearer {env.get('CLEF_API_KEY', '')}"})
        r.raise_for_status()
        answer = r.json()["answers"]["q"]
    except Exception:
        _failures += 1
        return None
    _failures = 0
    pick = answer.get("choice")
    if pick not in options or (answer.get("confidence") or 0) < MIN_CONFIDENCE:
        return None
    return pick

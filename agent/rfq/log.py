"""Structured logging: one JSON object per line on stdout (the platform shows stdout in the run log)."""
import json
import time

_T0 = time.monotonic()


def elapsed() -> float:
    return time.monotonic() - _T0


def log(event: str, **fields) -> None:
    line = {"t": round(elapsed(), 2), "event": event, **fields}
    print(json.dumps(line, ensure_ascii=False, default=str)[:4000], flush=True)

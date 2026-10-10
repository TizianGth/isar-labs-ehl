"""Structured logging: one JSON object per line on stdout (the platform shows stdout in the run log)."""
import json
import time
from contextlib import contextmanager

_T0 = time.monotonic()


def elapsed() -> float:
    return time.monotonic() - _T0


def _compact(value, budget: int, depth: int = 0):
    if depth > 8:
        return "[nested value omitted]"
    if isinstance(value, dict):
        items = list(value.items())
        result = {str(k): _compact(v, budget, depth + 1) for k, v in items[:64]}
        if len(items) > 64:
            result["_omitted_keys"] = len(items) - 64
        return result
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        result = [_compact(v, budget, depth + 1) for v in values[:max(1, budget // 32)]]
        if len(values) > len(result):
            result.append({"_omitted_items": len(values) - len(result)})
        return result
    if not isinstance(value, (str, int, float, bool, type(None))):
        value = str(value)
    if isinstance(value, str) and len(value) > budget:
        return value[:budget] + f"… [{len(value) - budget} chars omitted]"
    return value


def encode(event: str, fields: dict, *, limit: int = 4000) -> str:
    """Bound the serialized record without cutting JSON syntax or scalar timings."""
    header = {"t": round(elapsed(), 2), "event": str(event)[:160]}
    budget = 1600
    while True:
        line = {**_compact(fields, budget), **header}
        rendered = json.dumps(line, ensure_ascii=False, default=str, separators=(",", ":"))
        if len(rendered) <= limit:
            return rendered
        if budget > 32:
            budget = max(32, budget // 2)
            continue
        # Large maps can still exceed the limit even with shortened leaves. Keep
        # independent scalar metrics first and explicitly report dropped fields.
        line = {**header, "truncated": True, "omitted_fields": []}
        for key, value in sorted(fields.items(), key=lambda kv: isinstance(kv[1], (dict, list, tuple, set))):
            candidate = {**line, key: _compact(value, 32)}
            if len(json.dumps(candidate, ensure_ascii=False, default=str, separators=(",", ":"))) < limit - 100:
                line = candidate
            else:
                line["omitted_fields"].append(str(key)[:32])
        # Field names may themselves be unbounded; retain an omission count then.
        while len(json.dumps(line, ensure_ascii=False, default=str, separators=(",", ":"))) > limit:
            omitted = line["omitted_fields"]
            line["omitted_fields"] = f"{len(omitted)} fields omitted" if isinstance(omitted, list) else "fields omitted"
            if len(json.dumps(line, ensure_ascii=False, default=str, separators=(",", ":"))) > limit:
                key = next(k for k in reversed(line) if k not in {"t", "event", "truncated", "omitted_fields"})
                del line[key]
        return json.dumps(line, ensure_ascii=False, default=str, separators=(",", ":"))


def log(event: str, **fields) -> None:
    print(encode(event, fields), flush=True)


@contextmanager
def timed(phase: str, **fields):
    start = time.monotonic()
    status = "ok"
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        log("phase_timing", phase=phase, seconds=round(time.monotonic() - start, 4), status=status, **fields)

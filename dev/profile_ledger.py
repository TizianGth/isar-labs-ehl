#!/usr/bin/env python3
"""Offline span/token analysis. Accept JSONL logs or Atira MCP captures.

Usage: python3 dev/profile_ledger.py LOG... --seconds 80 --cost 0.02
Atira raw input excludes cache reads/writes; SDK prompt_tokens includes cache.
Normalize each source before aggregation. Never invent cancelled-request usage.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path
import re


def read_capture(path: Path) -> tuple[list[dict], str]:
    text = path.read_text()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        text = "\n".join(block.get("text", "") for block in data["content"]
                         if isinstance(block, dict) and block.get("type") == "text")
    elif isinstance(data, dict) and isinstance(data.get("calls"), list):
        run_id = data.get("run_id", "unknown")
        events = []
        for call in data["calls"]:
            counts = [call.get(key) for key in
                      ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")]
            known = all(isinstance(value, int) and value >= 0 for value in counts) and call.get("status") != 0
            incoming, cached, written, outgoing = counts
            events.append({"event": "platform_usage", "attempt_id": f"atira:{run_id}:{call['id']}",
                           "known": known, "input_tokens": incoming + cached + written if known else None,
                           "uncached_tokens": incoming, "cached_tokens": cached, "cache_write_tokens": written,
                           "output_tokens": outgoing, "reasoning_in_output": True, "reasoning_tokens": 0,
                           "run_id": run_id})
        events.append({"event": "platform_capture", "run_id": run_id, "reported_calls": data.get("total"),
                       "listed_calls": len(data["calls"]), "complete": data.get("next") is None
                       and data.get("finished_at") is not None and len(data["calls"]) == data.get("total")})
        return events, text
    elif isinstance(data, dict) and "event" in data:
        return [data], text
    events = []
    for line in text.splitlines():
        payload = line.partition("stdout: ")[2] if "stdout: " in line else line
        try:
            item = json.loads(payload)
        except ValueError:
            continue
        if isinstance(item, dict) and "event" in item:
            events.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            # The CLI's JSONL run logs wrap stdout JSON in {at, stream, text}.
            for nested in item["text"].splitlines():
                try:
                    inner = json.loads(nested)
                except ValueError:
                    continue
                if isinstance(inner, dict) and "event" in inner:
                    events.append(inner)
    return events, text


def union_seconds(intervals: list[tuple[float, float]]) -> float:
    total, end = 0.0, float("-inf")
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        total += max(0, stop - max(end, start))
        end = max(end, stop)
    return round(total, 6)


def gaps(intervals: list[tuple[float, float]], stop: float) -> list[list[float]]:
    """Observed gaps in this instrumentation, not proof that the process was idle."""
    result, cursor = [], 0.0
    for start, end in sorted(intervals):
        if start > cursor:
            result.append([round(cursor, 6), round(min(start, stop), 6)])
        cursor = max(cursor, end)
    if stop > cursor:
        result.append([round(cursor, 6), round(stop, 6)])
    return sorted((gap for gap in result if gap[1] > gap[0]),
                  key=lambda gap: gap[1] - gap[0], reverse=True)


def token_ledger(events: list[dict]) -> dict:
    platform = [event for event in events if event.get("event") == "platform_usage"]
    if len({event.get("run_id") for event in platform}) > 1:
        raise ValueError("do not combine platform captures from different runs")
    # SDK and platform represent the same requests. Never sum both ledgers.
    usages = platform or [event for event in events if event.get("event") == "profile_usage"]
    explicit = bool(usages)
    if not explicit:
        usages = [{"input_tokens": event["tokens"][0], "output_tokens": event["tokens"][1],
                   "cached_tokens": event["tokens"][2], "known": event["tokens"][0] is not None,
                   "reasoning_in_output": True, "reasoning_tokens": 0}
                  for event in events if event.get("event") == "llm"
                  and isinstance(event.get("tokens"), list) and len(event["tokens"]) == 3]
    totals = Counter()
    seen = {}
    for usage in usages:
        attempt = usage.get("attempt_id")
        if attempt is not None and attempt in seen:
            if seen[attempt] != usage:
                raise ValueError(f"conflicting metering for attempt {attempt}")
            continue
        if attempt is not None:
            seen[attempt] = usage
        totals["attempts"] += 1
        if not usage.get("known", False):
            totals["unknown_usage_attempts"] += 1
            continue
        incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
        cached, reasoning = usage.get("cached_tokens"), usage.get("reasoning_tokens", 0)
        if incoming is None or outgoing is None:
            raise ValueError("known usage lacks token counts")
        if min(incoming, outgoing, reasoning) < 0 or (cached is not None and not 0 <= cached <= incoming):
            raise ValueError("invalid token counts")
        totals["input_tokens"] += incoming
        totals["cache_write_tokens"] += usage.get("cache_write_tokens") or 0
        totals["uncached_tokens"] += usage.get("uncached_tokens", incoming - (cached or 0)) or 0
        totals["output_tokens"] += outgoing
        totals["cached_tokens"] += cached or 0
        totals["reasoning_tokens"] += reasoning
        separately_metered = reasoning if not usage.get("reasoning_in_output", True) else 0
        total = incoming + outgoing + separately_metered
        totals["total_tokens"] += total
        if usage.get("speculative"):
            totals["speculative_tokens"] += total
    fields = ("attempts", "unknown_usage_attempts", "input_tokens", "uncached_tokens", "cache_write_tokens", "output_tokens",
              "cached_tokens", "reasoning_tokens", "speculative_tokens", "total_tokens")
    captures = [event for event in events if event.get("event") == "platform_capture"]
    complete = explicit and totals["unknown_usage_attempts"] == 0
    if platform:
        complete &= bool(captures) and all(event["complete"] for event in captures)
    return {**{field: totals[field] for field in fields},
            "coverage": "platform raw calls normalized: input+cache_read+cache_write" if platform else (
                "instrumented SDK prompt_tokens (cache included)" if explicit else "legacy successful winners only"),
            "complete": complete}


def span_ledger(events: list[dict], wall_seconds: float | None = None) -> dict:
    spans = [event for event in events if event.get("event") == "profile_span"]
    groups, intervals = defaultdict(list), []
    for event in spans:
        start, seconds = float(event["start"]), float(event["seconds"])
        if start < 0 or seconds < 0:
            raise ValueError("invalid span timing")
        interval = (start, start + seconds)
        intervals.append(interval)
        groups[event.get("category") or event["name"]].append(interval)
    stop = wall_seconds if wall_seconds is not None else max((end for _, end in intervals), default=0)
    category = {name: {"count": len(values),
                       "summed_seconds": round(sum(end - start for start, end in values), 6),
                       "union_seconds": union_seconds(values)} for name, values in sorted(groups.items())}
    slow = sorted(spans, key=lambda event: event["seconds"], reverse=True)[:20]
    return {"spans": len(spans), "wall_seconds": stop, "categories": category,
            "uninstrumented_gaps": gaps(intervals, stop),
            "slowest_spans": [{key: event.get(key) for key in
                               ("id", "parent", "name", "agent", "category", "start", "seconds", "status")}
                              for event in slow],
            "note": "Summed times overlap. Union times are occupied wall time; gaps are not proof of idle time."}


def platform_snapshots(texts: list[str]) -> list[dict]:
    snapshots = []
    pattern = re.compile(r"LLM: ([\d,]+) calls, \$([\d.]+), ([\d,]+) in / ([\d,]+) out tokens")
    for text in texts:
        for match in pattern.finditer(text):
            calls, cost, incoming, outgoing = match.groups()
            # CLI modelLine prints raw input with cached in parentheses despite
            # cache being ADDITIONAL, not a subset of this raw input number.
            cache = sum(int(value.replace(",", "")) for value in re.findall(r"in \(([\d,]+) cached\)", text))
            snapshot = {"calls": int(calls.replace(",", "")), "cost_usd": cost,
                        "uncached_tokens": int(incoming.replace(",", "")),
                        "output_tokens": int(outgoing.replace(",", ""))}
            snapshot["cached_tokens"] = cache
            snapshot["input_tokens"] = snapshot["uncached_tokens"] + cache
            snapshot["total_tokens"] = snapshot["input_tokens"] + snapshot["output_tokens"]
            snapshot["normalization"] = "raw CLI input + separately reported cache reads; cache writes unavailable"
            snapshot["running_snapshot"] = "Status running" in text
            snapshots.append(snapshot)
    return snapshots


def analyze(paths: list[Path], *, seconds: float | None = None, cost: str | None = None,
            leader_cost: str = "0.02583", leader_seconds: str = "38.259") -> dict:
    captures = [read_capture(path) for path in paths]
    events = [event for captured, _ in captures for event in captured]
    ledger = token_ledger(events)
    snapshots = platform_snapshots([text for _, text in captures])
    lower_bound = max([ledger["total_tokens"]] + [snapshot["total_tokens"] for snapshot in snapshots])
    report = {"tokens": ledger, "platform_snapshots": snapshots,
              "observed_token_lower_bound": lower_bound,
              "within_200k": False if lower_bound > 200_000 else (True if ledger["complete"] else None),
              "timing": span_ledger(events, seconds),
              "actions": dict(Counter(event.get("tool", "unknown") for event in events
                                      if event.get("event") == "action")),
              "budget_note": "Normalize SDK prompt_tokens as inclusive and Atira raw input_tokens as uncached. "
                             "Use final platform totals for acceptance; cached input is included once. "
                             "Cancelled/unmetered requests and incomplete captures prevent a passing claim."}
    if seconds is not None and cost is not None:
        product = Decimal(cost) * Decimal(str(seconds))
        leader = Decimal(leader_cost) * Decimal(leader_seconds)
        report["comparison"] = {"cost_usd": cost, "seconds": seconds, "cost_times_seconds": str(product),
                                "leader_product": str(leader), "relative_product": str(product / leader),
                                "under_90_seconds": seconds < 90,
                                "cost_needed_at_90_seconds": str(leader / Decimal(90))}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--cost")
    parser.add_argument("--leader-cost", default="0.02583")
    parser.add_argument("--leader-seconds", default="38.259")
    args = parser.parse_args()
    print(json.dumps(analyze(args.captures, seconds=args.seconds, cost=args.cost,
                             leader_cost=args.leader_cost, leader_seconds=args.leader_seconds), indent=2))


if __name__ == "__main__":
    main()

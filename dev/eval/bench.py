"""Benchmark sandbox runs from the platform API, and compare paired runs with a fixed decision rule.

    uv run --project agent python dev/eval/bench.py RUN_ID [RUN_ID ...]          # one table, one row per run
    uv run --project agent python dev/eval/bench.py --pairs BASE:CAND [BASE:CAND ...]   # paired A/B decision
    uv run --project agent python dev/eval/bench.py --json RUN_ID ...             # raw metrics as JSON

The token comes from $ATIRA_TOKEN or `atira token`. Only reads; never starts, cancels or submits anything.

Decision rule for --pairs (docs/13-optimization-roadmap.md §4):
  INVALID      a run of the pair failed for platform reasons (failure_class set, no score): repeat that pair
  REJECT       a candidate run is not eligible, has lower quality than its baseline, or gave no answer
  ADOPT        median(C×T candidate / C×T baseline) <= 0.95 and the candidate wins at least 2 of 3 pairs
  ADOPT-COST   median C×T ratio <= 1.05, median cost ratio <= 0.97 and median time ratio <= 1.02
  REJECT       median C×T ratio > 1.05
  INCONCLUSIVE otherwise (run two more pairs, then decide again with all pairs)
"""
import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import urllib.request

API = os.environ.get("ATIRA_API", "https://ehl-challenge.atira.ai")
PRICES = {"input": 0.15e-6, "cached": 0.016e-6, "output": 0.47e-6}   # Qwen3.8-Flash-Next, simulated $/token
CACHED_SHARE_REF = 0.47     # median cached share of version C (docs/12 §3.2): cost normalized to this share
NAV_PROMPT_TOKENS = 3300    # prompt incl. cache >= this: a tool (navigation) call, else a JSON extraction call


def token() -> str:
    if os.environ.get("ATIRA_TOKEN"):
        return os.environ["ATIRA_TOKEN"]
    cli = shutil.which("atira") or os.path.expanduser("~/.local/bin/atira")
    return subprocess.run([cli, "token"], capture_output=True, text=True, check=True).stdout.strip()


def get(path: str, tok: str, raw: bool = False):
    request = urllib.request.Request(API + path, headers={"x-api-key": tok, "User-Agent": "isar-labs-bench"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = response.read().decode()
                return body if raw else json.loads(body)
        except Exception:
            if attempt == 3:
                raise
    return None


def calls_of(run_id: str, tok: str) -> list[dict]:
    calls, after = [], ""
    while True:
        page = get(f"/v1/runs/{run_id}/llm-calls?limit=2000" + (f"&after={after}" if after else ""), tok)
        calls += page.get("calls") or []
        after = page.get("next")
        if not after:
            return calls


def events_of(run_id: str, tok: str) -> list[dict]:
    events = []
    for line in get(f"/v1/runs/{run_id}/logs.jsonl", tok, raw=True).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("stream") != "stdout":
            continue
        for text in row.get("text", "").splitlines():
            try:
                events.append(json.loads(text))
            except ValueError:
                pass
    return events


def _quantile(values: list[float], q: float) -> float | None:
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 1) if values else None


def metrics(run_id: str, tok: str) -> dict:
    run = get(f"/v1/runs/{run_id}", tok)
    score = run.get("score") or {}
    seconds = (score.get("duration_ms") or ((run.get("finished_at") or 0) - (run.get("started_at") or 0))) / 1000
    cost = (score.get("cost_micro_usd") or run.get("cost_micro_usd") or 0) / 1e6
    calls = [c for c in calls_of(run_id, tok) if c.get("status") == 200]
    prompt = lambda c: (c.get("input_tokens") or 0) + (c.get("cache_read_tokens") or 0)
    nav = [c for c in calls if prompt(c) >= NAV_PROMPT_TOKENS]
    json_calls = [c for c in calls if prompt(c) < NAV_PROMPT_TOKENS]
    uncached = sum(c.get("input_tokens") or 0 for c in calls)
    cached = sum(c.get("cache_read_tokens") or 0 for c in calls)
    output = sum(c.get("output_tokens") or 0 for c in calls)
    total_prompt = uncached + cached
    normalized = (total_prompt * ((1 - CACHED_SHARE_REF) * PRICES["input"] + CACHED_SHARE_REF * PRICES["cached"])
                  + output * PRICES["output"])
    # time with >= 10 model calls in flight (our default slot limit)
    points = sorted([(c["at"], 1) for c in calls] + [(c["at"] + c["latency_ms"], -1) for c in calls])
    saturated, in_flight, last, peak = 0.0, 0, points[0][0] if points else 0, 0
    for at, delta in points:
        if in_flight >= 10:
            saturated += at - last
        in_flight += delta
        peak = max(peak, in_flight)
        last = at
    # hedge twins that were billed: same prompt size, second start 9-45 s after the first, before it ended
    pairs = 0
    ordered = sorted(calls, key=lambda c: c["at"])
    for i, first in enumerate(ordered):
        for second in ordered[i + 1:]:
            if second["at"] - first["at"] > 45000:
                break
            if (abs(prompt(first) - prompt(second)) <= 2 and 9000 <= second["at"] - first["at"]
                    and second["at"] < first["at"] + first["latency_ms"] + 500):
                pairs += 1
    events = events_of(run_id, tok)
    marks, missions, counters = {}, {}, {}
    for event in events:
        if event.get("event") == "run_summary":
            marks.update(event.get("marks") or {})
            missions.update(event.get("missions") or {})
            counters.update(event.get("counters") or {})
    evidence = next((e["t"] for e in events if e.get("event") == "evidence_done"), marks.get("evidence_done"))
    submit = next((e.get("seconds") for e in events if e.get("event") == "submit_phase_done"), None)
    tools: dict[str, int] = {}
    for m in missions.values():
        for tool, n in (m.get("tools") or {}).items():
            tools[tool] = tools.get(tool, 0) + n
    return {
        "run": run_id, "kind": run.get("kind"), "label": (run.get("package") or {}).get("label") or "",
        "package": ((run.get("package") or {}).get("sha256") or "")[:12], "status": run.get("status"),
        "failure_class": run.get("failure_class"), "has_answer": run.get("has_answer"),
        "quality": score.get("quality"), "eligible": score.get("eligible"),
        "seconds": round(seconds, 1), "cost": round(cost, 4), "cxt": round(max(cost, 0.01) * max(seconds, 1), 1),
        "cost_normalized": round(normalized, 4), "calls": len(calls), "nav_calls": len(nav), "json_calls": len(json_calls),
        "uncached": uncached, "cached": cached, "output": output,
        "cached_share": round(cached / total_prompt, 2) if total_prompt else None,
        "nav_p50": _quantile([c["latency_ms"] / 1000 for c in nav], 0.5),
        "json_p50": _quantile([c["latency_ms"] / 1000 for c in json_calls], 0.5),
        "saturated_s": round(saturated / 1000), "peak_in_flight": peak, "hedge_pairs": pairs,
        "evidence_done": evidence, "submit_phase_s": submit, "marks": marks, "counters": counters,
        "mission_ends": {tag: m.get("end") for tag, m in missions.items()}, "tools": tools,
    }


COLUMNS = ["run", "package", "quality", "eligible", "seconds", "cost", "cxt", "cost_normalized", "calls", "nav_calls",
           "json_calls", "cached_share", "nav_p50", "json_p50", "saturated_s", "hedge_pairs", "evidence_done",
           "submit_phase_s"]


def table(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c)) for c in COLUMNS) + " |")
    return "\n".join(lines)


def decide(pairs: list[tuple[dict, dict]]) -> tuple[str, dict]:
    for base, cand in pairs:
        for run in (base, cand):
            if run["failure_class"] or run["quality"] is None:
                return "INVALID", {"repeat": run["run"]}
    for base, cand in pairs:
        if not cand["eligible"] or not cand["has_answer"] or (cand["quality"] or 0) < (base["quality"] or 0):
            return "REJECT", {"reason": f"candidate {cand['run']} lost quality or eligibility"}
    ratio = lambda key: [c[key] / b[key] for b, c in pairs if b[key]]
    cxt, cost, seconds = ratio("cxt"), ratio("cost"), ratio("seconds")
    detail = {"cxt_ratios": [round(r, 3) for r in cxt], "median_cxt": round(statistics.median(cxt), 3),
              "median_cost": round(statistics.median(cost), 3), "median_time": round(statistics.median(seconds), 3)}
    wins = sum(1 for r in cxt if r < 1)
    if detail["median_cxt"] <= 0.95 and wins >= math.ceil(2 * len(cxt) / 3):
        return "ADOPT", detail
    if detail["median_cxt"] <= 1.05 and detail["median_cost"] <= 0.97 and detail["median_time"] <= 1.02:
        return "ADOPT-COST", detail
    if detail["median_cxt"] > 1.05:
        return "REJECT", detail
    return "INCONCLUSIVE", detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--pairs", nargs="*", default=[], help="BASE_RUN:CANDIDATE_RUN")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    tok = token()
    if args.pairs:
        pairs = [tuple(metrics(r, tok) for r in pair.split(":")) for pair in args.pairs]
        print(table([run for pair in pairs for run in pair]))
        verdict, detail = decide(pairs)
        print(f"\nDECISION: {verdict} {json.dumps(detail)}")
        return 0
    rows = [metrics(r, tok) for r in args.runs]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(table(rows))
        if len(rows) > 1:
            for key in ("seconds", "cost", "cxt"):
                print(f"median {key}: {statistics.median(r[key] for r in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

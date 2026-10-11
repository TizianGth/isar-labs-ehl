"""Run metrics, written as a few `run_summary` lines just before the process exits.

The platform keeps only the last 256 lines of a run's stdout, so the beginning of every sandbox run is missing from its
log. What is needed to see where the time went (phase marks, missions, model slots, prompt sizes, browser waits) is
collected here instead and summarized at the end. Nothing in here changes the agent's behaviour.
"""
import json

from .log import elapsed

MARKS: dict[str, float] = {}       # first time (s since start) of a named moment, e.g. brief_done, first:requirements
COUNTERS: dict[str, float] = {}    # sums and counts, e.g. slot_wait_s:nav, read_url_s, guards:<site>
MISSIONS: dict[str, dict] = {}     # mission tag -> steps, tools, timing, prompt sizes


def mark(name: str) -> None:
    MARKS.setdefault(name, round(elapsed(), 1))


def add(key: str, value: float = 1.0) -> None:
    COUNTERS[key] = round(COUNTERS.get(key, 0.0) + value, 2)


def mission(tag: str) -> dict:
    return MISSIONS.setdefault(tag, {"start": round(elapsed(), 1), "end": None, "status": None, "steps": 0,
                                     "calls": 0, "tools": {}, "background": 0, "prompt_chars": {}})


def step(tag: str, tool: str) -> None:
    stats = mission(tag)
    stats["steps"] += 1
    stats["tools"][tool] = stats["tools"].get(tool, 0) + 1


def prompt(tag: str, **parts: str) -> None:
    """Sizes of the parts of one model prompt (characters), to see what the tokens are spent on."""
    stats = mission(tag)
    stats["calls"] += 1
    for name, text in parts.items():
        stats["prompt_chars"][name] = stats["prompt_chars"].get(name, 0) + len(text or "")


def ended(tag: str, status: str) -> None:
    stats = mission(tag)
    stats["end"], stats["status"] = round(elapsed(), 1), status


def summary_lines(usage: dict | None = None, limit: int = 3500) -> list[dict]:
    """The run in a few log lines, each below the logger's 4000-character cap."""
    lines = [{"part": "timeline", "marks": dict(sorted(MARKS.items(), key=lambda kv: kv[1]))},
             {"part": "counters", "counters": COUNTERS, "usage": usage or {}}]
    chunk: dict = {}
    for tag, m in MISSIONS.items():
        calls = max(1, m["calls"])
        compact = {"start": m["start"], "end": m["end"], "status": m["status"], "steps": m["steps"], "tools": m["tools"],
                   "background": m["background"],
                   "prompt_chars_avg": {k: round(v / calls) for k, v in m["prompt_chars"].items()}}
        if chunk and len(json.dumps({"part": "missions", "missions": {**chunk, tag: compact}}, ensure_ascii=False)) > limit:
            lines.append({"part": "missions", "missions": chunk})
            chunk = {}
        chunk[tag] = compact
    if chunk:
        lines.append({"part": "missions", "missions": chunk})
    return lines


def reset() -> None:
    MARKS.clear()
    COUNTERS.clear()
    MISSIONS.clear()

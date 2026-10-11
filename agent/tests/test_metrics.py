"""O-1: the end-of-run summary keeps what the platform's short log retention would lose (synthetic data only)."""
import asyncio
import json
import time

from rfq import llm, metrics
from rfq.agent.explorer import Explorer, Mission


def test_summary_lines_stay_below_the_log_cap_and_marks_keep_the_first_time():
    metrics.reset()
    metrics.mark("brief_done")
    first = metrics.MARKS["brief_done"]
    time.sleep(0.02)
    metrics.mark("brief_done")
    assert metrics.MARKS["brief_done"] == first
    for i in range(40):
        tag = f"erp:G-{i}@erp"
        metrics.prompt(tag, intro="x" * 900, history="y" * 3000, observation="z" * 5000)
        for tool in ("click", "extract_urls", "wait_for", "open_url", "fill_fields", "type", "extract"):
            metrics.step(tag, tool)
        metrics.ended(tag, "complete")
    lines = metrics.summary_lines(usage={"calls": 250})
    assert {line["part"] for line in lines} == {"timeline", "counters", "missions"}
    assert all(len(json.dumps(line, ensure_ascii=False)) < 3900 for line in lines)
    missions = {tag: m for line in lines if line["part"] == "missions" for tag, m in line["missions"].items()}
    assert len(missions) == 40 and missions["erp:G-7@erp"]["prompt_chars_avg"]["history"] == 3000
    metrics.reset()


def test_slot_wait_is_measured_per_lane(monkeypatch):
    metrics.reset()

    class _Completions:
        async def create(self, **kwargs):
            message = type("M", (), {"content": "{}", "tool_calls": []})()
            return type("R", (), {"choices": [type("C", (), {"message": message})()], "usage": None})()

    monkeypatch.setattr(llm, "client", lambda: type("Client", (), {"chat": type("Chat", (), {"completions": _Completions()})()})())

    async def scenario():
        slots = asyncio.Semaphore(1)
        monkeypatch.setattr(llm, "_slots", slots)
        await slots.acquire()                                   # another call holds the only slot for 0.3 s
        asyncio.get_running_loop().call_later(0.3, slots.release)
        await llm.chat([{"role": "user", "content": "x"}], deadline=time.time() + 10)
    asyncio.run(scenario())
    assert metrics.COUNTERS["slot_wait_s:json"] >= 0.25 and metrics.COUNTERS["calls:json"] == 1
    metrics.reset()


class _Site:
    site_id, url = "shop", "http://shop.test"

    def __init__(self):
        self.page = type("P", (), {"url": "http://shop.test/a"})()

    async def wait_started(self):
        pass

    async def observe(self, limit=0):
        return "URL: http://shop.test/a\n[e1] link \"Weiter\" -> /b"

    async def act(self, name, args):
        return "clicked"


def test_explorer_records_steps_tools_and_prompt_sizes(monkeypatch):
    metrics.reset()
    calls = [("click", {"ref": "e1"}), ("finish", {"summary": "done"})]

    async def chat(messages, **kwargs):
        return {"content": "", "tool_calls": [calls.pop(0)]}
    monkeypatch.setattr(llm, "chat", chat)
    mission = Mission("probe", "goal", {}, max_steps=5, deadline=time.time() + 60)
    explorer = asyncio.run(Explorer(_Site(), mission, None).run())
    stats = metrics.MISSIONS["probe@shop"]
    assert explorer.status == "finished" and stats["status"] == "finished" and stats["end"] is not None
    assert stats["tools"] == {"click": 1} and stats["calls"] == 2
    assert stats["prompt_chars"]["observation"] > 0 and "history" in stats["prompt_chars"]
    metrics.reset()

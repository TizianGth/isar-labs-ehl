"""Your agent's entry point: agent.json runs `uv run --no-sync python agent.py`, in the sandbox and on your machine.

The kit only does the plumbing: run.py reads the run and submits an answer, browsers.py attaches to the run's
browsers, models.py calls the hosted Qwen and Clef. Everything else is yours to build, in `solve` below.
Docs: $API/docs/building-an-agent.md, $API/docs/run-contract.md, and the rules: $API/docs/fair-play.md
"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

import browsers
import models  # noqa: F401  models.ask_qwen, models.ask_clef: see models.py
import run as contract


async def solve(task: dict, page) -> dict | None:
    """TODO: your agent goes here.

    `task` is the run: task["brief"] (the task and its rules), task["access"] (each website's url and login),
    task["submission"] (where the answer goes) and task["answer_schema"]. `page` is a Playwright page in browser 1.

    For an "api" submission, return the answer and main() submits it. For a "site" submission, enter the answer on
    that website yourself, then return None.

    Mind the clock: a scored run has 20 minutes, and `contract.seconds_left(task)` says how many are left. Every part
    of the answer scores on its own, so stop and submit your best answer about a minute before the end; an answer
    that comes too late scores 0.
    """
    return None


async def main() -> int:
    task, token = contract.load_run()
    sites = ", ".join(f"{site.get('siteId')} {site.get('url')}" for site in task["access"])
    print(f"Run {task.get('run_id') or '(local: the scouting logins, nothing counts)'}; websites: {sites}; "
          f"answer via {task['submission']['kind']}", flush=True)
    async with async_playwright() as playwright:
        browser, page = await browsers.connect(playwright)  # more browsers: browsers.connect(playwright, 1), ...
        answer = await solve(task, page)
    if answer is None:
        print("No answer to submit: build your agent in solve() in agent.py", flush=True)
        return 0
    if not token:
        print("Your answer (locally nothing is submitted):", json.dumps(answer, ensure_ascii=False), flush=True)
        return 0
    status, body = contract.submit_answer(task, token, answer)
    print(f"Answer: HTTP {status} {body[:2000]}", flush=True)
    return 0 if status == 202 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

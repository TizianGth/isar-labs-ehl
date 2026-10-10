"""Live smoke test of agent/rfq/tools against the scouting sites (dev only).

usage: set -a; source .env.local; set +a
       uv run python dev/scout/smoke_tools.py [siteId] ['tool args-json' ...]
e.g.   uv run python dev/scout/smoke_tools.py atira-rfq-erp
       uv run python dev/scout/smoke_tools.py atira-rfq-erp 'observe {}' 'set_range {"ref":"e12"}'
Without tool steps it only opens the start page (exercises login, cookie wall, overlays, error retry).
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parents[2] / "agent"))
from rfq.tools.actions import BrowserTools, call_tool  # noqa: E402
from rfq.tools.session import SiteSession, open_context  # noqa: E402

STATE = Path(__file__).parent / "state"


async def main():
    run = json.loads(os.environ["CHALLENGE_SCOUTING"])
    access = run["access"]
    sites = list(access) if isinstance(access, dict) else [a["siteId"] for a in access]
    get = (lambda k: access[k]) if isinstance(access, dict) else (lambda k: next(a for a in access if a["siteId"] == k))
    wanted = sys.argv[1:2] or sites
    steps = sys.argv[2:]
    async with async_playwright() as pw:
        for i, site in enumerate(wanted):
            browser, ctx, _ = await open_context(pw, i)
            state = STATE / f"tools_{site}.json"
            if state.exists():
                await ctx.add_cookies(json.loads(state.read_text())["cookies"])
            s = await SiteSession.create(site, get(site), ctx, log=lambda e: None)
            t = BrowserTools(s)
            await s.page.goto(s.base_url, wait_until="domcontentloaded")
            print(f"########## {site}\n" + (await t.observe())[:5000])
            for step in steps:
                name, _, args = step.partition(" ")
                print(f"\n>>> {step}\n" + (await call_tool(t, name, json.loads(args or "{}")))[:6000])
            STATE.mkdir(exist_ok=True)
            state.write_text(json.dumps(await ctx.storage_state()))
            await browser.close()


asyncio.run(main())

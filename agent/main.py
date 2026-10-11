"""Entry point (agent.json runs `uv run --no-sync python main.py`), in the sandbox and locally.

Sandbox: attach to the run's browsers (CDP_URLS, one website per browser); never start browsers of our own.
Locally (no CDP_URLS): launch one Chromium with one context per website. Options:
  --out FILE   write answer, trace and evidence as JSON      --dry-run   stop before the binding submission
  --evidence FILE   (dev) skip exploration, replay brief + evidence of an earlier --out file
"""
import argparse
import asyncio
import json
import os
import sys
import time

from playwright.async_api import async_playwright

from rfq import contract
from rfq.agent.orchestrator import Orchestrator
from rfq.browser.site import Site
from rfq.log import log
from rfq.profile import span


def cdp_urls() -> list[str]:
    try:
        urls = [u for u in json.loads(os.environ.get("CDP_URLS") or "[]") if isinstance(u, str)]
    except ValueError:
        urls = []
    return urls or ([os.environ["CDP_URL"]] if os.environ.get("CDP_URL") else [])


async def open_sites(playwright, access: list[dict]) -> tuple[dict[str, Site], list]:
    urls, browsers, contexts = cdp_urls(), [], []
    if urls:
        browsers = list(await asyncio.gather(*(playwright.chromium.connect_over_cdp(url) for url in urls)))
        for index in range(len(access)):
            browser = browsers[index % len(browsers)]
            if index < len(browsers) and browser.contexts:
                contexts.append(browser.contexts[0])          # the sandbox's own window, recorded by the platform
            else:
                contexts.append(await browser.new_context(viewport={"width": 1280, "height": 800}))
    else:
        browser = await playwright.chromium.launch(headless=os.environ.get("HEADLESS", "1") == "1")
        browsers.append(browser)
        for _ in access:
            contexts.append(await browser.new_context(viewport={"width": 1280, "height": 800}, locale="de-DE"))
    sites = {a["siteId"]: Site(a["siteId"], a["url"], a["credentials"], ctx) for a, ctx in zip(access, contexts)}
    return sites, browsers


STARTED = time.time()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--evidence", help="dev only: replay brief + evidence from an earlier --out file")
    args = parser.parse_args()
    if args.dry_run:
        os.environ["RFQ_DRY_RUN"] = "1"
    run, token = contract.load_run()
    log("run", run_id=run.get("run_id"), local=token is None, seconds_left=round(contract.seconds_left(run)),
        sites=[a["siteId"] for a in run["access"]], submission=run.get("submission", {}).get("kind"))
    async with async_playwright() as playwright:
        with span("browser_attach", agent="orchestrator", category="startup"):
            sites, browsers = await open_sites(playwright, run["access"])
        for site in sites.values():
            site.start_task()
        orchestrator = Orchestrator(run, sites, started=STARTED)
        replay = json.load(open(args.evidence)) if args.evidence else None
        try:   # last line of defence: the run ends in time even if something hangs
            result = await asyncio.wait_for(orchestrator.run_all(replay), timeout=max(.1, orchestrator.end - time.time()))
        except asyncio.TimeoutError:
            log("watchdog_timeout")
            result = orchestrator.result
        finally:
            await orchestrator.source_reads.close()
        if args.out:
            with open(args.out, "w") as handle:
                json.dump({"answer": result.answer if result else None, "skipped": result.skipped if result else None,
                           "trace": result.trace if result else None, "brief": orchestrator.brief,
                           "status": orchestrator.status, "erp_complete": sorted(orchestrator.erp_complete),
                           "evidence": orchestrator.bb.dump(), "source_evidence": orchestrator.source_evidence,
                           "coverage": {key: vars(value) for key, value in orchestrator.coverage.items()}}, handle, ensure_ascii=False, indent=2, default=str)
        pending = [site._start_task for site in sites.values() if site._start_task and not site._start_task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if not cdp_urls():
            for browser in browsers:
                await browser.close()
    return 0


if __name__ == "__main__":
    started = time.time()
    try:
        code = asyncio.run(main())
    except Exception as error:   # last resort: log and exit cleanly
        log("fatal", error=f"{type(error).__name__}: {error}"[:1000])
        code = 1
    log("exit", code=code, seconds=round(time.time() - started, 1))
    sys.exit(code)

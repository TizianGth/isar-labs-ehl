"""The run's browsers: attach over CDP and get a page. Driving them is up to you.

In a run the sandbox starts the browsers (agent.json "browsers": 1 to 4, default 1) before the clock starts and sets
CDP_URL (browser 1) and CDP_URLS (all of them, as a JSON array). Use those: the sandbox stops browsers your agent
starts itself. On your own machine there is no CDP_URLS, so `connect` starts a local Chromium (once:
`uv run playwright install chromium`; HEADLESS=1 hides it). Docs: $API/docs/building-an-agent.md
"""
import json
import os


def cdp_urls(env=os.environ) -> list[str]:
    """Every browser of the run, in order; [] on your own machine."""
    try:
        urls = [url for url in json.loads(env.get("CDP_URLS") or "[]") if isinstance(url, str)]
    except ValueError:
        urls = []
    return urls or ([env["CDP_URL"]] if env.get("CDP_URL") else [])


async def connect(playwright, number: int = 0):
    """Browser `number` of the run (0 is browser 1) and a page in it. Locally, a Chromium of its own."""
    urls = cdp_urls()
    if urls:
        browser = await playwright.chromium.connect_over_cdp(urls[number])
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
    else:
        browser = await playwright.chromium.launch(headless=os.environ.get("HEADLESS") == "1")
        context = await browser.new_context()
    page = context.pages[0] if context.pages else await context.new_page()
    return browser, page

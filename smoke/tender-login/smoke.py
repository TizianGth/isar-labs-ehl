"""Test-only Atira portal smoke check; deliberately does not create or submit a bid."""
import asyncio
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from playwright.async_api import async_playwright


def http_json(url: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "atira-login-smoke/1"}
    if token:
        headers["x-api-key"] = token
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise RuntimeError(f"Could not load run context: {error}") from error


def load_run() -> dict:
    token = os.environ.get("CHALLENGE_RUN_TOKEN")
    if token:
        api = os.environ.get("CHALLENGE_API_URL", "https://ehl-challenge.atira.ai").rstrip("/")
        return http_json(api + "/v1/runs/current", token)
    raw = os.environ.get("CHALLENGE_SCOUTING")
    if raw:
        try:
            return json.loads(raw)
        except ValueError as error:
            raise RuntimeError("CHALLENGE_SCOUTING is not valid JSON") from error
    raise RuntimeError("No run context; expected CHALLENGE_RUN_TOKEN or CHALLENGE_SCOUTING")


def access_for(task: dict, site_id: str) -> tuple[str, str, str]:
    for site in task.get("access", []):
        if site.get("siteId") == site_id:
            credentials = site.get("credentials") or {}
            username = credentials.get("username") or credentials.get("user")
            password = credentials.get("password")
            if site.get("url") and username and password:
                return site["url"].rstrip("/"), username, password
    raise RuntimeError(f"Run context has no complete access entry for {site_id}")


async def main() -> int:
    task = load_run()
    url, username, password = access_for(task, "tender-portal")
    urls = json.loads(os.environ.get("CDP_URLS", "[]"))
    cdp_url = os.environ.get("CDP_URL") or (urls[0] if urls else None)
    if not cdp_url:
        raise RuntimeError("No sandbox browser endpoint (CDP_URL/CDP_URLS) was provided")

    print("Smoke test: opening tender portal with this run's credentials; no bid will be created.", flush=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print(f"Portal navigation: HTTP {response.status if response else 'unknown'}; title={await page.title()!r}", flush=True)

        if await page.get_by_label("Benutzername").count():
            await page.get_by_label("Benutzername").fill(username)
            await page.get_by_label("Passwort").fill(password)
            await page.get_by_role("button", name="Anmelden").click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)

        if await page.get_by_role("button", name="Notwendige Cookies akzeptieren").count():
            await page.get_by_role("button", name="Notwendige Cookies akzeptieren").click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)

        await page.get_by_label("Suchbegriff").wait_for(state="visible", timeout=30000)
        body_text = await page.locator("body").inner_text(timeout=15000)
        if "Ausschreibungen suchen" not in body_text:
            raise RuntimeError("Login did not reach the tender search page")
        await page.get_by_label("Suchbegriff").wait_for(state="visible", timeout=15000)
        print("Login check: passed; authenticated tender search is ready.", flush=True)

    # A smoke-only run intentionally has no bid answer; test_run logs the no-answer end state.
    print("Smoke test complete; intentionally exiting without a bid answer.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as error:
        print(f"Smoke test failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        sys.exit(1)

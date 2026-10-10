"""One browser session per website: connection, event handlers, login data, navigation memory.

In the sandbox the browsers come from CDP_URLS (never launch our own there); locally a headless Chromium is started.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, BrowserContext, Dialog, Page, Playwright, Response

VIEWPORT = {"width": 1280, "height": 800}


def cdp_urls(env=os.environ) -> list[str]:
    urls = json.loads(env.get("CDP_URLS") or "[]")
    if not urls and env.get("CDP_URL"):
        urls = [env["CDP_URL"]]
    return urls


async def open_context(pw: Playwright, number: int, env=os.environ) -> tuple[Browser, BrowserContext, bool]:
    """(browser, context, remote). Remote browsers belong to the sandbox: disconnect, never close them."""
    urls = cdp_urls(env)
    if urls:
        browser = await pw.chromium.connect_over_cdp(urls[number % len(urls)])
        context = browser.contexts[0] if browser.contexts else await browser.new_context(viewport=VIEWPORT)
        return browser, context, True
    browser = await pw.chromium.launch(headless=env.get("HEADLESS", "1") == "1")
    context = await browser.new_context(viewport=VIEWPORT, locale="de-DE", accept_downloads=True)
    return browser, context, False


@dataclass
class SiteSession:
    name: str
    base_url: str
    credentials: dict
    context: BrowserContext
    page: Page
    log: callable = print
    events: list[str] = field(default_factory=list)       # guard/handler notes for the next tool result
    popups: list[Page] = field(default_factory=list)
    seen_urls: set[str] = field(default_factory=set)      # URLs the model has been shown (goto guard)
    overlay_choices: dict[str, str | None] = field(default_factory=dict)  # overlay text -> close button (Clef)
    last_good_url: str | None = None
    intended_url: str | None = None                       # where the model wanted to go (restored after relogin)
    last_status: int | None = None
    server_errors: int = 0
    logins: int = 0
    _accept_next_dialog: bool = False
    last_observation: str = ""

    @classmethod
    async def create(cls, name: str, access: dict, context: BrowserContext, log=print) -> "SiteSession":
        page = context.pages[0] if context.pages else await context.new_page()
        s = cls(name=name, base_url=access["url"].rstrip("/"), credentials=access.get("credentials") or {},
                context=context, page=page, log=log)
        s.seen_urls.update({s.base_url, s.base_url + "/", access["url"]})
        s._attach(page)
        context.on("page", s._on_popup)
        return s

    def _attach(self, page: Page) -> None:
        page.set_default_timeout(15_000)
        page.set_default_navigation_timeout(30_000)
        page.on("dialog", self._on_dialog)
        page.on("response", self._on_response)
        page.on("crash", lambda _: self.note("Seite abgestürzt — wird neu geöffnet"))

    # ---- handlers -------------------------------------------------------------------------------------------
    def note(self, msg: str) -> None:
        self.events.append(msg)
        self.log({"agent": self.name, "event": "browser", "msg": msg})

    async def _handle_dialog(self, dialog: Dialog) -> None:
        if self._accept_next_dialog:
            self._accept_next_dialog = False
            self.note(f"Browser-Dialog ({dialog.type}) bestätigt: {dialog.message[:200]}")
            await dialog.accept()
        else:
            self.note(f"Browser-Dialog ({dialog.type}) abgelehnt: {dialog.message[:200]} — "
                      "falls er bestätigt werden muss: confirm_next_dialog() und Aktion wiederholen")
            await dialog.dismiss()

    def _on_dialog(self, dialog: Dialog) -> None:
        asyncio.ensure_future(self._handle_dialog(dialog))

    def arm_dialog_accept(self) -> None:
        """Accept exactly the next native alert/confirm (used for the explicit final signing step)."""
        self._accept_next_dialog = True

    def _on_response(self, response: Response) -> None:
        try:
            if response.request.is_navigation_request() and response.frame == self.page.main_frame:
                self.last_status = response.status
                if response.status >= 500:
                    self.server_errors += 1
        except Exception:
            pass

    def _on_popup(self, page: Page) -> None:
        if page is self.page:
            return
        self.popups.append(page)
        self._attach(page)
        self.note(f"Neues Fenster geöffnet: {page.url or '(lädt)'} — switch_tab({len(self.popups)}) zum Wechseln")

    def take_events(self) -> list[str]:
        ev, self.events = self.events, []
        return ev

    # ---- navigation memory ----------------------------------------------------------------------------------
    def absolute(self, href: str) -> str:
        return urljoin(self.page.url if self.page.url.startswith("http") else self.base_url + "/", href)

    def same_site(self, url: str) -> bool:
        return urlparse(url).netloc == urlparse(self.base_url).netloc

    def remember(self, text: str) -> None:
        """Record every link target shown to the model; goto() only accepts these."""
        import re
        for href in re.findall(r"→ (\S+)", text):
            self.seen_urls.add(self.absolute(href).split("#")[0])

    async def ensure_page(self) -> Page:
        if self.page.is_closed():
            self.page = await self.context.new_page()
            self._attach(self.page)
            if self.last_good_url:
                await self.page.goto(self.last_good_url, wait_until="domcontentloaded")
        return self.page


def now() -> float:
    return time.monotonic()

"""One website in one browser: login, guards, observation and actions.

The model decides what to do; this class only makes each action robust: overlays are closed, cookie walls and
gates handled, expired sessions re-logged, flaky error pages retried, popups folded back into the tab, and every
action answers with a fresh observation instead of raising.
"""
import asyncio
import html
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import BrowserContext, Error as PlaywrightError, Frame, Page

from ..log import log

OBSERVE_JS = (Path(__file__).with_name("observe.js")).read_text()
GUARDS_JS = (Path(__file__).with_name("guards.js")).read_text()
MAX_OBSERVATION = 14000


class Site:
    def __init__(self, site_id: str, url: str, credentials: dict, context: BrowserContext):
        self.site_id, self.url, self.credentials, self.context = site_id, url.rstrip("/"), credentials, context
        self.page: Page | None = None
        self.origin = urlparse(self.url).netloc
        self.logins = 0
        self.seen_urls: set[str] = set()
        self.lock = asyncio.Lock()   # one action at a time per site page

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> str:
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self._wire(self.page)
        await self.goto(self.url)
        return await self.observe()

    def _wire(self, page: Page) -> None:
        page.on("dialog", lambda dialog: asyncio.ensure_future(self._on_dialog(dialog)))
        page.set_default_timeout(8000)
        page.set_default_navigation_timeout(30000)

    accept_next_dialog = False

    async def _on_dialog(self, dialog) -> None:
        log("native_dialog", site=self.site_id, type=dialog.type, message=dialog.message[:200],
            accept=self.accept_next_dialog)
        try:
            if self.accept_next_dialog:
                self.accept_next_dialog = False
                await dialog.accept()
            else:
                await dialog.dismiss()
        except PlaywrightError:
            pass

    async def _ensure_page(self) -> Page:
        if self.page is None or self.page.is_closed():
            self.page = await self.context.new_page()
            self._wire(self.page)
            await self.goto(self.url)
        return self.page

    # ---------------------------------------------------------------- navigation + guards
    async def goto(self, url: str, frame: Frame | None = None) -> None:
        page = self.page
        target = frame or page
        for attempt in range(4):
            try:
                response = await target.goto(url, wait_until="domcontentloaded")
                if response is not None and response.status >= 500:
                    log("http_error", site=self.site_id, status=response.status, url=_short(url))
                    await asyncio.sleep(0.7 * (attempt + 1))
                    continue
                break
            except PlaywrightError as error:
                log("goto_error", site=self.site_id, url=_short(url), error=str(error)[:200])
                await asyncio.sleep(0.7 * (attempt + 1))
        await self.settle()

    async def settle(self, rounds: int = 6) -> None:
        """Run the guards until the page is usable: error retry, login, cookie consent, overlays, gates."""
        page = await self._ensure_page()
        for _ in range(rounds):
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except PlaywrightError:
                pass
            state = await self._guard_state()
            if state.get("error_page"):
                log("guard_error_page", site=self.site_id, url=_short(page.url))
                await asyncio.sleep(0.8)
                retry = state.get("retry_href")
                await self._safe_goto(urljoin(page.url, retry) if retry else page.url)
                continue
            if state.get("login"):
                if await self._login():
                    continue
                return
            if state.get("consent"):
                log("guard_consent", site=self.site_id)
                await self._click_js("consent")
                await asyncio.sleep(0.3)
                continue
            closed = await self._run_guards_everywhere("close_overlays")
            unlocked = await self._run_guards_everywhere("unlock_gates")
            if closed or unlocked:
                log("guard_overlays", site=self.site_id, closed=closed, unlocked=unlocked)
            return

    async def _safe_goto(self, url: str) -> None:
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError:
            pass

    async def _guard_state(self) -> dict:
        try:
            return await self.page.evaluate(GUARDS_JS, {"op": "state"}) or {}
        except PlaywrightError:
            return {}

    async def _click_js(self, op: str) -> bool:
        try:
            return bool(await self.page.evaluate(GUARDS_JS, {"op": op}))
        except PlaywrightError:
            return False

    async def _run_guards_everywhere(self, op: str) -> int:
        count = 0
        for frame in self.page.frames:
            try:
                count += int(await frame.evaluate(GUARDS_JS, {"op": op}) or 0)
            except PlaywrightError:
                pass
        return count

    async def _login(self) -> bool:
        if self.logins >= 6:
            log("login_giving_up", site=self.site_id)
            return False
        self.logins += 1
        page = self.page
        log("guard_login", site=self.site_id, attempt=self.logins)
        try:
            password = page.locator("input[type=password]:visible").first
            form = password.locator("xpath=ancestor::form[1]")
            scope = form if await form.count() else page
            user = scope.locator("input:visible:not([type=password]):not([type=hidden]):not([type=checkbox])"
                                 ":not([type=submit]):not([type=button])").first
            await user.fill(self.credentials["username"])
            await password.fill(self.credentials["password"])
            submit = scope.locator("button[type=submit]:visible, input[type=submit]:visible, button:visible").first
            if await submit.count():
                await submit.click()
            else:
                await password.press("Enter")
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(0.4)
            return True
        except PlaywrightError as error:
            log("login_error", site=self.site_id, error=str(error)[:200])
            return False

    # ---------------------------------------------------------------- observation
    async def observe(self, limit: int = MAX_OBSERVATION) -> str:
        page = await self._ensure_page()
        parts = [f"URL: {page.url}"]
        for index, frame in enumerate(page.frames):
            if frame.is_detached():
                continue
            try:
                result = await frame.evaluate(OBSERVE_JS, {"prefix": "e" if index == 0 else f"f{index}e"})
            except PlaywrightError as error:
                parts.append(f"=== frame {index} {frame.url}: unreadable ({str(error)[:80]})")
                continue
            if index and not result["text"].strip():
                continue
            if index:
                parts.append(f"=== frame {index}: {frame.url} ===")
            else:
                parts.append(f"TITLE: {result['title']}")
            parts.append(result["text"])
            for match in re.finditer(r"-> (\S+)|src=(\S+?)[\] ]", result["text"]):
                self.seen_urls.add(urljoin(frame.url, html.unescape(match.group(1) or match.group(2))))
        text = "\n".join(parts)
        if len(text) > limit:
            text = text[:limit] + f"\n[… observation cut at {limit} chars; use read_all_pages/extract for full text]"
        return text

    async def full_text(self, follow_iframes: bool = True) -> str:
        """The whole page. Iframes that show nothing (e.g. an empty srcdoc hiding a real src) are loaded from their
        src in a helper tab, one level deep."""
        text = await self.observe(limit=200000)
        if not follow_iframes:
            return text
        shown = set(re.findall(r"^=== frame \d+: (\S+) ===$", text, re.M))   # frames that had content
        for src in dict.fromkeys(re.findall(r"\[iframe [^\]]*?src=(\S+?)(?: \(srcdoc\))?\]", text)):
            url = urljoin(self.page.url, src)
            if not src or url in shown or urlparse(url).netloc != self.origin:
                continue
            extra = await self.read_url(url, follow_iframes=False)
            text += f"\n=== content of iframe source {url} ===\n{extra}"
        return text

    def _locate(self, ref: str):
        page = self.page
        match = re.fullmatch(r"f(\d+)e\d+", ref)
        frame = page.frames[int(match.group(1))] if match and int(match.group(1)) < len(page.frames) else page.main_frame
        return frame, frame.locator(f'[data-agent-ref="{ref}"]')

    # ---------------------------------------------------------------- actions (never raise)
    async def act(self, name: str, args: dict) -> str:
        async with self.lock:
            try:
                await self._ensure_page()
                # Delayed popups (surveys, notices) may have opened since the last observation.
                await self._run_guards_everywhere("close_overlays")
                message = await getattr(self, f"_do_{name}")(**args)
            except (PlaywrightError, KeyError, TypeError, ValueError, IndexError) as error:
                message = f"ERROR: {name} failed: {str(error).splitlines()[0][:300]}"
            await self.settle()
            return message

    async def _element(self, ref: str):
        frame, locator = self._locate(ref)
        if not await locator.count():
            raise ValueError(f"ref {ref} is not on the current page (refs change after every action)")
        return frame, locator.first

    async def _do_click(self, ref: str) -> str:
        frame, el = await self._element(ref)
        tag = await el.evaluate("e => e.tagName")
        href = await el.get_attribute("href") if tag == "A" else None
        if href and not href.startswith(("#", "javascript:")):
            await self.goto(urljoin(frame.url, href), frame=None if frame == self.page.main_frame else frame)
            return f"opened {href}"
        pages_before = set(self.context.pages)
        try:
            await el.click(timeout=5000)
        except PlaywrightError:
            await self._run_guards_everywhere("close_overlays")
            try:
                await el.click(timeout=4000)
            except PlaywrightError:
                await el.dispatch_event("click")
        await asyncio.sleep(0.3)
        for popup in set(self.context.pages) - pages_before:   # fold popups back into our tab
            try:
                await popup.wait_for_load_state("domcontentloaded", timeout=8000)
                url = popup.url
                await popup.close()
                if url and url != "about:blank":
                    await self.goto(url)
                    return f"clicked; a popup window opened {url} and was loaded here instead"
            except PlaywrightError:
                pass
        return "clicked"

    async def _ready(self, el) -> str | None:
        """Make a form control usable (open collapsed <details> around it); explain why it is not."""
        await el.evaluate("e => { for (let d = e.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true; }")
        if await el.is_disabled():
            return ("ERROR: this field is disabled right now. Usually the previous choice must be applied first "
                    "(e.g. an 'apply/übernehmen' button), or a gate must be unlocked.")
        return None

    async def _do_type(self, ref: str, text: str, submit: bool = False) -> str:
        _, el = await self._element(ref)
        if problem := await self._ready(el):
            return problem
        await el.fill(str(text), timeout=4000)
        value = await el.input_value()
        if submit:
            await el.press("Enter")
            await asyncio.sleep(0.3)
        return f"typed; field now contains {value!r}" + (" and pressed Enter" if submit else "")

    async def _do_select(self, ref: str, option: str) -> str:
        _, el = await self._element(ref)
        if problem := await self._ready(el):
            return problem
        options = await el.evaluate("e => [...e.options].map(o => [o.value, o.textContent.trim()])")
        wanted = " ".join(str(option).split()).casefold()
        value = next((v for v, label in options if label.casefold() == wanted or v.casefold() == wanted), None)
        if value is None:
            hits = [v for v, label in options if wanted and wanted in label.casefold()]
            value = hits[0] if len(hits) == 1 else None
        if value is None:
            return f"ERROR: no option {option!r}. Options: " + " | ".join(label for _, label in options)
        await el.select_option(value=value, timeout=4000)
        return f"selected {value!r}"

    async def _do_check(self, ref: str, checked: bool = True) -> str:
        _, el = await self._element(ref)
        if problem := await self._ready(el):
            return problem
        kind = await el.evaluate("e => e.type")
        if kind not in ("checkbox", "radio"):
            return f"ERROR: {ref} is not a checkbox/radio (it is {kind}); click it instead"
        await el.set_checked(bool(checked), force=True, timeout=4000)
        return f"{'checked' if checked else 'unchecked'}"

    async def _do_fill_fields(self, fields: list) -> str:
        """Several form fields in one step: text inputs, selects and checkboxes, each verified."""
        results = []
        for field in fields or []:
            ref, value = str(field.get("ref")), field.get("value")
            try:
                _, el = await self._element(ref)
                tag, kind = await el.evaluate("e => [e.tagName, e.type]")
                if tag == "SELECT":
                    results.append(f"{ref}: " + await self._do_select(ref, str(value)))
                elif kind in ("checkbox", "radio"):
                    on = str(value).lower() not in ("false", "0", "no", "nein", "")
                    results.append(f"{ref}: " + await self._do_check(ref, on))
                else:
                    results.append(f"{ref}: " + await self._do_type(ref, str(value)))
            except (PlaywrightError, ValueError) as error:
                results.append(f"{ref}: ERROR {str(error).splitlines()[0][:150]}")
        return "; ".join(results)

    async def _do_set_slider(self, ref: str, value: str = "max") -> str:
        _, el = await self._element(ref)
        if str(value) == "max":
            value = await el.get_attribute("max") or "100"
        await el.fill(str(value))
        return f"slider set to {value}"

    async def _do_back(self) -> str:
        await self.page.go_back(wait_until="domcontentloaded")
        return "went back"

    async def _do_open_url(self, url: str) -> str:
        url = self.absolute(url)
        if urlparse(url).netloc != self.origin:
            return "ERROR: only URLs of this website can be opened"
        if not self.was_seen(url):
            return "ERROR: this address was not offered on any page you saw; navigate via the links shown instead"
        await self.goto(url)
        return f"opened {url}"

    async def _do_scroll(self, ref: str | None = None) -> str:
        if ref:
            _, el = await self._element(ref)
            await el.scroll_into_view_if_needed()
        else:
            await self.page.mouse.wheel(0, 700)
        return "scrolled"

    # ---------------------------------------------------------------- helpers for model-selected content
    async def spawn(self) -> "Site":
        """Another tab of this website in the same browser context (shares the login session)."""
        twin = Site(self.site_id, self.url, self.credentials, self.context)
        twin.seen_urls = self.seen_urls
        twin.page = await self.context.new_page()
        twin._wire(twin.page)
        await twin.goto(self.url)
        return twin

    def absolute(self, url: str) -> str:
        return urljoin(self.page.url, html.unescape(str(url).strip()))

    def was_seen(self, url: str) -> bool:
        """Only addresses that appeared as links/frames on observed pages may be opened directly: the model picks
        from what the website offers instead of guessing addresses."""
        key = url.split("#")[0].rstrip("/")
        return key == self.url or any(seen.split("#")[0].rstrip("/") == key for seen in self.seen_urls)

    async def read_url(self, url: str, follow_iframes: bool = True) -> str:
        """Open a model-selected URL of this site in a helper tab and return its full serialized text."""
        url = self.absolute(url)
        if urlparse(url).netloc != self.origin:
            return "ERROR: only URLs of this website can be read"
        helper = Site(self.site_id, self.url, self.credentials, self.context)
        helper.logins, helper.seen_urls = self.logins, self.seen_urls
        helper.page = await self.context.new_page()
        helper._wire(helper.page)
        try:
            await helper.goto(url)
            if helper.page.url.rstrip("/") != url.rstrip("/") and "login" not in url:
                await helper.goto(url)       # after a re-login the site may have dropped the target
            return await helper.full_text(follow_iframes)
        finally:
            await helper.page.close()

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(type="jpeg", quality=60)


def _short(url: str) -> str:
    return url[:160]

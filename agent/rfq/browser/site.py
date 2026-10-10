"""One website in one browser: login, guards, observation and actions.

The model decides what to do; this class only makes each action robust: overlays are closed, cookie walls and
gates handled, expired sessions re-logged, flaky error pages retried, popups folded back into the tab, a tab that
stopped answering replaced by a fresh one, and every action answers with a fresh observation instead of raising.
"""
import asyncio
import html
import inspect
import re
import weakref
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import BrowserContext, Error as PlaywrightError, Frame, Page

from ..log import log

OBSERVE_JS = (Path(__file__).with_name("observe.js")).read_text()
GUARDS_JS = (Path(__file__).with_name("guards.js")).read_text()
MAX_OBSERVATION = 14000
EVAL_TIMEOUT = 12
NAV_TIMEOUT = 15                  # seconds per navigation attempt (pages here load in well under a second)
MAX_TAB_REPLACEMENTS = 8          # per site and run
_STUCK: "weakref.WeakSet[Page]" = weakref.WeakSet()   # tabs whose document stopped answering


async def _eval(target, script: str, arg, timeout: float | None = None):
    """page/frame.evaluate with a time limit: a page stuck in navigation must never block the agent."""
    timeout = timeout or EVAL_TIMEOUT
    try:
        return await asyncio.wait_for(target.evaluate(script, arg), timeout)
    except asyncio.TimeoutError:
        page = target if isinstance(target, Page) else target.page
        if target is page or target is page.main_frame:     # the document itself, not just a slow iframe
            _STUCK.add(page)
        raise PlaywrightError(f"evaluate timed out after {timeout}s")


class Site:
    def __init__(self, site_id: str, url: str, credentials: dict, context: BrowserContext):
        self.site_id, self.url, self.credentials, self.context = site_id, url.rstrip("/"), credentials, context
        self.page: Page | None = None
        self.origin = urlparse(self.url).netloc
        self.logins = 0
        self.seen_urls: set[str] = set()
        self.collections: dict = {}             # read_all_pages results (shared by all missions on this site)
        self.visual_notes: dict[str, list[str]] = {}   # url -> answers read from screenshots
        self._frames: dict[int, Frame] = {}     # frame index of the last observation -> frame (refs stay valid)
        self.lock = asyncio.Lock()   # one action at a time per site page
        self.last_good_url = self.url           # the last page whose document answered
        self.replaced_tabs = 0
        self._notice = ""                        # told to the model with the next observation

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> str:
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self._wire(self.page)
        await self.goto(self.url)
        return await self.observe()

    def _wire(self, page: Page) -> None:
        page.on("dialog", lambda dialog: asyncio.ensure_future(self._on_dialog(dialog)))
        page.set_default_timeout(8000)
        page.set_default_navigation_timeout(NAV_TIMEOUT * 1000)

    accept_next_dialog = False

    async def _on_dialog(self, dialog) -> None:
        log("native_dialog", site=self.site_id, type=dialog.type, message=dialog.message[:200],
            accept=self.accept_next_dialog)
        try:
            if dialog.type == "beforeunload":   # dismissing it would block every navigation
                await dialog.accept()
            elif self.accept_next_dialog:
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
        elif self.page in _STUCK and self.replaced_tabs < MAX_TAB_REPLACEMENTS:
            await self._replace_stuck_tab()
        return self.page

    async def _replace_stuck_tab(self) -> None:
        """The tab stopped answering (a navigation that never completes, a frozen script). Every later action on it
        would time out too, so continue in a fresh tab at the last page that worked, and tell the model."""
        stuck_url = self.page.url
        self.replaced_tabs += 1
        log("tab_replaced", site=self.site_id, stuck_url=_short(stuck_url), resume=_short(self.last_good_url))
        self._notice = (f"NOTE: the tab stopped responding at {stuck_url} and was replaced by a fresh one at "
                        f"{self.last_good_url}. That page may be temporarily broken: retry it once or take another route.")
        await self._fresh_page()
        try:                                     # one attempt: an outage must not turn into minutes of retries
            await self.page.goto(self.last_good_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT * 1000)
        except PlaywrightError as error:
            log("goto_error", site=self.site_id, url=_short(self.last_good_url), error=str(error)[:200])
        await self.settle()

    # ---------------------------------------------------------------- navigation + guards
    async def _fresh_page(self) -> None:
        """Continue in a new tab when the current one is stuck (the old one is left alone, not closed)."""
        log("fresh_tab", site=self.site_id)
        self.page = await self.context.new_page()
        self._wire(self.page)

    async def goto(self, url: str, frame: Frame | None = None) -> bool:
        """Navigate, retrying flaky errors (a fresh tab after a failed attempt). False if the page never loaded: the
        tab is then marked stuck and continues at the last page that worked on the next step."""
        target, failed = frame or self.page, False
        for attempt in range(3):
            if attempt == 1 and frame is None:      # a failed navigation may leave the tab stuck: use a fresh one
                await self._fresh_page()
                target = self.page
            try:
                response = await target.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT * 1000)
                failed = False
                if response is not None and response.status >= 500:
                    log("http_error", site=self.site_id, status=response.status, url=_short(url))
                    await asyncio.sleep(0.7 * (attempt + 1))
                    continue
                break
            except PlaywrightError as error:
                failed = True
                log("goto_error", site=self.site_id, url=_short(url), error=str(error)[:200])
                await asyncio.sleep(0.7 * (attempt + 1))
        if failed and frame is None and self.page:
            _STUCK.add(self.page)
        await self.settle()
        return not failed

    async def settle(self, rounds: int = 6) -> None:
        """Run the guards until the page is usable: error retry, login, cookie consent, overlays, gates."""
        try:
            await asyncio.wait_for(self._settle(rounds), timeout=45)
        except asyncio.TimeoutError:
            log("settle_timeout", site=self.site_id, url=_short(self.page.url if self.page else ""))
            if self.page:
                _STUCK.add(self.page)            # the guards could not get an answer from this tab

    async def _settle(self, rounds: int) -> None:
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
            return await _eval(self.page, GUARDS_JS, {"op": "state"}) or {}
        except PlaywrightError:
            return {}

    async def _click_js(self, op: str) -> bool:
        try:
            return bool(await _eval(self.page, GUARDS_JS, {"op": op}))
        except PlaywrightError:
            return False

    async def _run_guards_everywhere(self, op: str) -> int:
        count = 0
        for frame in self.page.frames:
            try:
                count += int(await _eval(frame, GUARDS_JS, {"op": op}) or 0)
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
    async def observe(self, limit: int = MAX_OBSERVATION, _retried: bool = False) -> str:
        page = await self._ensure_page()
        parts = [f"URL: {page.url}"]
        self._frames = {}
        for index, frame in enumerate(page.frames):
            if frame.is_detached():
                continue
            self._frames[index] = frame
            try:
                result = await _eval(frame, OBSERVE_JS, {"prefix": "e" if index == 0 else f"f{index}e"})
            except PlaywrightError as error:
                parts.append(f"=== frame {index} {frame.url}: unreadable ({str(error)[:80]})")
                continue
            if index and not result["text"].strip():
                continue
            if index:
                parts.append(f"=== frame {index}: {frame.url} ===")
            else:
                parts.append(f"TITLE: {result['title']}")
                if urlparse(page.url).netloc == self.origin:
                    self.last_good_url = page.url
            parts.append(result["text"])
            for match in re.finditer(r"-> (\S+)|src=(\S+?)[\] ]", result["text"]):
                self.seen_urls.add(urljoin(frame.url, html.unescape(match.group(1) or match.group(2))))
        if page in _STUCK and not _retried and self.replaced_tabs < MAX_TAB_REPLACEMENTS:
            return await self.observe(limit, _retried=True)     # replaced now instead of showing a dead tab
        if self._notice:
            parts.insert(0, self._notice)
            self._notice = ""
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
        notes = self.visual_notes.get(self.page.url.split("#")[0])
        if notes:
            text += "\n=== read from a screenshot of this page (visual) ===\n" + "\n".join(notes)
        return text

    def note_visual(self, url: str, note: str) -> None:
        self.visual_notes.setdefault(url.split("#")[0], []).append(note)

    def _locate(self, ref: str):
        page = self.page
        match = re.fullmatch(r"f(\d+)e\d+", ref)
        frame = page.main_frame
        if match:
            index = int(match.group(1))
            frame = self._frames.get(index)
            if frame is None or frame.is_detached():
                frame = page.frames[index] if index < len(page.frames) else page.main_frame
        return frame, frame.locator(f'[data-agent-ref="{ref}"]')

    # ---------------------------------------------------------------- actions (never raise)
    async def act(self, name: str, args: dict) -> str:
        async with self.lock:
            try:
                await self._ensure_page()
                # Delayed popups (surveys, notices) may have opened since the last observation.
                await self._run_guards_everywhere("close_overlays")
                method = getattr(self, f"_do_{name}")
                accepted = inspect.signature(method).parameters
                message = await asyncio.wait_for(
                    method(**{k: v for k, v in args.items() if k in accepted and v not in (None, "")}), timeout=60)
            except asyncio.TimeoutError:
                message = f"ERROR: {name} did not finish within 60 s"
                _STUCK.add(self.page)                # replaced on the next action or observation
            except (PlaywrightError, KeyError, TypeError, ValueError, IndexError) as error:
                message = f"ERROR: {name} failed: {str(error).splitlines()[0][:300]}"
            await self.settle()
            return message

    async def _element(self, ref: str):
        ref = re.sub(r"[^\w]", "", str(ref))          # models sometimes send '"e9"' or '[e9]'
        frame, locator = self._locate(ref)
        if not await locator.count():
            raise ValueError(f"ref {ref} is not on the current page (refs change after every action)")
        return frame, locator.first

    async def _do_click(self, ref: str) -> str:
        frame, el = await self._element(ref)
        tag = await el.evaluate("e => e.tagName")
        href = await el.get_attribute("href") if tag == "A" else None
        if href and not href.startswith(("#", "javascript:")):
            if await self.goto(urljoin(frame.url, href), frame=None if frame == self.page.main_frame else frame):
                return f"opened {href}"
            return f"ERROR: {href} did not load (the website did not answer in time); retry once or take another route"
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
        folded = await self._fold_popups(pages_before)
        return "clicked" + (f"; {folded}" if folded else "")

    async def _fold_popups(self, pages_before: set) -> str:
        """A new window (window.open, target=_blank) is loaded in our tab instead, so the model keeps one view."""
        for popup in set(self.context.pages) - pages_before:
            try:
                await popup.wait_for_load_state("domcontentloaded", timeout=8000)
                url = popup.url
                await popup.close()
                if url and url != "about:blank":
                    await self.goto(url)
                    return f"a popup window opened {url} and was loaded here instead"
            except PlaywrightError:
                pass
        return ""

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

    async def _do_select(self, ref: str, option: str, then_click: str | None = None) -> str:
        result = await self._select(ref, option, hint=not then_click)
        return result + ("" if result.startswith("ERROR") else await self._then_click(then_click))

    async def _select(self, ref: str, option: str, hint: bool = False) -> str:
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
        hint = hint and await el.evaluate("""e => { const f = e.form || e.closest('form') || document;
            return [...f.querySelectorAll('select:disabled, input:disabled')].length; }""")
        return f"selected {value!r}" + (" (other fields of this form are still disabled: apply the choice with the "
                                        "form's apply/update button, e.g. via then_click)" if hint else "")

    async def _do_check(self, ref: str, checked: bool = True) -> str:
        _, el = await self._element(ref)
        if problem := await self._ready(el):
            return problem
        kind = await el.evaluate("e => e.type")
        if kind not in ("checkbox", "radio"):
            return f"ERROR: {ref} is not a checkbox/radio (it is {kind}); click it instead"
        await el.set_checked(bool(checked), force=True, timeout=4000)
        return f"{'checked' if checked else 'unchecked'}"

    async def _then_click(self, ref: str | None) -> str:
        if not ref:
            return ""
        return "; then " + await self._do_click(str(ref))

    async def _do_fill_fields(self, fields: list, then_click: str | None = None) -> str:
        """Several form fields in one step: text inputs, selects and checkboxes, each verified."""
        results = []
        for field in fields or []:
            ref, value = str(field.get("ref")), field.get("value")
            try:
                _, el = await self._element(ref)
                tag, kind = await el.evaluate("e => [e.tagName, e.type]")
                if tag == "SELECT":
                    results.append(f"{ref}: " + await self._select(ref, str(value)))
                elif kind in ("checkbox", "radio"):
                    on = str(value).lower() not in ("false", "0", "no", "nein", "")
                    results.append(f"{ref}: " + await self._do_check(ref, on))
                else:
                    results.append(f"{ref}: " + await self._do_type(ref, str(value)))
            except (PlaywrightError, ValueError) as error:
                results.append(f"{ref}: ERROR {str(error).splitlines()[0][:150]}")
        return "; ".join(results) + await self._then_click(then_click)

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
        if await self.goto(url):
            return f"opened {url}"
        return f"ERROR: {url} did not load (the website did not answer in time); retry once or take another route"

    async def _do_scroll(self, ref: str | None = None, direction: str = "down", amount: int = 600) -> str:
        delta = abs(int(amount or 600)) * (-1 if direction == "up" else 1)
        if ref:
            frame, el = await self._element(ref)
            if await el.evaluate("e => e.tagName") in ("IFRAME", "FRAME"):
                inner = await el.content_frame()
                if inner:
                    await inner.evaluate(f"window.scrollBy(0, {delta})")
                    return f"scrolled inside the frame by {delta}px"
            moved = await el.evaluate(f"e => {{ const before = e.scrollTop; e.scrollBy(0, {delta}); return e.scrollTop - before; }}")
            if not moved:
                await el.scroll_into_view_if_needed()
            return f"scrolled the element by {moved}px" if moved else "scrolled the element into view"
        await self.page.mouse.wheel(0, delta)
        return f"scrolled the page by {delta}px"

    async def _do_hover(self, ref: str) -> str:
        _, el = await self._element(ref)
        await el.hover(timeout=4000)
        await asyncio.sleep(0.3)
        return "hovering (menus/tooltips that open on hover are now shown)"

    async def _do_press_key(self, key: str, ref: str | None = None) -> str:
        pages_before = set(self.context.pages)
        if ref:
            _, el = await self._element(ref)
            await el.press(str(key), timeout=4000)
        else:
            await self.page.keyboard.press(str(key))
        await asyncio.sleep(0.4)
        folded = await self._fold_popups(pages_before)
        return f"pressed {key}" + (f"; {folded}" if folded else "")

    async def _do_wait_for(self, condition: str, ref: str | None = None, timeout_s: float = 6) -> str:
        """Short, bounded wait for an observable condition (never a blind sleep)."""
        condition = str(condition or "change").strip()
        limit = asyncio.get_running_loop().time() + max(0.5, min(float(timeout_s or 6), 10))
        before = await _eval(self.page, "() => document.body ? document.body.innerText.length : 0", None)
        while asyncio.get_running_loop().time() < limit:
            try:
                if condition in ("enabled", "visible") and ref:
                    _, el = await self._element(ref)
                    if (condition == "enabled" and await el.is_enabled()) or (condition == "visible" and await el.is_visible()):
                        return f"condition '{condition}' met"
                elif condition.startswith("text:"):
                    words = condition[5:].strip().lower()
                    if words and words in (await _eval(self.page, "() => document.body.innerText", None)).lower():
                        return f"text '{words}' appeared"
                elif condition == "change":
                    now = await _eval(self.page, "() => document.body ? document.body.innerText.length : 0", None)
                    if now != before:
                        return "the page changed"
            except (PlaywrightError, ValueError):
                pass
            await asyncio.sleep(0.3)
        return f"condition '{condition}' not met within the time limit"

    # ---------------------------------------------------------------- helpers for model-selected content
    async def spawn(self) -> "Site":
        """Another tab of this website in the same browser context (shares the login session)."""
        twin = Site(self.site_id, self.url, self.credentials, self.context)
        twin.seen_urls, twin.collections, twin.visual_notes = self.seen_urls, self.collections, self.visual_notes
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

    async def screenshot(self, ref: str | None = None) -> bytes:
        """JPEG of an element (if it has a size) or of the viewport."""
        if ref:
            try:
                _, el = await self._element(ref)
                box = await el.bounding_box()
                if box and box["width"] > 4 and box["height"] > 4:
                    return await el.screenshot(type="jpeg", quality=70, timeout=5000)
            except (PlaywrightError, ValueError):
                pass
        return await self.page.screenshot(type="jpeg", quality=60)


def _short(url: str) -> str:
    return url[:160]

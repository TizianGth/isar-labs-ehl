"""One website in one browser: login, guards, observation and actions.

The model decides what to do; this class only makes each action robust: overlays are closed, cookie walls and
gates handled, expired sessions re-logged, flaky error pages retried, popups folded back into the tab, and every
action answers with a fresh observation instead of raising.
"""
import asyncio
import html
import inspect
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import BrowserContext, Error as PlaywrightError, Frame, Page

from .. import metrics
from ..log import log

OBSERVE_JS = (Path(__file__).with_name("observe.js")).read_text()
GUARDS_JS = (Path(__file__).with_name("guards.js")).read_text()
MAX_OBSERVATION = 14000
EVAL_TIMEOUT = 12
NAVIGATION_TIMEOUT_MS = 20000
LOADED_TIMEOUT_MS = 12000
RESPONSIVE_TIMEOUT = 4


async def _eval(target, script: str, arg, timeout: float | None = None):
    """page/frame.evaluate with a time limit: a page stuck in navigation must never block the agent."""
    timeout = timeout or EVAL_TIMEOUT
    try:
        return await asyncio.wait_for(target.evaluate(script, arg), timeout)
    except asyncio.TimeoutError:
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
        self.last_good_url: str | None = None   # last address that was observed successfully
        self.hung: dict[str, int] = {}          # address -> navigations that never arrived (do not insist)
        self._recoveries = 0
        self.lock = asyncio.Lock()   # one action at a time per site page
        self.last_observation = ""
        self._starting: asyncio.Future | None = None

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> str:
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self._wire(self.page)
        await self.goto(self.url)
        return await self.observe()

    def begin_start(self, timeout: float) -> None:
        """Start loading the website in the background: work that does not need it (the brief, other websites)
        goes on meanwhile, and each mission waits only for its own website (`wait_started`)."""
        async def starting():
            try:
                await asyncio.wait_for(self.start(), timeout=timeout)
                log("site_ready", site=self.site_id, ok=True)
            except Exception as error:      # a site that cannot be reached at once: missions retry it later
                log("site_ready", site=self.site_id, ok=False, error=f"{type(error).__name__}: {error}"[:200])
        self._starting = asyncio.ensure_future(starting())

    async def wait_started(self) -> None:
        if self._starting is not None:
            await asyncio.shield(self._starting)

    def _wire(self, page: Page) -> None:
        page.on("dialog", lambda dialog: asyncio.ensure_future(self._on_dialog(dialog)))
        page.set_default_timeout(8000)
        page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

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
        return self.page

    # ---------------------------------------------------------------- navigation + guards
    async def _fresh_page(self) -> None:
        """Continue in a new tab of the same session; the old tab is closed in the background."""
        log("fresh_tab", site=self.site_id)
        old = self.page
        self.page = await self.context.new_page()
        self._wire(self.page)
        if old is not None:
            asyncio.ensure_future(_close_quietly(old))

    async def responsive(self, timeout: float | None = None) -> bool:
        """Does the tab still execute JavaScript? A hung renderer must be replaced, not waited for."""
        if self.page is None or self.page.is_closed():
            return False
        for _ in range(2):                  # two misses in a row: a page that is merely loading answers in time
            try:
                await asyncio.wait_for(self.page.evaluate("1"), timeout or RESPONSIVE_TIMEOUT)
                return True
            except Exception:
                continue
        return False

    def fresh_start(self) -> None:
        """A decisive step needs this website now (the submission, the TAN inbox): hangs and tab recoveries of the
        research phase must not block it. The site may have recovered from a temporary outage since then."""
        self.hung.clear()
        self._recoveries = 0

    async def recover_tab(self, reason: str) -> None:
        """The tab stopped responding: continue in a fresh tab at the last good address (the session is shared)."""
        if self._recoveries >= 8:
            log("tab_recovery_limit", site=self.site_id, reason=reason)
            return
        self._recoveries += 1
        metrics.add("tab_recoveries")
        url = self.last_good_url or self.url
        log("tab_recovered", site=self.site_id, reason=reason, url=_short(url))
        await self._fresh_page()
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as error:
            log("goto_error", site=self.site_id, url=_short(url), error=str(error)[:200])
        await self.settle(recover=False)

    async def goto(self, url: str, frame: Frame | None = None) -> bool:
        """Navigate the tab (or a frame); True if a readable page arrived. Sites have pages that never finish
        loading: such an address gets two chances per run, afterwards the caller is told at once (try another route)."""
        key = url.split("#")[0]
        if self.hung.get(key, 0) >= 2:
            log("goto_skipped_hung", site=self.site_id, url=_short(url))
            return False
        target, arrived, hard_failure, hangs = frame or self.page, False, False, 0
        for attempt in range(3):
            if hangs >= 2:            # a page that hung twice in a row will not come in a third try
                break
            # After a hang, never insist on the same tab: a hung renderer does not come back.
            if hard_failure and frame is None and (attempt == 1 or not await self.responsive()):
                await self._fresh_page()
                target = self.page
            hard_failure = False
            try:
                response = await target.goto(url, wait_until="commit")
                if response is not None and response.status >= 500:
                    log("http_error", site=self.site_id, status=response.status, url=_short(url))
                    await asyncio.sleep(0.7 * (attempt + 1))
                    continue
                if await self._readable(target):
                    arrived = True
                    break
                hard_failure, hangs = True, hangs + 1
            except PlaywrightError as error:
                log("goto_error", site=self.site_id, url=_short(url), error=str(error)[:200])
                hard_failure, hangs = True, hangs + 1
                await asyncio.sleep(0.5)
        if not arrived:      # no guards on a page that never arrived: they would only wait for timeouts
            self.hung[key] = self.hung.get(key, 0) + 1
            metrics.add("goto_failed")
            log("goto_failed", site=self.site_id, url=_short(url), times=self.hung[key])
            return False
        await self.settle()
        return True

    async def _readable(self, target) -> bool:
        """The document arrived; wait for it to be parsed. If it never finishes (stuck sub-resource or stream) but
        already shows content, use it anyway."""
        try:
            await target.wait_for_load_state("domcontentloaded", timeout=LOADED_TIMEOUT_MS)
            return True
        except PlaywrightError:
            pass
        try:
            size = await _eval(target, "() => document.body ? document.body.innerText.length : 0", None, timeout=4)
        except PlaywrightError:
            size = 0
        log("partial_load", site=self.site_id, url=_short(target.url), chars=size)
        return bool(size and size > 40)

    async def settle(self, rounds: int = 6, recover: bool = True) -> None:
        """Run the guards until the page is usable: error retry, login, cookie consent, overlays, gates."""
        if not await self.responsive():
            if recover:
                await self.recover_tab("tab unresponsive while settling")
            return
        try:
            await asyncio.wait_for(self._settle(rounds), timeout=45)
        except asyncio.TimeoutError:
            log("settle_timeout", site=self.site_id, url=_short(self.page.url if self.page else ""))
            if recover and not await self.responsive():
                await self.recover_tab("settle timed out")

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
            if closed or unlocked:     # counted, not logged line by line: routine gates would fill the kept log
                metrics.add(f"guards:{self.site_id}")
            if closed:
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
    async def observe(self, limit: int = MAX_OBSERVATION) -> str:
        await self._ensure_page()
        if not await self.responsive():
            await self.recover_tab("tab unresponsive before observing")
        page = self.page
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
                if page.url.startswith("http"):
                    self.last_good_url = page.url
            parts.append(result["text"])
            for match in re.finditer(r"-> (\S+)|src=(\S+?)[\] ]", result["text"]):
                self.seen_urls.add(urljoin(frame.url, html.unescape(match.group(1) or match.group(2))))
        text = "\n".join(parts)
        self.last_observation = text            # refs on the page now match this text (used by `steps`)
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
                if not await self.responsive():
                    await self.recover_tab(f"tab unresponsive before {name}")
                # Delayed popups (surveys, notices) may have opened since the last observation.
                await self._run_guards_everywhere("close_overlays")
                method = getattr(self, f"_do_{name}")
                accepted = inspect.signature(method).parameters
                message = await asyncio.wait_for(
                    method(**{k: v for k, v in args.items() if k in accepted and v not in (None, "")}), timeout=100)
            except asyncio.TimeoutError:
                message = f"ERROR: {name} did not finish within 100 s"
                if not await self.responsive():
                    await self.recover_tab(f"{name} hung")
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
            return (f"ERROR: {href} did not load (the page keeps hanging). Use another route: e.g. a 'load more' "
                    f"button, another link to the same content, or continue with other work and come back later.")
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

    STEP_ACTIONS = ("click", "type", "select", "check", "fill_fields")

    async def _do_steps(self, steps: list) -> str:
        """Several form actions in a row, in one model step. A form re-renders after each 'apply', so refs of later
        actions are matched again by what they are (kind, name, n-th of that kind and name) on the new page. Stops at
        the first error or at an element that cannot be matched; the result says what was done."""
        done: list[str] = []
        steps = [s for s in steps or [] if isinstance(s, dict)][:20]
        seen = self.last_observation            # the page the model wrote its refs for
        for index, step in enumerate(steps):
            action = str(step.get("action", ""))
            if action not in self.STEP_ACTIONS:
                done.append(f"{index + 1}. ERROR action {action!r} not allowed here (use {', '.join(self.STEP_ACTIONS)})")
                break
            if index:
                await self.settle()
                step = _rematch(step, seen, await self.observe())
                if isinstance(step, str):
                    done.append(f"{index + 1}. {action}: ERROR {step}")
                    break
            method = getattr(self, f"_do_{action}")
            accepted = inspect.signature(method).parameters
            try:
                result = await method(**{k: v for k, v in step.items() if k in accepted and v not in (None, "")})
            except (PlaywrightError, KeyError, TypeError, ValueError, IndexError) as error:
                result = f"ERROR: {action} failed: {str(error).splitlines()[0][:200]}"
            done.append(f"{index + 1}. {action}: {result}")
            if "ERROR" in result:
                break
        left = len(steps) - len(done)
        return "\n".join(done) + (f"\nSTOPPED: {left} later action(s) not done; continue from the current page."
                                  if left else "")

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
        return f"ERROR: {url} did not load (the page keeps hanging); use another route"

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
        twin.seen_urls, twin.collections, twin.visual_notes, twin.hung = (
            self.seen_urls, self.collections, self.visual_notes, self.hung)
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
        helper.logins, helper.seen_urls, helper.hung = self.logins, self.seen_urls, self.hung
        started = asyncio.get_running_loop().time()
        helper.page = await self.context.new_page()
        helper._wire(helper.page)
        try:
            if not await helper.goto(url):          # shares self.hung: the failure is remembered for the site
                return ("ERROR: the page did not load (it keeps hanging); use another route, e.g. the list's "
                        "'load more' button or another link to the same content")
            if helper.page.url.rstrip("/") != url.rstrip("/") and "login" not in url:
                await helper.goto(url)       # after a re-login the site may have dropped the target
            return await helper.full_text(follow_iframes)
        finally:
            await _close_quietly(helper.page)       # closing a hung tab can hang too: bounded
            metrics.add("read_url_n")
            metrics.add("read_url_s", asyncio.get_running_loop().time() - started)

    async def close(self) -> None:
        """Close this tab (a tab opened for one mission)."""
        if self.page is not None:
            await _close_quietly(self.page)

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


async def _close_quietly(page: Page) -> None:
    try:
        await asyncio.wait_for(page.close(), timeout=10)
    except Exception:
        pass


def _short(url: str) -> str:
    return url[:160]


_CONTROL = re.compile(r'^\s*\[((f\d+)?e\d+)\] (\S+) "(.*?)"', re.M)


def _identities(text: str) -> dict[str, tuple]:
    """ref -> (frame, kind, name, n-th element with that frame, kind and name) for every control in an observation."""
    seen: dict[tuple, int] = {}
    found = {}
    for match in _CONTROL.finditer(text):
        key = (match.group(2) or "", match.group(3), match.group(4))
        seen[key] = seen.get(key, 0) + 1
        found[match.group(1)] = key + (seen[key],)
    return found


def _rematch(step: dict, before: str, after: str) -> dict | str:
    """The same action with its refs pointing at the same controls on the re-rendered page (or why that failed)."""
    old, new = _identities(before), {v: k for k, v in _identities(after).items()}

    def ref(value):
        value = re.sub(r"[^\w]", "", str(value))
        identity = old.get(value)
        if identity is None:
            return value                       # not a control we know: the action reports it
        if identity not in new:
            raise LookupError(f'{identity[1]} "{identity[2]}" is no longer on the page after the previous action')
        return new[identity]

    try:
        step = dict(step)
        for key in ("ref", "then_click"):
            if step.get(key):
                step[key] = ref(step[key])
        if isinstance(step.get("fields"), list):
            step["fields"] = [dict(f, ref=ref(f.get("ref"))) if isinstance(f, dict) else f for f in step["fields"]]
        return step
    except LookupError as error:
        return str(error)

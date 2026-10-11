"""One website in one browser: login, guards, observation and actions.

The model decides what to do; this class only makes each action robust: overlays are closed, cookie walls and
gates handled, expired sessions re-logged, flaky error pages retried, popups folded back into the tab, and every
action answers with a fresh observation instead of raising.
"""
import asyncio
import html
import inspect
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import BrowserContext, Error as PlaywrightError, Frame, Page

from ..log import log, register_secret

OBSERVE_JS = (Path(__file__).with_name("observe.js")).read_text()
GUARDS_JS = (Path(__file__).with_name("guards.js")).read_text()
MAX_OBSERVATION = 9000
EVAL_TIMEOUT = 12
NAVIGATION_TIMEOUT_MS = 4000
LOADED_TIMEOUT_MS = 2000
NAVIGATION_BUDGET = 12.0
RESPONSIVE_TIMEOUT = 1.0


async def _eval(target, script: str, arg, timeout: float | None = None):
    """page/frame.evaluate with a time limit: a page stuck in navigation must never block the agent."""
    timeout = timeout or EVAL_TIMEOUT
    try:
        return await asyncio.wait_for(target.evaluate(script, arg), timeout)
    except asyncio.TimeoutError:
        raise PlaywrightError(f"evaluate timed out after {timeout}s")


class NotDispatched(ValueError):
    """The requested target did not exist before any browser write."""


class Site:
    def __init__(self, site_id: str, url: str, credentials: dict, context: BrowserContext):
        self.site_id, self.url, self.credentials, self.context = site_id, url.rstrip("/"), credentials, context
        for secret in credentials.values():
            register_secret(secret)
        self.page: Page | None = None
        self.origin = urlparse(self.url).netloc
        self.logins = 0
        self.seen_urls: set[str] = set()
        self.collections: dict = {}             # read_all_pages results (shared by all missions on this site)
        self.visual_notes: dict[str, list[str]] = {}   # url -> answers read from screenshots
        self._frames: dict[int, Frame] = {}     # frame index of the last observation -> frame (refs stay valid)
        self.last_good_url: str | None = None   # last address that was observed successfully
        self.hung: dict[str, int] = {}          # address -> navigations that never arrived (do not insist)
        self.health = {"fails": 0, "resets": 0, "at": 0.0}   # navigation failures in a row, session resets (shared)
        self._recoveries = 0
        self.write_outcome_unknown = False
        self.last_readbacks: list[dict] = []
        self._start_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()   # one action at a time per site page

    @property
    def session_generation(self) -> int:
        return self.health.get("generation", 0)

    # ---------------------------------------------------------------- lifecycle
    def start_task(self) -> asyncio.Task:
        """Attach this site independently while other sites and the brief are prepared."""
        if self._start_task is not None and self._start_task.done():
            if self._start_task.cancelled() or self._start_task.exception() is not None:
                self._start_task = None
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start(), name=f"site-start:{self.site_id}")
            self._start_task.add_done_callback(self._startup_finished)
        return self._start_task

    def _startup_finished(self, task: asyncio.Task) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            log("site_start_error", site=self.site_id, error=str(error)[:200])

    async def wait_started(self) -> str:
        if self._start_task is None and self.page is not None:
            return await self.observe()
        return await asyncio.shield(self.start_task())

    async def start(self) -> str:
        return await self.wait_started()

    async def _start(self) -> str:
        self.page = self.page or (self.context.pages[0] if self.context.pages else await self.context.new_page())
        self._wire(self.page)
        await self.goto(self.url)
        return await self.observe()

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
        """A decisive step needs this website now (the submission, the TAN inbox): hangs, tab recoveries and lists
        collected during the research phase must not block or replace it. The site may have recovered from a
        temporary outage, and the inbox has new mail (the TAN) that a cached list would not show."""
        self.hung.clear()
        self._recoveries = 0
        self.collections.clear()

    async def recover_tab(self, reason: str) -> None:
        """Read-only recovery in a new tab sharing the session; never replay a write."""
        if self._recoveries >= 8:
            log("tab_recovery_limit", site=self.site_id, reason=reason)
            return
        self._recoveries += 1
        url = self.last_good_url or self.url
        log("tab_recovered", site=self.site_id, reason=reason, url=_short(url))
        await self._fresh_page()
        if await self._navigate(self.page, url):
            await self.settle(recover=False)

    async def _navigate(self, target, url: str) -> bool:
        """Four seconds to commit and two seconds for functional current-document readiness."""
        before = target.url
        try:
            response = await target.goto(url, wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
            if response is not None and response.status >= 500:
                log("http_error", site=self.site_id, status=response.status, url=_short(url))
                return False
        except PlaywrightError as error:
            log("goto_error", site=self.site_id, url=_short(url), error=str(error)[:200])
            # A timeout may follow a committed redirect. An unchanged old document is not success.
            if target.url == before:
                return False
        return await self._readable(target)

    async def _goto_fast(self, url: str, frame: Frame | None) -> bool:
        target = frame or self.page
        for attempt in range(2):
            if attempt and frame is None:
                await self._fresh_page()
                target = self.page
            if await self._navigate(target, url):
                return True
        return False

    async def goto(self, url: str, frame: Frame | None = None) -> bool:
        """A bounded read-only attempt with one recovery; quality session repair is logged separately."""
        started = time.monotonic()
        key = url.split("#")[0]
        if self.hung.get(key, 0) >= 2:
            log("goto_skipped_hung", site=self.site_id, url=_short(url))
            return False
        arrived = False
        try:
            arrived = await asyncio.wait_for(self._goto_fast(url, frame), timeout=NAVIGATION_BUDGET)
        except asyncio.TimeoutError:
            log("navigation_budget_exhausted", site=self.site_id, url=_short(url))
        if arrived:
            self.health["fails"] = 0
            # Guard work uses the same whole-attempt budget. Login repair can continue for quality.
            remaining = NAVIGATION_BUDGET - (time.monotonic() - started)
            if remaining > 0:
                try:
                    await asyncio.wait_for(self.settle(recover=False), remaining)
                except asyncio.TimeoutError:
                    log("navigation_speed_miss", site=self.site_id, reason="guard recovery", url=_short(url))
                    await self.settle(recover=False)
            else:
                log("navigation_speed_miss", site=self.site_id, reason="functional recovery", url=_short(url))
                await self.settle(recover=False)
        else:
            self.hung[key] = self.hung.get(key, 0) + 1
            self.health["fails"] += 1
            log("goto_failed", site=self.site_id, url=_short(url), times=self.hung[key])
            if frame is None and await self._new_session(url):
                log("navigation_speed_miss", site=self.site_id, reason="session recovery", url=_short(url))
                arrived = await self.goto(url)
        log("navigation_span", site=self.site_id, url=_short(url), seconds=round(time.monotonic() - started, 3),
            arrived=arrived, speed_miss=time.monotonic() - started > NAVIGATION_BUDGET)
        return arrived

    async def _new_session(self, url: str) -> bool:
        """Pages of a website that keep hanging for our session load again in a new one: often one request that
        never ends holds the session on the server, and every later request of that session waits behind it.
        The site's cookies are dropped and the login guard signs in again. At most twice per run and site, once a
        minute, after two failed navigations in a row."""
        state = self.health
        if state["fails"] < 2 or state["resets"] >= 2 or time.time() - state["at"] < 60:
            return False
        state.update(resets=state["resets"] + 1, fails=0, at=time.time())
        log("session_reset", site=self.site_id, after=_short(url), reset=state["resets"])
        self.hung.clear()
        self.health["generation"] = self.session_generation + 1
        self.collections.clear()
        try:
            await asyncio.wait_for(self.context.clear_cookies(), timeout=10)
        except Exception as error:
            log("session_reset_failed", site=self.site_id, error=str(error)[:200])
            return False
        await self._fresh_page()             # the old tab and its pending requests go away
        await self._navigate(self.page, self.url)
        await self.settle(recover=False)     # the login guard signs in
        return True

    async def _readable(self, target) -> bool:
        """Use functional DOM readiness, including readable documents with stuck subresources."""
        limit = time.monotonic() + LOADED_TIMEOUT_MS / 1000
        while time.monotonic() < limit:
            try:
                state = await _eval(target, _FUNCTIONAL_STATE, None,
                                    timeout=max(0.01, limit - time.monotonic()))
                if state and state.get("ready"):
                    return True
            except PlaywrightError:
                return False
            await asyncio.sleep(min(0.1, max(0, limit - time.monotonic())))
        log("functional_readiness_timeout", site=self.site_id, url=_short(target.url))
        return False

    async def settle(self, rounds: int = 6, recover: bool = True, retry_errors: bool = True) -> None:
        """Run the guards until the page is usable: error retry, login, cookie consent, overlays, gates."""
        if not await self.responsive():
            if recover:
                await self.recover_tab("tab unresponsive while settling")
            return
        try:
            await asyncio.wait_for(self._settle(rounds, retry_errors=retry_errors), timeout=12)
        except asyncio.TimeoutError:
            log("settle_timeout", site=self.site_id, url=_short(self.page.url if self.page else ""))
            if recover and not await self.responsive():
                await self.recover_tab("settle timed out")

    async def _settle(self, rounds: int, retry_errors: bool = True) -> None:
        page = await self._ensure_page()
        for _ in range(rounds):
            state = await self._guard_state()
            if state.get("error_page"):
                if not retry_errors:
                    self.write_outcome_unknown = True
                    log("guard_write_outcome_unknown", site=self.site_id, url=_short(page.url))
                    return
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
            await asyncio.wait_for(self._goto_fast(url, None), NAVIGATION_BUDGET)
        except asyncio.TimeoutError:
            log("navigation_budget_exhausted", site=self.site_id, url=_short(url), via="guard")

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
            await self._readable(page)
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
            self.write_outcome_unknown = False
            try:
                await self._ensure_page()
                if not await self.responsive():
                    await self.recover_tab(f"tab unresponsive before {name}")
                # Delayed popups (surveys, notices) may have opened since the last observation.
                await self._run_guards_everywhere("close_overlays")
                method = getattr(self, f"_do_{name}")
                accepted = inspect.signature(method).parameters
                message = await asyncio.wait_for(
                    method(**{k: v for k, v in args.items() if k in accepted and v is not None}), timeout=100)
            except NotDispatched as error:
                message = "NOT_DISPATCHED: " + str(error)
            except asyncio.TimeoutError:
                message = f"ERROR: {name} did not finish within 100 s"
                if not await self.responsive():
                    await self.recover_tab(f"{name} hung")
            except (PlaywrightError, KeyError, TypeError, ValueError, IndexError) as error:
                message = f"ERROR: {name} failed: {str(error).splitlines()[0][:300]}"
            await self.settle(retry_errors=name in {"open_url", "back", "hover", "scroll", "wait_for"})
            if self.write_outcome_unknown:
                message += "; OUTCOME UNKNOWN: error page after a write; observe and verify before repeating"
            return message

    async def _element(self, ref: str):
        ref = re.sub(r"[^\w]", "", str(ref))          # models sometimes send '"e9"' or '[e9]'
        frame, locator = self._locate(ref)
        if not await locator.count():
            raise NotDispatched(f"ref {ref} is not on the current page (refs change after every action)")
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
        popups = self._watch_popups()
        try:
            # A failed click may have submitted a write. Observe its outcome; do not dispatch or replay it.
            await el.click(timeout=4000, no_wait_after=True)
            await asyncio.sleep(0.3)
        finally:
            self._unwatch_popups(popups)
        folded = await self._fold_popups(popups)
        return "clicked" + (f"; {folded}" if folded else "")

    def _watch_popups(self) -> "_Popups":
        """Windows opened by THIS tab from now on (other missions open tabs in the same browser context)."""
        return _Popups(self.page)

    def _unwatch_popups(self, popups: "_Popups") -> None:
        popups.stop()

    async def _fold_popups(self, popups: list) -> str:
        """Adopt this action's window as the current view without replaying its navigation."""
        for popup in list(popups):
            try:
                readable = await self._readable(popup)
                url = popup.url
                if url and url != "about:blank":
                    old = self.page
                    self.page = popup
                    self._wire(popup)
                    self._frames.clear()
                    if old is not popup:
                        asyncio.ensure_future(_close_quietly(old))
                    return (f"a popup window opened {url} and was loaded here instead" +
                            ("; document is still loading, observe before continuing" if not readable else ""))
            except PlaywrightError:
                pass
        return ""

    async def _ready(self, el) -> str | None:
        """Make a form control usable (open collapsed <details> around it); explain why it is not."""
        await el.evaluate("e => { for (let d = e.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true; }")
        for _ in range(12):
            if not await el.is_disabled():
                return None
            await asyncio.sleep(0.2)
        if await el.is_disabled():
            return ("ERROR: this field is disabled right now. Usually the previous choice must be applied first "
                    "(e.g. an 'apply/übernehmen' button), or a gate must be unlocked.")
        return None

    async def _do_type(self, ref: str, text: str, submit: bool = False) -> str:
        _, el = await self._element(ref)
        return await self._type_el(el, text, submit=submit)

    async def _type_el(self, el, text: str, submit: bool = False) -> str:
        if problem := await self._ready(el):
            return problem
        await el.fill(str(text), timeout=4000)
        value = await el.input_value()
        if value != str(text):
            return "ERROR: field readback differs from requested value"
        if submit:
            await el.press("Enter")
            await asyncio.sleep(0.3)
        return f"typed; field now contains {value!r}" + (" and pressed Enter" if submit else "")

    async def _do_select(self, ref: str, option: str, then_click: str | None = None) -> str:
        result = await self._select(ref, option, hint=not then_click)
        return result + ("" if result.startswith("ERROR") else await self._then_click(then_click))

    async def _select(self, ref: str, option: str, hint: bool = False) -> str:
        _, el = await self._element(ref)
        return await self._select_el(el, option, hint=hint)

    async def _select_el(self, el, option: str, hint: bool = False) -> str:
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
        kept, shown = await el.evaluate("e => [e.value, e.selectedOptions[0] ? e.selectedOptions[0].textContent.trim() : '']")
        if kept != value:          # read back: scripts may reset a choice (e.g. until a previous step is applied)
            return (f"ERROR: the field did not keep {option!r}; it shows {shown!r}. Apply the previous step first or "
                    "select it again")
        hint = hint and await el.evaluate("""e => { const f = e.form || e.closest('form') || document;
            return [...f.querySelectorAll('select:disabled, input:disabled')].length; }""")
        return f"selected {value!r}" + (" (other fields of this form are still disabled: apply the choice with the "
                                        "form's apply/update button, e.g. via then_click)" if hint else "")

    async def _do_check(self, ref: str, checked: bool = True) -> str:
        _, el = await self._element(ref)
        return await self._check_el(el, checked=checked)

    async def _check_el(self, el, checked: bool = True) -> str:
        if problem := await self._ready(el):
            return problem
        kind = await el.evaluate("e => e.type")
        if kind not in ("checkbox", "radio"):
            return f"ERROR: element is not a checkbox/radio (it is {kind}); click it instead"
        await el.set_checked(bool(checked), timeout=4000)
        if await el.is_checked() != bool(checked):
            return "ERROR: checkbox/radio readback differs from requested value"
        return f"{'checked' if checked else 'unchecked'}"

    async def _then_click(self, ref: str | None) -> str:
        if not ref:
            return ""
        return "; then " + await self._do_click(str(ref))

    async def field_identity(self, ref: str) -> dict:
        """A binding identifies the frame, field meaning and current DOM instance."""
        frame, el = await self._element(ref)
        state = await el.evaluate(_FIELD_STATE)
        return {"url": self.page.url, "frame": frame.url, **state}

    async def _field_readback(self, ref: str) -> dict:
        _, el = await self._element(ref)
        value = await el.evaluate("e => ['checkbox','radio'].includes(e.type) ? e.checked : e.value")
        return {"ref": ref, "value": value, "identity": await self.field_identity(ref)}

    async def _do_fill_fields(self, fields: list, then_click: str | None = None) -> str:
        """Apply observed fields, verify every readback, and stop on unexpected rerender or navigation."""
        fields = [f for f in fields or [] if isinstance(f, dict)]
        self.last_readbacks = []
        identity = {}
        for field in fields:
            ref = str(field.get("ref"))
            try:
                if field.get("apply"):
                    apply_ref = str(field["apply"])
                    identity[apply_ref] = await self.field_identity(apply_ref)
                actual = await self.field_identity(ref)
                expected = field.get("identity")
                if expected is not None and expected != actual:
                    return f"STOPPED: binding for {ref} no longer identifies the observed field; observe again"
                identity[ref] = actual
            except (PlaywrightError, ValueError) as error:
                return f"STOPPED: {ref} is unavailable; observe again ({str(error).splitlines()[0][:100]})"
        if then_click:
            try:
                identity[str(then_click)] = await self.field_identity(str(then_click))
            except (PlaywrightError, ValueError):
                return "STOPPED: final batch control is unavailable; observe again"
        results, stopped, allow_rerender = [], False, False
        for index, field in enumerate(fields):
            ref, value, apply = str(field.get("ref")), field.get("value"), field.get("apply")
            try:
                actual = await self.field_identity(ref)
                wanted = identity[ref]
                semantic = ("url", "frame", "identity")
                if any(actual[k] != wanted[k] for k in semantic) or (not allow_rerender and actual["node"] != wanted["node"]):
                    results.append(f"{ref}: STOPPED, the form changed and {ref} is another field now")
                    stopped = True
                    break
                _, el = await self._element(ref)
                tag, kind = await el.evaluate("e => [e.tagName, e.type]")
                if tag == "SELECT":
                    result = await self._select_el(el, str(value))
                elif kind in ("checkbox", "radio"):
                    result = await self._check_el(el, str(value).lower() not in ("false", "0", "no", "nein", ""))
                else:
                    result = await self._type_el(el, str(value))
                if not result.startswith("ERROR"):
                    self.last_readbacks.append(await self._field_readback(ref))
            except (PlaywrightError, ValueError) as error:
                result = f"ERROR {str(error).splitlines()[0][:150]}"
            results.append(f"{ref}: {result}")
            if result.startswith("ERROR"):
                stopped = True
                break
            if apply:
                try:
                    # Capture this explicit dependency control before executing it once.
                    before_apply = await self.field_identity(str(apply))
                    wanted_apply = identity[str(apply)]
                    if any(before_apply[k] != wanted_apply[k] for k in ("url", "frame", "identity")) or (
                            not allow_rerender and before_apply["node"] != wanted_apply["node"]):
                        raise ValueError("apply control changed identity")
                    applied = await self._do_click(str(apply))
                    results.append(f"applied ({apply}): " + applied)
                    if applied.startswith("ERROR") or self.page.url != wanted["url"]:
                        stopped = True
                        break
                    await self.settle(rounds=3, retry_errors=False)
                    await self.observe()
                    allow_rerender = True
                except (PlaywrightError, ValueError) as error:
                    results.append(f"STOPPED: apply outcome requires a new observation ({str(error)[:100]})")
                    stopped = True
                    break
        if stopped:
            done = [r["ref"] for r in self.last_readbacks]
            return ("STOPPED: " + "; ".join(results) + f". Done before stopping: {done or 'nothing'}. Observe the page and continue "
                    "with the remaining fields.")
        # Later dependencies can reset earlier values. Verify all applied fields before any final click.
        checked = []
        for saved in self.last_readbacks:
            try:
                current = await self._field_readback(saved["ref"])
                if current["value"] != saved["value"] or any(
                        current["identity"][k] != saved["identity"][k] for k in ("url", "frame", "identity")):
                    return "; ".join(results) + "; STOPPED: a previously applied field changed; observe again"
                checked.append(current)
            except (PlaywrightError, ValueError):
                return "; ".join(results) + "; STOPPED: a previously applied field disappeared; observe again"
        self.last_readbacks = checked
        if then_click:
            actual = await self.field_identity(str(then_click))
            wanted = identity[str(then_click)]
            if any(actual[k] != wanted[k] for k in ("url", "frame", "identity")) or (not allow_rerender and actual["node"] != wanted["node"]):
                return "; ".join(results) + "; STOPPED: final batch control changed; observe again"
        return "; ".join(results) + await self._then_click(then_click)

    async def _do_set_slider(self, ref: str, value: str = "max") -> str:
        _, el = await self._element(ref)
        if str(value) == "max":
            value = await el.get_attribute("max") or "100"
        await el.fill(str(value))
        return f"slider set to {value}"

    async def _do_back(self) -> str:
        started = time.monotonic()
        before = self.page.url
        try:
            await self.page.go_back(wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
        except PlaywrightError as error:
            log("back_error", site=self.site_id, error=str(error)[:200])
            if self.page.url == before:
                return "ERROR: back navigation did not commit; current document may still be the previous page"
        if await self._readable(self.page):
            return "went back"
        # Back can leave a partially committed document. One read-only reload of that URL is safe.
        remaining = NAVIGATION_BUDGET - (time.monotonic() - started)
        if remaining > 0:
            try:
                if await asyncio.wait_for(self._navigate(self.page, self.page.url), remaining):
                    return "went back; recovered current document"
            except asyncio.TimeoutError:
                pass
        return "ERROR: back navigation did not reach a functional document; observe before continuing"

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
        popups = self._watch_popups()
        try:
            if ref:
                _, el = await self._element(ref)
                await el.press(str(key), timeout=4000)
            else:
                await self.page.keyboard.press(str(key))
            await asyncio.sleep(0.4)
        finally:
            self._unwatch_popups(popups)
        folded = await self._fold_popups(popups)
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
        await self.wait_started()
        twin = Site(self.site_id, self.url, self.credentials, self.context)
        twin.seen_urls, twin.collections, twin.visual_notes, twin.hung, twin.health = (
            self.seen_urls, self.collections, self.visual_notes, self.hung, self.health)
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
        await self.wait_started()
        url = self.absolute(url)
        if urlparse(url).netloc != self.origin:
            return "ERROR: only URLs of this website can be read"
        started = time.monotonic()
        helper = Site(self.site_id, self.url, self.credentials, self.context)
        helper.logins, helper.seen_urls, helper.hung, helper.health = self.logins, self.seen_urls, self.hung, self.health
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
            seconds = time.monotonic() - started
            log("helper_read_span", site=self.site_id, url=_short(url), seconds=round(seconds, 3),
                speed_miss=seconds > NAVIGATION_BUDGET)

    async def screenshot(self, ref: str | None = None) -> tuple[bytes, str]:
        """JPEG of an element (if it has a size) or of the viewport, and a note on what it shows. A small control
        (tab, button) is not a document: when the page shows a large picture without text, that picture is taken."""
        if ref:
            try:
                frame, el = await self._element(ref)
                box = await el.bounding_box()
                if box and box["width"] > 4 and box["height"] > 4:
                    tag = await el.evaluate("e => e.tagName")
                    if tag not in ("CANVAS", "IMG", "SVG") and box["width"] * box["height"] < 25000:
                        picture = await self._largest_picture(frame)
                        if picture is not None:
                            return (await picture.screenshot(type="jpeg", quality=70, timeout=8000),
                                    f"{ref} is a small control; the screenshot shows the largest picture of the page instead")
                    return await el.screenshot(type="jpeg", quality=70, timeout=8000), f"element {ref}"
            except (PlaywrightError, ValueError):
                pass
        else:   # no target named: a page showing a document as a picture is asked about that picture
            for frame in self.page.frames:
                try:
                    picture = await self._largest_picture(frame)
                    if picture is not None:
                        return (await picture.screenshot(type="jpeg", quality=70, timeout=8000),
                                "the largest picture of the page (whole picture, also the part outside the window)")
                except PlaywrightError:
                    continue
        return await self.page.screenshot(type="jpeg", quality=60), "the visible part of the page"

    async def _largest_picture(self, frame):
        """The largest visible canvas (else image) of at least 200x200 px: a scanned document or a drawn price table,
        labelled or not (labels like 'scanned document' do not carry the content); None when there is none."""
        handle = await frame.evaluate_handle("""() => {
            for (const kind of ['canvas', 'img']) {
                let best = null, area = 40000;
                for (const e of document.querySelectorAll(kind)) {
                    const b = e.getBoundingClientRect(), a = b.width * b.height;
                    if (a > area && getComputedStyle(e).visibility !== 'hidden') { best = e; area = a; }
                }
                if (best) return best;
            }
            return null; }""")
        element = handle.as_element()
        return element


_FUNCTIONAL_STATE = """() => {
    const b = document.body;
    if (!b) return {ready: false};
    const visible = e => { const r=e.getBoundingClientRect(), s=getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
    const controls = [...b.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea')]
        .some(e => visible(e) && !e.disabled);
    const content = [...b.querySelectorAll('main, article, p, td, li, h1, h2, pre, canvas, img')]
        .some(e => visible(e) && (e.textContent.trim() || ['CANVAS','IMG'].includes(e.tagName)));
    return {ready: controls || content || (document.readyState !== 'loading' && !!b.innerText.trim())};
}"""

_FIELD_STATE = """e => {
    const w = e.ownerDocument.defaultView;
    if (!w.__agentFieldNodes) { w.__agentFieldNodes = new WeakMap();
        w.__agentFieldSerial = 0; w.__agentFieldDocument = Math.random().toString(36).slice(2); }
    if (!w.__agentFieldNodes.has(e)) w.__agentFieldNodes.set(e, ++w.__agentFieldSerial);
    const l = e.labels && e.labels[0];
    const own = l ? [...l.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim() : '';
    return {identity: [e.tagName, e.type || '', e.name || '', e.id || '',
        own || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.textContent.trim(),
        e.form ? e.form.action : ''].join('|'),
        node: w.__agentFieldDocument + ':' + w.__agentFieldNodes.get(e)};
}"""



class _Popups(list):
    """The windows one tab opens while it acts (window.open, target=_blank): the page's own popup events, so a
    helper tab another mission opens meanwhile in the same browser context is never taken for one."""

    def __init__(self, page: Page):
        super().__init__()
        self.page = page
        page.on("popup", self._opened)

    def _opened(self, popup: Page) -> None:
        self.append(popup)

    def stop(self) -> None:
        try:
            self.page.remove_listener("popup", self._opened)
        except Exception:
            pass


async def _close_quietly(page: Page) -> None:
    try:
        await asyncio.wait_for(page.close(), timeout=2)
    except Exception:
        pass


def _short(url: str) -> str:
    return url[:160]

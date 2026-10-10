"""Browser tools for the model. Every tool returns text and never raises: errors become information.

The model chooses every target (by ref from its last observation); the code only executes, verifies by reading
back, runs the guards (overlays, cookies, relogin, error retry) and reports what changed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re

from playwright.async_api import Locator

from . import guards
from .documents import read_document
from .observe import Observation, diff, frame_of, observe
from .session import SiteSession

REF = re.compile(r"^(f\d+)?e\d+$")


class ToolError(Exception):
    pass


class BrowserTools:
    def __init__(self, session: SiteSession):
        self.s = session
        self.obs: Observation | None = None
        self.images: list[bytes] = []  # screenshots for the next model turn (vision)

    # ---- helpers --------------------------------------------------------------------------------------------
    async def _locate(self, ref: str) -> Locator:
        ref = (ref or "").strip().strip("[]")
        if not REF.match(ref):
            raise ToolError(f'"{ref}" ist keine Ref. Refs stehen in eckigen Klammern in der Beobachtung, z. B. e12.')
        frame = frame_of(self.s.page, ref)
        if frame is None:
            raise ToolError(f"Frame von {ref} existiert nicht mehr.")
        loc = frame.locator(f'[data-agent-ref="{ref}"]')
        if await loc.count() == 0:
            raise ToolError(f"{ref} existiert nicht mehr (Seite hat sich geändert).")
        return loc.first

    async def _attr(self, loc: Locator, name: str) -> str | None:
        try:
            return await loc.get_attribute(name, timeout=2000)
        except Exception:
            return None

    async def _observe_text(self, scope: str | None = None) -> Observation:
        await guards.close_overlays(self.s)
        o = await observe(self.s.page, scope=scope)
        self.s.remember(o.text)
        return o

    async def _result(self, msg: str, full: bool = False) -> str:
        """Message + guard notes + what changed on the page (diff when small, else the full observation)."""
        before = self.obs.text if self.obs else ""
        url_before = self.obs.url if self.obs else ""
        o = await self._observe_text()
        self.obs = o
        notes = self.s.take_events()
        parts = [msg] + [f"[Hinweis] {n}" for n in notes]
        d = None if full or o.url != url_before else diff(before, o.text)
        parts.append(f"Änderungen auf der Seite ({o.url}):\n{d}" if d else o.render())
        return "\n".join(parts)

    async def _run(self, coro_fn, msg: str) -> str:
        try:
            await guards.before_action(self.s)
            out = await coro_fn()
            await guards.after_action(self.s)
            return await self._result(out or msg)
        except ToolError as e:
            return await self._result(f"Fehler: {e}", full=True)
        except Exception as e:
            first = str(e).splitlines()[0][:300] if str(e) else type(e).__name__
            return await self._result(f"Fehler bei der Aktion: {first}", full=True)

    async def _click(self, loc: Locator, **kw) -> None:
        try:
            await loc.click(timeout=6000, **kw)
        except Exception as e:
            if "intercepts pointer events" in str(e) or "Timeout" in str(e):
                await guards.close_overlays(self.s)
                try:
                    await loc.click(timeout=4000, **kw)
                    return
                except Exception:
                    pass
                await loc.dispatch_event("click")
            else:
                raise

    # ---- tools ----------------------------------------------------------------------------------------------
    async def observe(self, scope: str | None = None, offset: int = 0) -> str:
        """Current page as text with refs. scope=ref limits to one element (e.g. a table); offset pages long text."""
        try:
            await guards.after_action(self.s)
            o = await self._observe_text(scope)
            if not scope:
                self.obs = o
            notes = [f"[Hinweis] {n}" for n in self.s.take_events()]
            return "\n".join(notes + [o.render(offset=offset)])
        except Exception as e:
            return f"Fehler beim Lesen der Seite: {e}"

    async def click(self, ref: str) -> str:
        async def go():
            loc = await self._locate(ref)
            if await self._attr(loc, "disabled") is not None or await self._attr(loc, "aria-disabled") == "true":
                raise ToolError(f"{ref} ist deaktiviert — anderes Element wählen.")
            await self._click(loc)
        return await self._run(go, f"Geklickt: {ref}")

    async def open_link(self, ref: str) -> str:
        """Open a link's target in this tab (avoids popups / new windows)."""
        async def go():
            loc = await self._locate(ref)
            href = await self._attr(loc, "href") or await self._attr(loc, "src")
            if not href or href.startswith("javascript:"):
                await self._click(loc)
                return f"{ref} hat kein Linkziel — stattdessen geklickt"
            url = self.s.absolute(href)
            self.s.intended_url = url
            await self.s.page.goto(url, wait_until="domcontentloaded")
            return f"Geöffnet: {url}"
        return await self._run(go, "")

    async def goto(self, url: str) -> str:
        """Only URLs that appeared in an observation (or the site start page) are allowed."""
        async def go():
            target = self.s.absolute(url).split("#")[0]
            if target not in self.s.seen_urls:
                raise ToolError("Diese URL wurde auf keiner Seite angezeigt. Nutze Links aus der Beobachtung.")
            self.s.intended_url = target
            await self.s.page.goto(target, wait_until="domcontentloaded")
            return f"Geöffnet: {target}"
        return await self._run(go, "")

    async def back(self) -> str:
        async def go():
            await self.s.page.go_back(wait_until="domcontentloaded")
        return await self._run(go, "Zurück navigiert")

    async def fill(self, ref: str, text: str, submit: bool = False) -> str:
        async def go():
            loc = await self._locate(ref)
            await loc.fill(str(text), timeout=6000)
            got = await loc.input_value(timeout=2000)
            msg = f'Eingetragen in {ref}: "{got}"' + ("" if got == str(text) else f' (ACHTUNG: erwartet "{text}")')
            if submit:
                await loc.press("Enter")
                msg += " + Enter"
            return msg
        return await self._run(go, "")

    async def select(self, ref: str, option: str) -> str:
        async def go():
            loc = await self._locate(ref)
            try:
                await loc.select_option(label=option, timeout=4000)
            except Exception:
                try:
                    await loc.select_option(value=option, timeout=3000)
                except Exception:
                    labels = await loc.evaluate("e => [...e.options].map(o => o.label || o.text)")
                    match = [l for l in labels if option.strip().lower() in l.lower()]
                    if len(match) != 1:
                        raise ToolError(f'Option "{option}" nicht eindeutig. Optionen: {labels}')
                    await loc.select_option(label=match[0], timeout=3000)
            chosen = await loc.evaluate("e => e.selectedOptions[0] ? e.selectedOptions[0].text : ''")
            return f'Ausgewählt in {ref}: "{chosen.strip()}"'
        return await self._run(go, "")

    async def check(self, ref: str, checked: bool = True) -> str:
        async def go():
            loc = await self._locate(ref)
            try:
                await loc.set_checked(checked, timeout=5000)
            except Exception:
                await self._click(loc)
            state = await loc.evaluate("e => e.checked ?? (e.getAttribute('aria-checked') === 'true')")
            return f"{ref} ist jetzt {'angehakt' if state else 'nicht angehakt'}"
        return await self._run(go, "")

    async def set_range(self, ref: str, value: str = "max") -> str:
        """Slider: set to a value or "max" (fires input/change); falls back to a real mouse drag."""
        async def go():
            loc = await self._locate(ref)
            info = await loc.evaluate("e => ({min: e.min || e.getAttribute('aria-valuemin'), max: e.max || e.getAttribute('aria-valuemax'), tag: e.tagName})")
            target = str(info["max"] if value in ("max", "", None) else info["min"] if value == "min" else value)
            if info["tag"] == "INPUT":
                try:
                    await loc.fill(target, timeout=3000)
                except Exception:
                    pass
            now = await loc.evaluate("e => e.value ?? e.getAttribute('aria-valuenow')")
            if str(now) != target:  # custom slider or listener on pointer events: drag
                box = await loc.bounding_box()
                if box:
                    y = box["y"] + box["height"] / 2
                    await self.s.page.mouse.move(box["x"] + 3, y)
                    await self.s.page.mouse.down()
                    end = box["x"] + (box["width"] + 30 if value in ("max", "", None) else 3)
                    await self.s.page.mouse.move(end, y, steps=20)
                    await self.s.page.mouse.up()
                now = await loc.evaluate("e => e.value ?? e.getAttribute('aria-valuenow')")
            return f"Schieberegler {ref} steht auf {now}" + ("" if str(now) == target else f" (Ziel {target} nicht erreicht)")
        return await self._run(go, "")

    async def hover(self, ref: str) -> str:
        async def go():
            await (await self._locate(ref)).hover(timeout=5000)
            await asyncio.sleep(0.3)
        return await self._run(go, f"Maus über {ref} — Menüs/Tooltips sind jetzt sichtbar")

    async def expand(self, ref: str) -> str:
        """Open a <details>/summary, an accordion or a menu button."""
        async def go():
            loc = await self._locate(ref)
            opened = await loc.evaluate("""e => { const d = e.tagName === 'DETAILS' ? e : e.closest('details');
                if (d) { d.open = true; return true; } return false; }""")
            if not opened:
                await self._click(loc)
        return await self._run(go, f"Aufgeklappt: {ref}")

    async def context_menu(self, ref: str) -> str:
        async def go():
            await self._click(await self._locate(ref), button="right")
        return await self._run(go, f"Kontextmenü an {ref} geöffnet")

    async def press(self, key: str, ref: str | None = None) -> str:
        async def go():
            if ref:
                await (await self._locate(ref)).press(key)
            else:
                await self.s.page.keyboard.press(key)
        return await self._run(go, f"Taste {key} gedrückt")

    async def search(self, ref: str, query: str) -> str:
        """Type a query into a search field and submit it (Enter)."""
        return await self.fill(ref, query, submit=True)

    async def read_canvas(self, ref: str) -> str:
        """Text of a canvas/image: its aria-label/alt, else a screenshot of it for vision."""
        try:
            loc = await self._locate(ref)
            label = await self._attr(loc, "aria-label") or await self._attr(loc, "alt")
            if label:
                return f"{ref}: {label}"
            self.images.append(await loc.screenshot(type="png", timeout=5000))
            return f"{ref} hat keinen Text — Bildausschnitt ist angehängt."
        except Exception as e:
            return f"Fehler: {e}"

    async def screenshot(self, ref: str | None = None) -> str:
        try:
            target = await self._locate(ref) if ref else self.s.page
            self.images.append(await target.screenshot(type="jpeg", quality=60, timeout=8000))
            return "Screenshot angehängt."
        except Exception as e:
            return f"Fehler: {e}"

    async def read_document(self, ref: str) -> str:
        """Fetch the document behind a link (CSV/TXT/HTML/ZIP/PDF) with the session cookies and return its text."""
        try:
            loc = await self._locate(ref)
            href = await self._attr(loc, "href") or await self._attr(loc, "src")
            if not href:
                return f"{ref} hat kein Linkziel. Öffne es mit click und lies mit observe."
            return await read_document(self.s, self.s.absolute(href))
        except Exception as e:
            return f"Fehler beim Lesen des Dokuments: {e}"

    async def paginate(self, next_ref: str, max_pages: int = 10, scope: str | None = None) -> str:
        """Follow the "next page" control the model picked, collecting every page; stops on repeats or when
        the control disappears/gets disabled. Returns all pages' text."""
        try:
            loc = await self._locate(next_ref)
            name = (await loc.evaluate("e => (e.innerText || e.getAttribute('aria-label') || e.value || '').trim()")) or ""
            href = await self._attr(loc, "href")
        except ToolError as e:
            return f"Fehler: {e}"
        pages, seen = [], set()
        o = await self._observe_text()
        for i in range(max_pages):
            h = hashlib.sha1(o.text.encode()).hexdigest()
            if h in seen:
                pages.append(f"--- Seite {i + 1}: identisch mit einer vorherigen Seite, Ende ---")
                break
            seen.add(h)
            pages.append(f"--- Seite {i + 1} ({o.url}) ---\n{o.text}")
            nxt = await self._find_like(name, href is not None)
            if nxt is None:
                break
            try:
                if href is not None and (h2 := await self._attr(nxt, "href")):
                    await self.s.page.goto(self.s.absolute(h2), wait_until="domcontentloaded")
                else:
                    await self._click(nxt)
                await guards.after_action(self.s)
            except Exception as e:
                pages.append(f"--- Abbruch: {str(e)[:200]} ---")
                break
            o = await self._observe_text()
        self.obs = o
        text = "\n".join(pages)
        return text if len(text) < 60_000 else text[:60_000] + "\n[… gekürzt]"

    async def _find_like(self, name: str, is_link: bool) -> Locator | None:
        """The control with the same visible name on the new page (same role), enabled."""
        o = await observe(self.s.page)
        self.s.remember(o.text)
        kind = "link" if is_link else r"(?:button|link|clickable)"
        m = re.search(rf'\[((?:f\d+)?e\d+)\] {kind} "{re.escape(name)}"(?![^\n]*deaktiviert)', o.text)
        if not m:
            return None
        try:
            return await self._locate(m.group(1))
        except ToolError:
            return None

    async def tabs(self) -> str:
        lines = [f"0: {self.s.page.url} (aktiv)"] + [f"{i}: {p.url}" for i, p in enumerate(self.s.popups, 1) if not p.is_closed()]
        return "\n".join(lines)

    async def switch_tab(self, index: int) -> str:
        pages = [self.s.page] + [p for p in self.s.popups if not p.is_closed()]
        if not 0 <= index < len(pages):
            return f"Kein Tab {index}. {await self.tabs()}"
        self.s.page = pages[index]
        await self.s.page.bring_to_front()
        self.obs = None
        return await self.observe()

    async def confirm_next_dialog(self) -> str:
        """The next native browser confirm/alert will be accepted (e.g. the final binding submission)."""
        self.s.arm_dialog_accept()
        return "Der nächste Browser-Dialog wird bestätigt."

    def take_images(self) -> list[str]:
        imgs, self.images = self.images, []
        return [base64.b64encode(i).decode() for i in imgs]


# ---- OpenAI function-calling schemas -------------------------------------------------------------------------
def _fn(name, desc, props=None, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": list(required)}}}


_REF = {"type": "string", "description": "Ref aus der letzten Beobachtung, z. B. e12 oder f1e7"}
TOOL_SPECS = [
    _fn("observe", "Aktuelle Seite als Text mit Refs lesen (alle Frames). scope=Ref liest nur ein Element; offset blättert in langem Text.",
        {"scope": _REF, "offset": {"type": "integer"}}),
    _fn("click", "Element anklicken.", {"ref": _REF}, ["ref"]),
    _fn("open_link", "Linkziel im selben Tab öffnen (statt Popup/neuem Fenster). Auch für iframes (öffnet deren src).", {"ref": _REF}, ["ref"]),
    _fn("goto", "Eine URL öffnen, die auf einer Seite angezeigt wurde.", {"url": {"type": "string"}}, ["url"]),
    _fn("back", "Browser zurück."),
    _fn("fill", "Text in ein Feld eintragen (optional mit Enter absenden).",
        {"ref": _REF, "text": {"type": "string"}, "submit": {"type": "boolean"}}, ["ref", "text"]),
    _fn("search", "Suchbegriff in ein Suchfeld eintragen und absenden.", {"ref": _REF, "query": {"type": "string"}}, ["ref", "query"]),
    _fn("select", "Option in einer Auswahlliste wählen (Beschriftung oder Wert).", {"ref": _REF, "option": {"type": "string"}}, ["ref", "option"]),
    _fn("check", "Checkbox/Radio setzen.", {"ref": _REF, "checked": {"type": "boolean"}}, ["ref"]),
    _fn("set_range", 'Schieberegler auf Wert oder "max" stellen (z. B. "ganz nach rechts ziehen").',
        {"ref": _REF, "value": {"type": "string"}}, ["ref"]),
    _fn("hover", "Maus über ein Element (Hover-Menüs, Tooltips).", {"ref": _REF}, ["ref"]),
    _fn("expand", "Aufklappbaren Bereich/Menü öffnen.", {"ref": _REF}, ["ref"]),
    _fn("context_menu", "Rechtsklick-Menü an einem Element öffnen.", {"ref": _REF}, ["ref"]),
    _fn("press", "Taste drücken (z. B. Enter, Escape, Shift+F10), optional auf einem Element.",
        {"key": {"type": "string"}, "ref": _REF}, ["key"]),
    _fn("read_canvas", "Text eines Canvas/Bildes lesen (aria-label oder Bildausschnitt).", {"ref": _REF}, ["ref"]),
    _fn("screenshot", "Screenshot der Seite oder eines Elements ansehen.", {"ref": _REF}),
    _fn("read_document", "Dokument hinter einem Link laden und als Text lesen (CSV, TXT, HTML, ZIP, PDF).", {"ref": _REF}, ["ref"]),
    _fn("paginate", "Einer gewählten 'Nächste Seite'-Steuerung folgen und alle Seiten gesammelt zurückgeben.",
        {"next_ref": _REF, "max_pages": {"type": "integer"}}, ["next_ref"]),
    _fn("tabs", "Offene Tabs/Fenster auflisten."),
    _fn("switch_tab", "Zu einem Tab wechseln.", {"index": {"type": "integer"}}, ["index"]),
    _fn("confirm_next_dialog", "Den nächsten Browser-Bestätigungsdialog akzeptieren (nur für die verbindliche Abgabe)."),
]


async def call_tool(tools: BrowserTools, name: str, args: dict) -> str:
    fn = getattr(tools, name, None)
    if name.startswith("_") or not callable(fn) or name not in {t["function"]["name"] for t in TOOL_SPECS}:
        return f"Unbekanntes Werkzeug: {name}"
    try:
        return await fn(**(args or {}))
    except TypeError as e:
        return f"Falsche Argumente für {name}: {e}"

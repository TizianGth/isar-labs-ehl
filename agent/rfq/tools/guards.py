"""Guards: generic mechanics that run around every model action, in every frame. No site knowledge —
detection by roles, form structure and common UI vocabulary only.

- settle         wait for the DOM to calm down (delayed banners/dialogs)
- close_overlays close nuisance modals (survey, notices) via their close button, never via "accept and continue";
                 unknown close wording → Clef picks among the safe buttons (decide.py), once per overlay text
- consent        cookie walls/banners: prefer "necessary only"
- relogin        a login form appeared (session timeout) → log in again and return to the last page
- error_retry    5xx / "temporarily unavailable" pages → retry with backoff
Gates (slider + checkbox + receipt number) are NOT solved here: observe() shows them, the model works them with
set_range/check/fill.
"""
from __future__ import annotations

import asyncio
import re

from playwright.async_api import Frame, Page

from . import decide
from .session import SiteSession

SETTLE_JS = """(quietMs, maxMs) => new Promise((resolve) => {
  let timer; const done = () => { obs.disconnect(); resolve(true); };
  const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(done, quietMs); });
  obs.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  timer = setTimeout(done, quietMs); setTimeout(done, maxMs);
})"""

# Marks the button a guard wants to click with data-agent-guard and describes what it found.
OVERLAY_JS = r"""() => {
  const norm = (s) => (s || "").replace(/[​-‍⁠﻿­]/g, "").replace(/\s+/g, " ").trim();
  const CLOSE = /^(×|✕|✖|x|schlie(ß|ss)en|close|später|spaeter|nicht jetzt|nein,? danke|no,? thanks|ausblenden|hinweis (schlie(ß|ss)en|ausblenden)|dismiss|(ok(ay)?,? )?verstanden|ok(ay)?|alles klar|got it|zur kenntnis genommen)$/i;
  const DANGER = /fortfahren|umfrage|teilnehmen|bewerten|feedback geben|weiter zur|continue|survey|jetzt starten/i;
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden"; };
  const nameOf = (b) => norm(b.getAttribute("aria-label") || b.innerText || b.value || b.title);
  const found = [];
  const cands = new Set([...document.querySelectorAll("dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true]")]);
  // fixed overlays covering a large part of the viewport
  for (const el of document.body ? document.body.querySelectorAll("div,section,aside") : []) {
    const s = getComputedStyle(el);
    if ((s.position === "fixed" || s.position === "sticky") && Number(s.zIndex) >= 10) {
      const r = el.getBoundingClientRect();
      if (r.width * r.height > 0.35 * innerWidth * innerHeight) cands.add(el);
    }
  }
  for (const el of cands) {
    if (!vis(el) || el.closest("[data-agent-keep]")) continue;
    const modal = (el.tagName === "DIALOG" && el.matches(":modal")) || el.getAttribute("aria-modal") === "true" ||
                  getComputedStyle(el).position === "fixed";
    if (!modal) continue;
    const text = norm(el.innerText).slice(0, 300);
    const inputs = [...el.querySelectorAll("input:not([type=hidden]):not([type=submit]):not([type=button]),select,textarea")].filter(vis);
    const buttons = [...el.querySelectorAll("button,[role=button],input[type=button],input[type=submit],a[href]")];
    const consent = /cookie|einwilligung|datenschutzeinstellungen|consent/i.test(text);
    let pick = null, kind = "form";
    if (consent) {
      kind = "consent";
      pick = buttons.find((b) => /notwendig|erforderlich|necessary|essential|ablehnen|reject/i.test(nameOf(b)))
          || buttons.find((b) => /akzeptieren|zustimmen|accept|einverstanden/i.test(nameOf(b)) && !DANGER.test(nameOf(b)));
    } else if (!inputs.length) {
      kind = "notice";
      pick = buttons.find((b) => b.tagName !== "A" && CLOSE.test(nameOf(b)) && !DANGER.test(nameOf(b)));
    }
    const entry = {kind, text, close: pick ? nameOf(pick) : null};
    found.push(entry);
    if (pick) { pick.setAttribute("data-agent-guard", String(found.length)); entry.mark = String(found.length); }
    else if (kind === "notice") {
      // unknown wording: offer the safe candidates to a decision model (never "continue/survey", never real links)
      entry.options = buttons.filter((b) => vis(b) && nameOf(b) && !DANGER.test(nameOf(b)) &&
          !(b.tagName === "A" && !/^(#|javascript:)/i.test(b.getAttribute("href") || "")))
        .slice(0, 8).map((b, i) => { const m = found.length + "o" + i; b.setAttribute("data-agent-guard", m); return {mark: m, name: nameOf(b)}; });
    }
  }
  return found;
}"""

# Full-page cookie walls (not overlays): page talks about cookies and offers a consent button.
CONSENT_PAGE_JS = r"""() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const body = norm(document.body ? document.body.innerText : "");
  if (!/cookie|einwilligung|consent/i.test(body) || body.length > 4000) return null;
  const bs = [...document.querySelectorAll("button,input[type=submit],[role=button]")].filter((b) => b.offsetParent !== null);
  const nm = (b) => norm(b.getAttribute("aria-label") || b.innerText || b.value);
  const pick = bs.find((b) => /notwendig|erforderlich|necessary|essential|ablehnen|reject/i.test(nm(b)))
            || bs.find((b) => /(cookies?.*(akzeptieren|zulassen))|(akzeptieren|zustimmen|accept)/i.test(nm(b)) && !/fortfahren|continue/i.test(nm(b)));
  if (!pick) return null;
  pick.setAttribute("data-agent-guard", "consent");
  return nm(pick);
}"""

# A masked TAN/PIN/one-time code is not a login: the site password must never be typed there.
PASSWORD_JS = r"""
  const vis = (el) => el.offsetParent !== null || el.getClientRects().length > 0;
  const OTP = /\btan\b|pin\b|otp|one.?time|einmal|signatur|bestätigungscode|sicherheitscode/i;
  const words = (el) => [el.name, el.id, el.placeholder, el.getAttribute("aria-label"),
    ...[...(el.labels || [])].map((l) => l.textContent)].join(" ");
  const pw = [...document.querySelectorAll("input[type=password]")].find((el) =>
    vis(el) && el.autocomplete !== "one-time-code" && !OTP.test(words(el)));
"""

LOGIN_JS = r"""() => {""" + PASSWORD_JS + r"""
  if (!pw) return null;
  const scope = pw.form || document;
  const fields = [...scope.querySelectorAll("input:not([type=hidden]):not([type=password]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button])")].filter(vis);
  const user = fields.reverse().find((f) => (f.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING)) || fields[0];
  if (user) user.setAttribute("data-agent-guard", "user");
  pw.setAttribute("data-agent-guard", "password");
  const name = (b) => (b.innerText || b.value || b.getAttribute("aria-label") || "").trim();
  const NOT = /vergessen|forgot|registrier|register|zurücksetzen|reset|abbrechen|cancel|hilfe|help/i;
  const btns = [...scope.querySelectorAll("button,input[type=submit],[role=button]")].filter((b) => vis(b) && !NOT.test(name(b)));
  const sub = btns.find((b) => /anmelden|einloggen|login|log in|sign in/i.test(name(b)))
           || btns.find((b) => b.type === "submit") || btns[0];
  if (sub) sub.setAttribute("data-agent-guard", "submit");
  return {user: !!user, submit: !!sub};
}"""

ERROR_JS = r"""() => {
  const t = (document.body ? document.body.innerText : "").replace(/\s+/g, " ").trim();
  const ERR = /vorübergehend nicht verfügbar|temporarily unavailable|service unavailable|bad gateway|gateway time-?out|interner (server)?fehler|internal server error|laden fehlgeschlagen|seite konnte nicht geladen|zu viele anfragen|too many requests/i;
  // generic wording also appears as an inline form message; reloading a form would lose its entries
  const SOFT = /etwas ist schief ?gelaufen|ein fehler ist aufgetreten|something went wrong|an error (has )?occurred/i;
  const form = document.querySelector("input:not([type=hidden]),select,textarea");
  const empty = document.readyState === "complete" && t.length === 0 && !document.querySelector("iframe,frame,canvas,img,input");
  if (!(empty || (t.length < 2500 && (ERR.test(t) || (!form && SOFT.test(t)))))) return null;
  const retry = [...document.querySelectorAll("a[href],button,[role=button]")].find((b) =>
      /erneut versuchen|nochmal versuchen|neu laden|wiederholen|retry|try again|reload/i.test(b.innerText || b.getAttribute("aria-label") || ""));
  if (retry) retry.setAttribute("data-agent-guard", "retry");
  return {text: t.slice(0, 160), retry: !!retry, empty};
}"""


def _frames(page: Page) -> list[Frame]:
    return [page.main_frame] + [f for f in page.frames if f != page.main_frame]


async def settle(page: Page, quiet_ms: int = 350, max_ms: int = 1500) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except Exception:
        pass
    try:
        await page.evaluate(SETTLE_JS, quiet_ms, max_ms)
    except Exception:
        pass


async def _click_mark(frame: Frame, mark: str) -> bool:
    loc = frame.locator(f'[data-agent-guard="{mark}"]').first
    try:
        await loc.click(timeout=3000)
    except Exception:
        try:  # tiny / displaced close buttons: click via DOM, not via pixels
            await loc.dispatch_event("click", timeout=2000)
        except Exception:
            return False
    try:
        await frame.evaluate("m => document.querySelectorAll('[data-agent-guard]').forEach(e => e.removeAttribute('data-agent-guard'))", mark)
    except Exception:
        pass
    return True


async def _choose_close(s: SiteSession, overlay: dict) -> str | None:
    """Name of the button that dismisses an overlay with unknown wording, decided once per overlay text."""
    key = overlay["text"]
    if key not in s.overlay_choices:
        options = {f"b{i}": o["name"] for i, o in enumerate(overlay["options"])}
        pick = await decide.choose(
            {"dialog": overlay["text"], "buttons": options},
            "This dialog covers a web page. Which button only closes or dismisses it, without accepting, "
            "starting or confirming anything and without leaving the page? Answer none if no button does that.",
            options)
        s.overlay_choices[key] = options[pick] if pick else None
        s.log({"agent": s.name, "event": "clef", "dialog": key[:120], "options": list(options.values()),
               "choice": s.overlay_choices[key]})
    return s.overlay_choices[key]


async def close_overlays(s: SiteSession, rounds: int = 3) -> list[str]:
    """Close nuisance overlays in every frame. Overlays with form fields are left to the model."""
    notes: list[str] = []
    for _ in range(rounds):
        acted = False
        for frame in _frames(s.page):
            try:
                found = await frame.evaluate(OVERLAY_JS)
            except Exception:
                continue
            for o in found:
                if not o.get("mark") and o.get("options"):
                    name = await _choose_close(s, o)
                    o["mark"] = next((x["mark"] for x in o["options"] if x["name"] == name), None)
                    o["close"] = f"{name}, Clef" if name else None
                if o.get("mark") and await _click_mark(frame, o["mark"]):
                    acted = True
                    notes.append(f'Overlay geschlossen ({o["kind"]}, Button "{o["close"]}"): {o["text"][:80]}')
        if not acted:
            break
        await asyncio.sleep(0.2)
    for n in notes:
        s.note(n)
    return notes


async def consent(s: SiteSession) -> str | None:
    for frame in _frames(s.page):
        try:
            label = await frame.evaluate(CONSENT_PAGE_JS)
        except Exception:
            continue
        if label and await _click_mark(frame, "consent"):
            await settle(s.page)
            s.note(f'Cookie-Abfrage bestätigt: "{label}"')
            return label
    return None


async def relogin(s: SiteSession, max_logins: int = 6) -> bool:
    """Fill a visible login form with the run's credentials, then return to the last good URL."""
    if not s.credentials or s.logins >= max_logins:
        return False
    for frame in _frames(s.page):
        try:
            info = await frame.evaluate(LOGIN_JS)
        except Exception:
            continue
        if not info:
            continue
        user = s.credentials.get("username") or s.credentials.get("user") or s.credentials.get("email") or ""
        pw = s.credentials.get("password") or ""
        if info["user"]:
            await frame.locator('[data-agent-guard="user"]').fill(user)
        await frame.locator('[data-agent-guard="password"]').fill(pw)
        if info["submit"]:
            await frame.locator('[data-agent-guard="submit"]').click()
        else:
            await frame.locator('[data-agent-guard="password"]').press("Enter")
        s.logins += 1
        await settle(s.page)
        s.note(f"Angemeldet (Anmeldung {s.logins})")
        await consent(s)
        back, s.intended_url = s.intended_url or s.last_good_url, None
        if back and s.page.url.split("#")[0] != back and "login" not in back:
            try:
                await s.page.goto(back, wait_until="domcontentloaded")
                await settle(s.page)
                s.note(f"Zurück zur letzten Seite: {back}")
            except Exception:
                pass
        return True
    return False


async def error_retry(s: SiteSession, attempts: int = 4) -> bool:
    """True if the page is (now) fine."""
    for i in range(attempts + 1):
        try:
            info = await s.page.main_frame.evaluate(ERROR_JS)
        except Exception:
            info = None
        bad_status = s.last_status is not None and s.last_status >= 500
        if not info and not bad_status:
            return True
        if i == attempts:
            s.note(f"Fehlerseite bleibt nach {attempts} Versuchen: {(info or {}).get('text', s.last_status)}")
            return False
        await asyncio.sleep(0.5 * 2 ** i)
        s.note(f"Fehlerseite erkannt ({(info or {}).get('text') or s.last_status}) — Versuch {i + 1}")
        s.last_status = None
        try:
            if info and info.get("retry"):
                await _click_mark(s.page.main_frame, "retry")
            else:
                await s.page.reload(wait_until="domcontentloaded")
        except Exception:
            pass
        await settle(s.page)
    return False


async def after_action(s: SiteSession) -> None:
    """Run after every navigation/action: settle → error retry → relogin → consent → overlays."""
    await settle(s.page)
    await error_retry(s)
    if await relogin(s):
        await error_retry(s)
    await consent(s)
    await close_overlays(s)
    if re.match(r"https?://", s.page.url) and s.same_site(s.page.url) and not await _has_login_form(s.page):
        s.last_good_url = s.page.url


async def _has_login_form(page: Page) -> bool:
    try:
        return await page.main_frame.evaluate("() => {" + PASSWORD_JS + " return !!pw; }")
    except Exception:
        return False


async def before_action(s: SiteSession) -> None:
    await close_overlays(s)

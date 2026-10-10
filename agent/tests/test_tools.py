"""Browser tools against a synthetic mini-site (served via routing, no network). Values are made up."""
import asyncio
import io
import sys
import zipfile
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parents[1]))
from rfq.tools.actions import BrowserTools  # noqa: E402
from rfq.tools.observe import observe  # noqa: E402
from rfq.tools.session import SiteSession  # noqa: E402

BASE = "http://site.test"
LOGIN = """<form method=post action=/login><label>Benutzer <input name=u></label>
<label>Kennwort <input type=password name=p></label><button>Anmelden</button></form>"""

TRAPS = """<html><head><title>Akte</title><style>.row{display:flex}.b{order:1}.a{order:2}
#lbl::before{content:attr(data-label)} .menu{display:none}</style></head><body>
<h1>Akte <span role=img aria-label="AZ-77">A​Z​-​77</span></h1>
<p>Status: 07</p><p aria-hidden=true hidden style="display:none!important">Status: 01</p>
<button aria-describedby=tip1>Datum</button><span id=tip1 role=tooltip style="display:none">03.02.2030, 10:00</span>
<details><summary>Antwort</summary><p>Menge neu: 42 Stück</p></details>
<canvas aria-label="Belegnummer: 12345"></canvas>
<div class=row><span class=a>zweitens</span><span class=b>erstens</span></div>
<ul><li aria-posinset=2>Zeile B</li><li aria-posinset=1>Zeile A</li></ul>
<span id=lbl data-label="Feldname"></span>
<time datetime="2030-02-03T09:00:00Z">03.02.</time>
<table><tr><th>Pos</th><th>Menge</th></tr><tr><td>1</td><td>5</td></tr></table>
<select aria-label=Art><option>Haupt</option><option selected>Neben</option></select>
<button disabled title="Funktion nicht verfügbar">Öffnen</button><a href="/doc/2">Öffnen</a>
<a href="/doc.zip">ZIP</a> <a href="/list?page=1">Liste</a> <a href="/flaky">Flaky</a>
<dialog id=d aria-label=Umfrage><p>Wie zufrieden?</p><a href="/survey">Akzeptieren und fortfahren</a>
<button aria-label="Schließen" onclick="this.closest('dialog').close()">×</button></dialog>
<script>setTimeout(() => document.getElementById('d').showModal(), 300)</script>
</body></html>"""

GATE = """<html><body><h1>Systemmitteilung</h1><canvas aria-label="Empfangsnummer: X-9"></canvas>
<input type=range min=0 max=100 value=0 aria-label="Zum Entsperren ziehen">
<label><input type=checkbox id=bot> Ich bin kein Roboter</label><p role=status id=st>Gesperrt</p>
<fieldset disabled inert><input name=r aria-label=Nummer><button>Weiter</button></fieldset>
<script>const s=document.querySelector('[type=range]'),c=document.getElementById('bot'),f=document.querySelector('fieldset');
const u=()=>{const ok=s.value==='100'&&c.checked;f.disabled=!ok;f.inert=!ok;document.getElementById('st').textContent=ok?'Entsperrt':'Gesperrt'};
s.addEventListener('input',u);c.addEventListener('change',u);</script></body></html>"""


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lv.csv", "Position;Menge\n1;5\n2;7\n")
        z.writestr("info.txt", "Hinweis")
    return buf.getvalue()


class Site:
    def __init__(self):
        self.logged_in = False
        self.flaky = 2

    async def handle(self, route):
        url, req = route.request.url, route.request
        path = url[len(BASE):] or "/"
        if path.startswith("/login") and req.method == "POST":
            self.logged_in = True
            return await route.fulfill(body=TRAPS, content_type="text/html; charset=utf-8")
        if not self.logged_in:
            return await route.fulfill(body=LOGIN, content_type="text/html; charset=utf-8")
        if path == "/flaky":
            if self.flaky:
                self.flaky -= 1
                return await route.fulfill(status=503, body="<h1>Dienst vorübergehend nicht verfügbar</h1>"
                                           "<a href=/flaky>Erneut versuchen</a>", content_type="text/html; charset=utf-8")
            return await route.fulfill(body="<p>Daten da</p>", content_type="text/html; charset=utf-8")
        if path == "/doc.zip":
            return await route.fulfill(body=_zip(), content_type="application/zip")
        if path.startswith("/list"):
            n = int(path.split("=")[-1])
            nxt = f'<a href="/list?page={n + 1}">Nächste Seite</a>' if n < 3 else '<a href="/list?page=3">Nächste Seite</a>'
            return await route.fulfill(body=f"<p>Eintrag {n}</p><p>Seite {n} von 3</p>{nxt}", content_type="text/html; charset=utf-8")
        if path == "/gate":
            return await route.fulfill(body=GATE, content_type="text/html; charset=utf-8")
        return await route.fulfill(body=TRAPS, content_type="text/html; charset=utf-8")


def run(coro_fn):
    async def main():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
            site = Site()
            await ctx.route(f"{BASE}/**", site.handle)
            s = await SiteSession.create("t", {"url": BASE, "credentials": {"username": "u", "password": "p"}}, ctx,
                                         log=lambda *_: None)
            t = BrowserTools(s)
            await s.page.goto(BASE + "/")
            try:
                return await coro_fn(t, s)
            finally:
                await browser.close()
    return asyncio.run(main())


def test_login_overlay_and_serializer():
    async def body(t, s):
        first = await t.observe()  # logs in
        await asyncio.sleep(0.6)  # delayed survey dialog opens
        return first + "\n" + await t.observe()
    out = run(body)
    assert "Angemeldet" in out
    assert "Overlay geschlossen" in out and out.count("MODALER DIALOG") <= 1 and "MODALER DIALOG" not in out.split("Overlay geschlossen")[1]
    assert "Status: 07" in out and "Status: 01" not in out
    assert "[Tooltip: 03.02.2030, 10:00]" in out
    assert "Menge neu: 42 Stück" in out and "eingeklappt" in out
    assert "[Bild: AZ-77]" in out and "​" not in out
    assert "[Bild: Belegnummer: 12345]" in out
    assert out.index("erstens") < out.index("zweitens")
    assert out.index("Zeile A") < out.index("Zeile B")
    assert "Feldname" in out
    assert "(2030-02-03T09:00:00Z)" in out
    assert "| Pos | Menge |" in out and "| 1 | 5 |" in out
    assert 'select "Art" = "Neben"' in out
    assert 'button "Öffnen" [Titel: Funktion nicht verfügbar]' not in out or "deaktiviert" in out
    assert 'link "Öffnen" → /doc/2' in out


def test_error_retry_and_document_and_pagination():
    async def body(t, s):
        o = await t.observe()
        flaky = o.split('link "Flaky"')[0].rsplit("[", 1)[1].split("]")[0]
        r1 = await t.click(flaky)
        o = await t.goto(BASE + "/")
        zipref = o.split('link "ZIP"')[0].rsplit("[", 1)[1].split("]")[0]
        doc = await t.read_document(zipref)
        listref = o.split('link "Liste"')[0].rsplit("[", 1)[1].split("]")[0]
        await t.open_link(listref)
        o = await t.observe()
        nxt = o.split('link "Nächste Seite"')[0].rsplit("[", 1)[1].split("]")[0]
        pages = await t.paginate(nxt)
        return r1, doc, pages
    r1, doc, pages = run(body)
    assert "Daten da" in r1 and "Fehlerseite erkannt" in r1
    assert "| 2 | 7 |" in doc and "info.txt" in doc
    assert "Eintrag 1" in pages and "Eintrag 3" in pages and "identisch" in pages


def test_gate_with_slider_checkbox_and_receipt():
    async def body(t, s):
        await t.goto(BASE + "/")  # start page is always allowed
        s.seen_urls.add(BASE + "/gate")
        o = await t.goto("/gate")
        ref = lambda kind, name: o.split(f'{kind} "{name}"')[0].rsplit("[", 1)[1].split("]")[0]
        assert "[gesperrt bis Freigabe:]" in o and "Empfangsnummer: X-9" in o
        await t.set_range(ref("slider", "Zum Entsperren ziehen"))
        r = await t.check(ref("checkbox", "Ich bin kein Roboter"))
        return o, r
    o, r = run(body)
    assert "Entsperrt" in r


def test_goto_refuses_unseen_urls():
    async def body(t, s):
        return await t.goto("/secret-admin")
    assert "keiner Seite angezeigt" in run(body)


def test_dialog_dismissed_unless_armed():
    async def body(t, s):
        await s.page.evaluate("() => { window.r1 = confirm('a'); }")
        await asyncio.sleep(0.1)
        await t.confirm_next_dialog()
        await s.page.evaluate("() => { window.r2 = confirm('b'); }")
        await asyncio.sleep(0.1)
        return await s.page.evaluate("() => [window.r1, window.r2]")
    assert run(body) == [False, True]


@pytest.mark.parametrize("html,absent", [
    ('<div style="visibility:hidden">Köder 99</div><p>echt</p>', "Köder 99"),
    ('<span aria-hidden="true">Köder 99</span><p>echt</p>', "Köder 99"),
    ('<span role=tooltip aria-hidden=true id=x>Köder 99</span><p aria-describedby=x>echt</p>', "Köder 99"),
])
def test_decoys_dropped(html, absent):
    async def body(t, s):
        await s.page.set_content(html)
        return (await observe(s.page)).text
    out = run(body)
    assert "echt" in out and absent not in out

"""Generalization: the same information as other software would present it (the private dataset uses different
websites). Each case is a pattern the public sites do not use. Values are made up."""
import pytest

from test_tools import run
from rfq.tools import guards
from rfq.tools.observe import observe


async def _show(s, html: str) -> str:
    await s.page.set_content(html)
    return (await observe(s.page)).text


# ---- serializer -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("html,expected", [
    # SPA buttons: plain divs with JS listeners, recognisable only by their pointer cursor
    ('<div id=x style="cursor:pointer">Unterlagen</div><script>x.addEventListener("click",()=>{})</script>',
     'clickable "Unterlagen"'),
    # ARIA grid made of divs instead of <table>
    ('<div role=table><div role=row><span role=columnheader>Pos</span><span role=columnheader>Menge</span></div>'
     '<div role=row><span role=cell>9</span><span role=cell>14</span></div></div>', "| 9 | 14 |"),
    # tooltip via aria-description instead of aria-describedby
    ('<button aria-description="gültig seit 02.03.2031">Stand</button>', "gültig seit 02.03.2031"),
    # content inside an open shadow root (web components)
    ('<x-card></x-card><script>customElements.define("x-card", class extends HTMLElement {'
     'constructor(){super(); this.attachShadow({mode:"open"}).innerHTML="<p>Bohrung 63 mm</p>"}})</script>',
     "Bohrung 63 mm"),
    # CSV shown in a <pre>: rows stay separate lines
    ("<pre>Pos;Menge\n7;3\n8;4</pre>", "Pos;Menge\n7;3\n8;4"),
    # definition list: label and value stay together
    ("<dl><dt>Hub</dt><dd>410 mm</dd></dl>", "Hub"),
])
def test_other_markup_is_readable(html, expected):
    async def body(t, s):
        return await _show(s, html)
    out = run(body)
    assert expected in out, out


@pytest.mark.parametrize("html", [
    '<style>.x{display:none}</style><p class=x>Köder 77</p><p>echt</p>',            # hidden by stylesheet class
    '<p style="opacity:0">Köder 77</p><p>echt</p>',                                 # invisible but laid out
    '<div hidden><div><p>Köder 77</p></div></div><p>echt</p>',                      # hidden ancestor
])
def test_other_decoy_hiding_is_dropped(html):
    async def body(t, s):
        return await _show(s, html)
    out = run(body)
    assert "echt" in out and "Köder 77" not in out, out


# ---- guards -----------------------------------------------------------------------------------------------------
NOTICE = """<dialog id=d><p>Neue Funktionen im Portal.</p><a href="/tour">Zur Tour</a>
<button onclick="this.closest('dialog').close()">{label}</button></dialog><p>Inhalt</p>
<script>document.getElementById('d').showModal()</script>"""


@pytest.mark.parametrize("label", ["Schließen", "Verstanden", "OK", "Alles klar", "Got it", "Ausblenden"])
def test_notice_closed_whatever_the_wording(label):
    async def body(t, s):
        await s.page.set_content(NOTICE.format(label=label))
        await guards.close_overlays(s)
        return await s.page.evaluate("() => document.getElementById('d').open")
    assert run(body) is False


@pytest.mark.parametrize("text", ["Etwas ist schiefgelaufen. Bitte erneut versuchen.",
                                  "Something went wrong. Try again.", "Ein Fehler ist aufgetreten"])
def test_error_wording_variants_detected(text):
    async def body(t, s):
        await s.page.set_content(f"<h1>{text}</h1><button>Erneut versuchen</button>")
        return await s.page.main_frame.evaluate(guards.ERROR_JS)
    assert run(body), text


def test_login_without_form_picks_the_login_button():
    """No <form>, type=button everywhere, a 'forgot password' button first."""
    html = """<div><input aria-label=Benutzer id=u><input type=password aria-label=Passwort id=p>
    <button type=button onclick="window.clicked='forgot'">Passwort vergessen?</button>
    <button type=button onclick="window.clicked='login:'+u.value+'/'+p.value">Anmelden</button></div>"""
    async def body(t, s):
        await s.page.set_content(html)
        await guards.relogin(s)
        return await s.page.evaluate("() => window.clicked")
    assert run(body) == "login:u/p"


@pytest.mark.parametrize("field", [
    '<input type=password name=tan aria-label="Signatur-TAN">',
    '<input type=password autocomplete=one-time-code aria-label="Code">',
    '<label>Einmal-PIN <input type=password id=pin></label>',
])
def test_relogin_never_types_the_password_into_a_tan_field(field):
    """A signing step can mask its TAN like a password; the site password must never go there."""
    html = f"<h2>Prüfen und signieren</h2><input aria-label=Firma value=Muster>{field}<button>Abgeben</button>"
    async def body(t, s):
        await s.page.set_content(html)
        done = await guards.relogin(s)
        vals = await s.page.evaluate("() => [...document.querySelectorAll('input')].map(i => i.value)")
        return done, vals
    done, vals = run(body)
    assert done is False and "p" not in vals and "u" not in vals

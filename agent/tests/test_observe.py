"""The page serializer against synthetic trap HTML (no scenario values)."""
import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

JS = (Path(__file__).parents[1] / "rfq/browser/observe.js").read_text()

HTML = """<html><body>
<h1>Seite</h1>
<p>Status <output aria-label="Status: 03">03</output><span aria-hidden="true" hidden style="display:none!important">01</span></p>
<span id="d"><button aria-describedby="tip">Eingang</button><span id="tip" role="tooltip" style="display:none">01.02.2030, 10:00</span></span>
<details><summary>Antwort</summary><p>Menge neu: 7 Stück</p></details>
<canvas aria-label="Belegnummer: 4711">Belegnummer: 4711</canvas>
<span role="img" aria-label="AB-12">A&#8203;B&#8203;-&#8203;1&#8203;2</span>
<div role="list" style="display:flex;flex-direction:column">
  <div role="listitem" aria-posinset="2" style="order:1">zweitens</div><div role="listitem" aria-posinset="1" style="order:0">erstens</div>
</div>
<nav><button>Menü</button><ul style="display:none"><li><a href="/x">Versteckter Menüpunkt</a></li></ul></nav>
<p style="display:none">Geheimer Köderwert 999</p>
<dialog><a href="/survey">Akzeptieren und fortfahren</a></dialog>
<label>Menge <input name="q" value="3"></label>
<select aria-label="Art"><option>Haupt</option><option selected>Neben</option></select>
<time datetime="2030-02-01T09:00:00Z">01.02.2030</time>
</body></html>"""


@pytest.fixture(scope="module")
def text():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(HTML)
            result = await page.evaluate(JS, {"prefix": "e"})
            await browser.close()
            return result["text"]
    return asyncio.run(run())


def test_decoys_dropped(text):
    assert "03" in text and "01\n" not in text and "Köderwert" not in text and "Akzeptieren" not in text


def test_hidden_values_surface(text):
    assert "[info: 01.02.2030, 10:00]" in text
    assert "Menge neu: 7 Stück" in text
    assert "Belegnummer: 4711" in text and "AB-12" in text and "​" not in text
    assert "(2030-02-01T09:00:00Z)" in text


def test_logical_order_and_controls(text):
    assert text.index("erstens") < text.index("zweitens")
    assert 'link "Versteckter Menüpunkt" -> /x (hidden-until-hover/open)' in text
    assert 'input[text] "Menge" value="3"' in text
    assert "*Neben*" in text

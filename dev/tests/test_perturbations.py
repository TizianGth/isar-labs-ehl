"""G-3: the page serializer reads the same facts when a known page looks different (labels, nesting, row order).

The private dataset runs on other software with other layouts. These tests take saved public pages
(dev/fixtures/public-1, dev only) and change their look without changing their content; the set of fact lines
(lines with digits, refs removed) must stay the same, and hidden decoys must stay hidden.

    uv run --project agent pytest -q dev/tests/test_perturbations.py
"""
import asyncio
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
OBSERVE_JS = (ROOT / "agent" / "rfq" / "browser" / "observe.js").read_text()
FIXTURES = ROOT / "dev" / "fixtures" / "public-1"
PAGES = [
    "erp_mm03_planning_blocked_hidden_decoy.html",
    "erp_mmbe_stock_free_vs_blocked_canvas.html",
    "erp_vk13_scales_deDE.html",
    "erp_search_catalog_group_two_lookalikes.html",
    "supplier_spec_revisions_tooltip_status.html",
    "supplier_spec_viewer_current_p1_hidden_decoy.html",
    "mail_msg_conditional_discount_draft_injection.html",
    "tender_qa_content_page2.html",
]
RENAMES = {"Schließen": "Fenster zu", "Weiter": "Fortfahren", "Öffnen": "Anzeigen", "Suchen": "Finden",
           "Übernehmen": "Anwenden", "Zurück": "Rückwärts"}

RELABEL = """(renames) => {
  for (const el of document.querySelectorAll('button, a, [role=button], summary, label')) {
    for (const node of el.childNodes) {
      if (node.nodeType !== 3) continue;
      let text = node.textContent;
      for (const [from, to] of Object.entries(renames)) text = text.split(from).join(to);
      node.textContent = text;
    }
  }
}"""
WRAP = """() => {
  const outer = document.createElement('div'); outer.className = 'x-shell';
  const inner = document.createElement('section'); inner.className = 'x-content';
  while (document.body.firstChild) inner.appendChild(document.body.firstChild);
  const nav = document.createElement('nav');
  nav.innerHTML = '<a href="#home">Startseite</a> <a href="#help">Hilfe</a> <a href="#about">Impressum</a>';
  outer.appendChild(nav); outer.appendChild(inner); document.body.appendChild(outer);
}"""
REORDER = """() => {
  for (const body of document.querySelectorAll('tbody')) {
    const rows = [...body.children]; rows.reverse().forEach(r => body.appendChild(r));
  }
  for (const list of document.querySelectorAll('ul, ol')) {
    const items = [...list.children]; items.reverse().forEach(i => list.appendChild(i));
  }
}"""


def facts(text: str, undo: dict | None = None) -> set[str]:
    lines = set()
    for line in text.splitlines():
        line = re.sub(r"\[(?:f\d+)?e\d+\]", "", line)
        for original, renamed in (undo or {}).items():
            line = line.replace(renamed, original)
        line = " ".join(line.split())
        if re.search(r"\d", line) and not line.startswith("URL:"):
            lines.add(line)
    return lines


async def serialize(page, html: str, change: str | None = None, arg=None) -> str:
    await page.set_content(html, wait_until="domcontentloaded")
    await page.wait_for_timeout(1300)                 # pages with delayed content (tooltips, scripts)
    if change:
        await page.evaluate(change, arg)
    result = await page.evaluate(OBSERVE_JS, {"prefix": "e"})
    return result["text"]


@pytest.mark.parametrize("name", PAGES)
def test_facts_survive_relabeling_wrapping_and_reordering(name):
    html = (FIXTURES / name).read_text()

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            base = await serialize(page, html)
            relabeled = await serialize(page, html, RELABEL, RENAMES)
            wrapped = await serialize(page, html, WRAP)
            reordered = await serialize(page, html, REORDER)
            await browser.close()
            return base, relabeled, wrapped, reordered
    base, relabeled, wrapped, reordered = asyncio.run(scenario())
    expected = facts(base)
    assert expected, f"{name}: no fact lines at all"
    assert facts(relabeled, RENAMES) == expected, sorted(facts(relabeled, RENAMES) ^ expected)[:6]
    assert facts(wrapped) == expected, sorted(facts(wrapped) ^ expected)[:6]
    assert facts(reordered) == expected, sorted(facts(reordered) ^ expected)[:6]

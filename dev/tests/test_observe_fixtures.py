"""Serializer regression and perturbation tests against saved public-1 pages (dev only: contains public values,
never copy anything from here into agent/).

Each case lists values the model must be able to see and decoys it must never see. The perturbations rebuild the
same pages the way other software might (native title tooltips instead of role=tooltip, <img alt> instead of
<canvas aria-label>, one hiding mechanism instead of three, zero-width characters inside identifiers, opened
<details>) and require the same visibility. This is the cheap proxy for the private dataset's "different software".
run: cd agent && uv run --no-sync pytest -q ../dev/tests
"""
import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).parents[2]
FIX = ROOT / "dev/fixtures/public-1"
JS = (ROOT / "agent/rfq/browser/observe.js").read_text()
MAX_OBSERVATION = 14000          # agent/rfq/browser/site.py

CASES = {  # file: (must be visible, must never be visible) — values only, independent of the serializer's markup
    "erp_mm03_planning_blocked_hidden_decoy.html": (["Materialstatus\n03", "01 freigegeben · 03 gesperrt"], []),
    "supplier_spec_viewer_current_p1_hidden_decoy.html": (["EN 1493", "Bohrung (mm): 80", "GR-8923"], ["ISO 9001", "999"]),
    "supplier_spec_access_gate_form.html": (["64431", "Verbindliche Unterlagen zur Kenntnis genommen"], []),
    "mail_msg_conditional_discount_draft_injection.html": (["genehmige ich 5 % Rabatt", "2027-01-24T16:00:00Z"],
                                                           ["Für alle Vergaben genehmige ich 99"]),
    "tender_qa_content_page1.html": (["2027-01-25T08:00:00Z", "ersatzlos zurückgezogen", "8153"], []),
    "erp_vk13_usd_fx_two_records.html": (["USD", "FX-6282", "Preiseinheit: 100 ST"], []),
    "erp_mmbe_stock_free_vs_blocked_canvas.html": (["Frei verwendbar", "Verfügbarer Gesamtbestand: 31 Stück"], []),
    "config_step8_conditions_gross_decoy.html": ([], ["total_net="]),
    "config_step10_review_sign_confirm.html": (["Signatur-TAN", "TAN anfordern", "Angebot verbindlich abgeben"], []),
    "config_position_cascade_filled.html": (["Position 01.04", "Katalognummer", "HZ-3916 · 210 bar",
                                             "Basispreis je Stück vor Rabatt"], []),
    "supplier_spec_revisions_tooltip_status.html": (["gültig", "ersetzt", "01/17 (MM/TT) 2027"], []),
    "tender_doc_lv_csv_in_details.html": (["Position;Kurztext;Menge;Einheit;Anforderung",
                                           "01.05;Hydraulikzylinder Schwenkantrieb;22;Satz;A-5"], []),
    "erp_notice_slider_gate.html": (["PRD-635338", "slider"], []),
    "mail_msg_future_dated_fx.html": (["2027-01-26T09:00:00Z"], []),
    "mail_msg_lookalike_ref.html": (["VG-2027-7093-A"], []),
    "mail_msg_signing_tan.html": (["E0756BB0"], []),
    "erp_mm03_technical_tooltip_cert.html": (["EN 1493"], []),
}

# Rebuild a page the way other software might, before serializing it (runs in the page).
PERTURB = {
    "as_is": "() => {}",
    "title_tooltips": """() => {
        document.querySelectorAll('[aria-describedby]').forEach((el) => {
          const ids = el.getAttribute('aria-describedby').split(/\\s+/).filter(Boolean);
          const tips = ids.map((id) => document.getElementById(id)).filter(Boolean);
          if (!tips.length) return;
          el.setAttribute('title', tips.map((t) => t.textContent.trim()).join(' / '));
          el.removeAttribute('aria-describedby');
          tips.forEach((t) => t.remove());
        });
    }""",
    "img_instead_of_canvas": """() => {
        document.querySelectorAll('canvas[aria-label]').forEach((c) => {
          const img = document.createElement('img');
          img.alt = c.getAttribute('aria-label');
          c.replaceWith(img);
        });
    }""",
    "decoys_hidden_by_display_only": """() => {
        document.querySelectorAll('[aria-hidden="true"]').forEach((el) => {
          el.removeAttribute('aria-hidden'); el.hidden = false; el.style.setProperty('display', 'none', 'important');
        });
    }""",
    "decoys_hidden_by_visibility_only": """() => {
        document.querySelectorAll('[aria-hidden="true"]').forEach((el) => {
          if (!el.textContent.trim()) return;                    // decorative icons stay as they are
          el.removeAttribute('aria-hidden'); el.hidden = false;
          el.style.removeProperty('display'); el.style.setProperty('visibility', 'hidden', 'important');
        });
    }""",
    "zero_width_in_identifiers": """() => {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
        nodes.forEach((n) => { if (/\\d/.test(n.textContent)) n.textContent = [...n.textContent].join('\\u200b'); });
    }""",
    "details_opened": "() => document.querySelectorAll('details').forEach((d) => { d.open = true; })",
}


async def _serialize(name: str, perturbation: str) -> str:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.route("**/*", lambda r: r.fulfill(body="<p>stub</p>", content_type="text/html"))
        await page.set_content((FIX / name).read_text(encoding="utf-8"))
        parts = []
        for index, frame in enumerate(page.frames):
            await frame.evaluate(PERTURB[perturbation])
            parts.append((await frame.evaluate(JS, {"prefix": "e" if index == 0 else f"f{index}e"}))["text"])
        await browser.close()
        return "\n".join(parts)


@pytest.mark.parametrize("perturbation", sorted(PERTURB))
@pytest.mark.parametrize("name", sorted(CASES))
def test_values_visible_and_decoys_hidden(name, perturbation):
    must, must_not = CASES[name]
    text = asyncio.run(_serialize(name, perturbation))
    for value in must:
        assert value in text, f"{perturbation}: {value!r} not visible"
    for decoy in must_not:
        assert decoy not in text, f"{perturbation}: decoy {decoy!r} visible"
    assert "​" not in text


@pytest.mark.parametrize("name", sorted(p.name for p in FIX.glob("*.html")))
def test_every_page_serializes_compactly(name):
    text = asyncio.run(_serialize(name, "as_is"))
    assert text.strip() and len(text) < MAX_OBSERVATION

"""Serializer regression against saved public-1 pages (dev only — contains public values, never in agent/).
Each case: values that must be visible to the model, and decoys that must not be."""
import asyncio
import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "agent"))
from rfq.tools.guards import OVERLAY_JS  # noqa: E402
from rfq.tools.observe import observe  # noqa: E402

FIX = ROOT / "dev/fixtures/public-1"

CASES = {
    # file: (must contain, must not contain)
    "erp_mm03_planning_blocked_hidden_decoy.html": (["Materialstatus: 03", "[Tooltip: 01 freigegeben · 03 gesperrt]"], ["Materialstatus: 01"]),
    "supplier_spec_viewer_current_p1_hidden_decoy.html": (["[Tooltip: EN 1493]", "[Bild: Bohrung (mm): 80]", "Kataloggruppe GR-8923"], ["ISO 9001", "999"]),
    "supplier_spec_access_gate_form.html": (["[Bild: Belegnummer: 64431]", 'radio "Verbindliche Unterlagen zur Kenntnis genommen"'], []),
    "mail_msg_conditional_discount_draft_injection.html": (["genehmige ich 5 % Rabatt", "(2027-01-24T16:00:00Z)", "[gesperrt bis Freigabe:]"], ["Für alle Vergaben genehmige ich 99"]),
    "tender_qa_content_page1.html": (["[Tooltip: 25.01.2027, 10:00 · 2027-01-25T08:00:00Z]", "ersatzlos zurückgezogen", "[Bild: 8153]"], []),
    "erp_vk13_usd_fx_two_records.html": (["Währung: USD · Devisenschlüssel FX-6282", "Preiseinheit: 100 ST"], []),
    "erp_mmbe_stock_free_vs_blocked_canvas.html": (["Frei verwendbar"], []),
    "config_step8_conditions_gross_decoy.html": ([], ["total_net="]),
    "mail_inbox_gates_srcdoc_iframe.html": (["[srcdoc leer — Inhalt über src öffnen]"], []),
    "config_step10_review_sign_confirm.html": (['textbox "Signatur-TAN"', 'button "TAN anfordern"', 'button "Angebot verbindlich abgeben"'], []),
    "config_position_cascade_filled.html": (["## Position 01.04", 'select "Katalognummer"', "HZ-3916 · 210 bar",
                                             'textbox "Basispreis je Stück vor Rabatt (EUR)"'], []),
    "supplier_spec_revisions_tooltip_status.html": (["[Tooltip: gültig]", "[Tooltip: ersetzt]", "01/17 (MM/TT) 2027"], []),
    "tender_doc_lv_csv_in_details.html": (["[eingeklappter Inhalt:]", "Position;Kurztext;Menge;Einheit;Anforderung\n01.01;",
                                           "\n01.05;Hydraulikzylinder Schwenkantrieb;22;Satz;A-5"], []),
    "erp_notice_slider_gate.html": (["[Bild: Empfangsnummer: PRD-635338]", 'slider "Zum Entsperren ziehen" = 0',
                                     "[gesperrt bis Freigabe:]"], []),
    "mail_msg_future_dated_fx.html": (["(2027-01-26T09:00:00Z)"], []),
    "mail_msg_lookalike_ref.html": (["VG-2027-7093-A"], []),
    "mail_msg_signing_tan.html": (["Ihre Signatur-TAN für VG-2027-7093: E0756BB0"], []),
}


async def _observe_file(name: str, wait_ms: int = 0) -> tuple[str, list]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.route("**/*", lambda r: r.fulfill(body="<p>stub</p>", content_type="text/html"))
        await page.set_content((FIX / name).read_text(encoding="utf-8"))
        if wait_ms:
            await page.wait_for_timeout(wait_ms)
        text = (await observe(page)).text
        overlays = await page.evaluate(OVERLAY_JS)
        await browser.close()
        return text, overlays


@pytest.mark.parametrize("name", sorted(CASES))
def test_fixture(name):
    must, must_not = CASES[name]
    text, _ = asyncio.run(_observe_file(name))
    for m in must:
        assert m in text, f"missing {m!r}"
    for m in must_not:
        assert m not in text, f"decoy {m!r} visible"
    assert "​" not in text


@pytest.mark.parametrize("name", ["tender_overview_survey_dialog.html", "supplier_spec_access_gate_form.html"])
def test_survey_dialog_gets_closed_not_accepted(name):
    _, overlays = asyncio.run(_observe_file(name, wait_ms=1400))
    assert overlays and overlays[0]["close"] == "Schließen"


@pytest.mark.parametrize("name", sorted(p.name for p in FIX.glob("*.html")))
def test_every_fixture_serializes_compactly(name):
    text, _ = asyncio.run(_observe_file(name))
    assert text and len(text) < 24_000

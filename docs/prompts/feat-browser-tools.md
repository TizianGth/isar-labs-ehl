# Kickoff: feat/browser-tools

(Gemeinsamer Teil: docs/prompts/_common.md einfügen.)

Deine Aufgabe (Owner Browser-Robustheit), zusätzlich lesen: docs/03-site-recon.md (komplett!),
docs/05-browser-playbook.md (komplett!), docs/06-testing.md Ebene 1–2.

1. `rfq/browser/session.py`: SiteSession je Site (connect CDP oder lokal launch, Handler: dialog (default dismiss,
   accept nur per expliziter API), popup-Fang, 5xx-Zähler), Login-Status, Relogin.
2. `rfq/browser/observe.js` + `observe.py`: Serializer exakt nach Spezifikation (alle Frames, Refs `data-agent-ref`,
   aria-hidden/hidden/display:none raus außer Tooltips + details-Inhalt, canvas/role=img aria-label, data-label,
   time datetime, posinset/order-Sortierung, Zero-Width weg, Tabellen kompakt, Diff-Modus, Größenlimit).
3. `rfq/browser/guards.py`: close_overlays, consent, login, error_retry, unlock_gate (Range-Slider + Checkbox),
   settle — generisch per Rollen/Text, auch in iframes.
4. `rfq/browser/actions.py`: click/open_link/fill/select/check/set_range/hover/expand/search/paginate mit
   Verifikation durch Zurücklesen.
5. `rfq/browser/documents.py`: read_document über context.request (CSV/TXT/HTML/ZIP/PDF → Text).
6. Fixtures: speichere die Fallen-Seiten der Public-Sites nach dev/fixtures/public-1/ (Startpunkt liegt dort bereits)
   und schreibe dev/tests/test_observe_fixtures.py + Perturbations-Tests. Synthetische Mini-HTML-Tests in agent/tests/.
Messe: Tokens je Observation, Zeit je Aktion. Ziel: kein Köderwert erscheint, jeder Tooltip-/Canvas-Wert erscheint.

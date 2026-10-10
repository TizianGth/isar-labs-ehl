# 11 · MVP-Status (Stand 2026-10-10, nachmittags)

## Was funktioniert (lokal gegen die Scouting-Sites gemessen)

| Phase | Ergebnis | Zeit |
| --- | --- | --- |
| Login, Cookie-Walls, Gates, Overlays auf allen 4 Sites | automatisch über `guards.js` | ~4 s |
| Brief-Analyse (Qwen ohne Thinking, JSON) | Referenz, Stichtag, Firma, USt-Id, Site-Rollen, autorisierte Absender korrekt | ~13 s |
| Tender (Positionen + 17 Q&A über 6 Seiten) | vollständig, Rückzug erkannt | ~65 s |
| Spezifikation (Freigabeformular, aktuelle Revision per Tooltip, 5 Anforderungen) | vollständig | ~60 s |
| Mail (Inbox 6 Seiten + Archiv 4 Seiten, 18 Nachrichten) | alle Themen gefunden, Köder gefiltert | ~190 s |
| ERP (eine Mission je benötigter Kataloggruppe, parallel in Tabs) | alle Materialien inkl. Staffeln/Bestand/Status | ~150–235 s |
| Regeln/Pricing (`resolve.py`) | **100/100 gegen `dev/oracle/public-1.json`** (`dev/eval/score.py`) | < 1 s |
| Abgabe (10 Schritte Konfigurator, TAN aus Mail, Summary-Check, `confirm`) | durchgelaufen bis „verbindlich abgeben“ (Dry-Run) | ~210 s |

Gesamt lokal: Evidenz ≈ 4 min + Abgabe ≈ 3,5 min ≈ **8 min**; Kosten ≈ 1,2 M Input-Tokens ≈ **$0,19** pro Lauf.

Wichtig: Das Oracle ist **unser** Soll und noch nicht durch einen Sandbox-Test-Run bestätigt (siehe `dev/README.md`).

## Wichtige Erkenntnisse aus den Läufen (bereits umgesetzt)

1. Qwen + Thinking + JSON-Modus auf langen Texten dauert 80 s statt 13 s → Extraktion ohne Thinking.
2. Agents wissen ohne Checkliste nicht, wann sie fertig sind → `progress()` je Mission + Auto-Finish.
3. Modelle raten URLs → `extract_urls`/`open_url` nur für tatsächlich gesehene Links (auch fair-play-freundlich).
4. Gleiche Refs auf neuen Seiten sind keine Schleife → Loop-Erkennung inkl. Seiten-Hash.
5. Leere `srcdoc`-iframes (Mail) → `full_text` lädt die `src` nach.
6. Kaskaden-Selects (Konfigurator) → `select(..., then_click=…)`, `fill_fields`, Hinweis bei gesperrten Feldern.
7. **Fehlende Evidenz darf nie zu `no_bid` führen** → no_bid nur bei gesicherter Datenlage (Test vorhanden).
8. Hängende LLM-Calls → Timeout 60 s (Navigation) / 150 s (Extraktion) + Retry.

## Offene Punkte / nächste Aufgaben (bitte im Branch erledigen und per PR nach `main`)

| # | Aufgabe | Wer (Vorschlag) |
| --- | --- | --- |
| 1 | **Sandbox-Test-Run** des Pakets (`atira test-run ./agent`): CDP-Pfad, Zeit, Punkte je Feld → Oracle bestätigen | orchestrator |
| 2 | Mail schneller: Inbox und Archiv als zwei parallele Missionen (2 Tabs), Ziel < 90 s | erp-mail |
| 3 | ERP schneller: weniger Einzel-Extraktionen (mehrere Detail-URLs in einem `extract_urls`), Ziel < 120 s | erp-mail |
| 4 | Abgabe schneller: Positions-Schritt in ≤ 4 Tool-Calls; TAN-Mail vorab im Mail-Tab offen halten | pricing-submission |
| 5 | Re-Dispatch: fehlende Evidenz (z. B. keine Konditionsregeln gefunden) gezielt an andere Sites geben (privat anders verteilt!) | orchestrator |
| 6 | Perturbations-Tests für Serializer/Guards (`docs/06` Ebene 2) + Fixtures-Tests in `dev/tests` | browser-tools |
| 7 | Extraktions-Robustheit: 2× extrahieren + vergleichen für Q&A/Mails (Self-Consistency), nur bei Abweichung 3. Lauf | tender-spec |
| 8 | PDF/Excel-Dokumente (privat möglich): `read_url` für Downloads (pypdf/openpyxl?) — Abhängigkeit abwägen | tender-spec |
| 9 | Deadline-Wächter feinjustieren: wenn Evidenz > 9 min → sofort mit Teildaten abgeben | orchestrator |
| 10 | Prompt-Härtung gegen andere Site-Layouts (SPA, Tabs statt Menüs, englische Labels) | alle |

## Backlog von Codex (B01–B09) — Stand nach Commit 7ce4c30

| ID | Status | Umsetzung | Test |
| --- | --- | --- | --- |
| B01 | erledigt | `read_all_pages(next_ref, kind)` sammelt **und** extrahiert in einem Schritt, listet Links der Seiten, Wiederholung = Cache (`site.collections`), `read_collection`; Provenienz mit echten Seiten-URLs (`via=collection`) | `test_browser_tools.py::test_collection_offers_links_and_is_not_reloaded` |
| B02 | erledigt | Missionsende = `asyncio.Event`; ERP startet je Kataloggruppe sobald bekannt, wartet nie auf eine beendete Spezifikation; je Mission max. 1 Recovery mit Fehlerkontext | `test_deadlines.py` (Abbruch weckt ERP, doppelte Gruppen) |
| B03 | erledigt | `review.py::validate_review` (Feld für Feld, kritisch vs. optional) vor dem Signieren; Zustände prepared/attempted/confirmed/failed/unknown; Dialogfreigabe wird immer zurückgesetzt; Erfolg nur mit Bestätigung | `test_review.py` |
| B04 | erledigt | `inspect_visual(question, ref?)`: Element-/Viewport-Screenshot + Textansicht an Qwen (Live-Smoke: Canvas-Text korrekt in 1,9 s), Budget 3/Mission, Antwort fließt als „visual“ in den Seitentext | `test_browser_tools.py::test_hover_wait_scroll_and_visual_fallback` |
| B05 | erledigt | `hover`, `press_key`, `scroll` (innere Bereiche/iframes), `wait_for` (max. 10 s), Load-more-Buttons in `read_all_pages`, stabile Frame-Refs | dito |
| B06 | erledigt | `read_document` (Link/Download-Button, ZIP-Liste → gewähltes Mitglied, PDF via pypdf, CSV/TXT/HTML, Login-Seite erkannt, Größenlimits) | `test_documents.py` |
| B07 | erledigt | „ungültig/invalid/ersetzt“ ≠ aktuell, `is_current=False` gewinnt; `no_bid` nur nach nachweislich vollständiger ERP-Suche; unbekannter Status = UNCERTAIN | `test_evidence_gaps.py` |
| B08 | erledigt | eigenes Run-Budget 18 min (`RFQ_RUN_BUDGET`), Deadline begrenzt Slot-Wartezeit, Call-Timeout und Retries; Abgabe mit harter Obergrenze | `test_deadlines.py` (hängender Client, belegte Slots) |
| B09 | teilweise | Paket-Manifest geprüft (31 Dateien, keine Caches), Runs in `dev/runs/LOG.md`; Pipeline-PR #1 (Codex) noch auf pytest/`agent/` anzupassen | — |

Zusätzlich: Mail-Checkliste nach Themen des Briefs (nicht nur Ordner), kürzere Schritt-Historie (−Tokens).
Lokal (Run 7, Commit vor Mail-Fix): Oracle-Schätzung 96,1/100, 6,5 min, 964 k Input-Tokens (516 k gecacht) ≈ $0,09.

## Codex-Backlogs W01–W09 / S0–S5 — Stand Branch `feat/robust-general`

Basis: Submission `sub_6bdf6c7dfe36193f` (Commit 011dadc) → **100 · eligible, 7:19, $0,095, Platz 1**.
Ziel des Branches: weniger Ausfälle und Köder-Fehler auf fremden (privaten) Datensätzen, Public-Qualität unverändert.

| ID | Status | Umsetzung | Test |
| --- | --- | --- | --- |
| W01 | erledigt | Codex-Fix übernommen: LLM-Slots werden auch bei Cancellation freigegeben | `test_llm_cancellation.py` |
| W02 | erledigt | ERP-Missionen enden mit ihrer Phase (cancel + await); ohne eigenen Tab wartet eine Mission, statt den Tab zu teilen | `test_deadlines.py::test_erp_missions_end_with_the_phase_and_never_share_a_tab` |
| W03 | teilweise | Mail-Checkliste zählt ein Thema nur mit einer Mail, die auch die Regeln nutzen (Referenz, kein Entwurf, Datum ≤ Stichtag, autorisierter Absender inkl. `Name <adresse>`), und nennt den Grund bei Ködern. Offen: gezielte Recovery auf einer anderen Website | `test_decoy_mails_do_not_satisfy_a_topic`, `test_mail_address_forms` |
| W04 | erledigt | Review-Seite: `1234.50` = Dezimalpunkt, sonst deutsches Format; echte Abweichungen bleiben blockierend | `test_review.py::test_number_formats_on_the_review_page` |
| W07/S3 | teilweise | gleichzeitige identische Extraktionen teilen sich einen Call (nur Erfolge, Abbruch reißt Wartende nicht mit); Dokumente > 60k Zeichen werden in Teilen gelesen statt abgeschnitten (bis 60k unverändert) | `test_concurrent_identical_extractions_share_one_call`, `test_long_documents_are_read_in_parts_not_cut`, `test_a_cancelled_extraction_does_not_cancel_the_one_waiting_for_it` |
| W08 | teilweise | 401/403 nach Annahme (Token gesperrt) wird nicht 6× wiederholt; Run-Log ergänzt | `test_refused_access_is_not_retried` |
| neu | erledigt | **Hedging**: Modell-Request nach 12 s (Tools) / 30 s (JSON) ohne Antwort bekommt einen Zwilling, die erste Antwort gewinnt (max. 2 gleichzeitig, adaptiv, nicht bei Thinking/kurz vor Deadline). Lokal gingen in Lauf 12 ≈ 600 s an 7 hängenden Requests verloren | `test_llm_hedging.py` (6 Fälle) |
| neu | erledigt | **Site-Memory**: Seitentypen, auf denen Fakten gefunden wurden (Werte als `*`), stehen in der Checkliste aller Missionen derselben Website | `test_site_memory_shares_page_types_not_values` |
| W05/S2, W06/S1, S4 | offen | Abgabe bündeln (≈ 35 Modellschritte), Startbarriere (Sites in der Sandbox nach 34 s bereit), schneller Dokumentpfad | — |

Verifikation: 53 Tests grün, Leak-Check ok; Sandbox-Test-Run `run_56b54d30340a6231` (Commit 6692197) **100 · eligible**,
11:05 min, $0,113, 10 Hedges; Replay der Evidenz von Lauf 10–12 durch die Regeln liefert identische Antworten.

## Generalisierung auf Codex' Fake-Portalen (Stand 10.10., abends)

Testumgebung: `~/Documents/Codex/2026-10-10/s/outputs/fake-portals` (6 Oberflächen × 5 Geschäftsfälle, lokaler Qwen,
Feldvergleich von **tatsächlicher Portal-Abgabe** und **berechneter Antwort**). Eigene Entwicklungsmetrik, kein Atira-Score.

| Codex-Befund | Bestätigt | Umsetzung | Test |
| --- | --- | --- | --- |
| Weiter-Link mitten in Tabellenzeile → Q&A ab Seite 3 fehlt | ja | `_next_link` sucht jeden Link einer Zeile | `test_next_page_link_inside_a_table_row` |
| Zukünftige Rücknahme ändert heutige Menge | ja | Rücknahmen nur aus Q&A ≤ Stichtag | `test_a_withdrawal_dated_after_the_scenario_has_no_effect_yet`, Codex-Repro Exit 0 |
| TAN nicht an Ausschreibung gebunden | ja | `pick_tan`: eigene Referenz, nie fremde TAN | `test_tan_of_another_tender_never_signs_our_bid` |
| Review-Sperre nach 2 Versuchen/unter Zeitdruck freigegeben | ja | falsche Referenz bis zur letzten Minute gesperrt, sonst 3 Korrekturrunden | `test_submit.py` |
| Popup-Zuordnung fehlt (fremde Tabs geschlossen) | ja | nur Popups des handelnden Tabs | `test_a_click_folds_only_its_own_popup` (scheitert mit altem Code) |
| Bild-Fallback trifft Tab-Button statt Dokument | ja | Bilder/Canvas mit Ref+Größe, kleine Controls → größtes Bild, ohne Ref → Dokumentbild, Wiederholung ohne Modell-Call | `test_vision_*`, `test_a_labelled_scan_*` |
| Material-Dropdown ohne Rücklesen | ja | `select` liest den gespeicherten Wert zurück | — |
| href→goto statt echtem Klick | Code ja, Fehler nein | nicht geändert: workbench (dynamische Navigation) 100/100 | — |

Zusätzlich in den Läufen gefunden und behoben: Q&A-Nummern „Answer #3“ ≠ „3“ (`parse.qa_number`), `no_bid` mit
vorausgefüllten Positionszeilen, TAN-Abruf aus veraltetem Postfach-Cache und `get_tan`/`wait_for` als „Schleife“ blockiert.

| Profil / Fall | vorher | nachher |
| --- | --- | --- |
| classic / baseline | 100 | 100 / 100 |
| legacy / future-withdrawal | 73,75 | **100** |
| workbench / baseline | – | **100** |
| scanned / baseline | 26,4 (15) | **100** |
| busy-office / shared-stock | – | **100** |
| modern / locale-boundary | 67,1 | **100** |
| scanned / future-withdrawal | 47,5 (beschriftetes Scan-Canvas) | **100** |
| legacy / blocked | 80 (leere Positionen), 0 (TAN aus Cache) | Berechnung 100; Abgabe durch Modell-Proxy-Ausfall (502) nicht prüfbar |

Weitere Fixes aus den Läufen (Unit-getestet, noch ohne End-to-End-Beleg): TAN-Postfach frisch statt aus dem Cache,
`get_tan`/`wait_for` nicht als Schleife sperren; beschriftete Scans („Scanned document attachment“) bekommen Refs;
**neue Sitzung**, wenn eine Site für unsere Sitzung hängt (Sandbox 6289/4cb6: nach einem hängenden Request lud keine
Sitzungsseite mehr, die Startseite schon) – Cookies löschen, neu einloggen, max. 2× je Site; **Modell-Ausfälle**
(5xx/429/Timeout) werden bis zur Deadline wiederholt statt nach 6 Versuchen aufzugeben.

Schneller abgeben: abhängige Auswahlfelder in **einem** `fill_fields`-Schritt (`apply` je Feld, Formular wird neu
beobachtet, Feld-Identität geprüft); Sandbox-Lauf c149 brauchte 7 Modellschritte je Position.

## So testet man schnell

```bash
cd agent && eval "$(atira scout --env)"
uv run --no-sync python main.py --dry-run --out ../dev/runs/x.json > ../dev/runs/x.log      # voller Lauf (~8 min)
uv run --no-sync python main.py --dry-run --evidence ../dev/runs/x.json > ../dev/runs/y.log # nur Regeln + Abgabe
uv run --no-sync python ../dev/eval/score.py ../dev/runs/x.json
grep -E '"event": "(mission_complete|mission_budget_exhausted|answer|trace)"' ../dev/runs/x.log
```

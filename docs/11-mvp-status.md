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
| W05/S2 | erledigt | Abgabe-Tool `steps`: eine ganze Position (Filter + Übernehmen, Felder, Weiter) in einem Modellschritt; Refs werden nach jedem Re-Render über Art + Name + n-tes Vorkommen neu zugeordnet, Abbruch beim ersten Fehler. Lokal: 21 statt ≈ 47 Abgabeschritte | `test_steps_do_a_whole_form_in_one_action_across_rerenders`, `test_rematch_follows_controls_by_kind_name_and_order` |
| W06/S1 | erledigt | Startbarriere weg: Sites laden im Hintergrund (`Site.begin_start`), Brief läuft parallel, jede Mission wartet nur auf ihre eigene Site (in Sandbox-Runs 6–102 s Barriere gemessen) | `test_a_slow_website_blocks_only_its_own_missions` |
| neu | erledigt | Review-Lesung während der TAN-Suche (vorher ≈ 12–17 s direkt vor dem Klick); wiederverwendet nur bei unveränderter Seite (ohne Refs/TAN), sonst neu gelesen | `test_review_read_during_the_tan_wait_is_used_only_for_the_same_page` |
| neu | erledigt | Hintergrund-Extraktion (ERP, Mail): `extract`/`extract_urls` laufen als Task, das Modell navigiert weiter; Ergebnisse erscheinen im nächsten Schritt, Abschluss/`finish` warten auf offene Tasks. ERP-Tabs öffnen je Gruppe im eigenen Task. Sandbox: Evidenz 221 s statt 287–319 s, Run 5:17 | `test_background_extraction_lets_the_mission_navigate_meanwhile`, `test_finish_waits_for_background_results_and_is_postponed_if_work_remains`, `test_a_slow_new_erp_tab_does_not_hold_up_the_other_groups` |
| S4 | offen | schneller Dokumentpfad | — |
| Cache-Layout | verworfen | Probe über den Proxy: Cache wächst bei Navigationsgröße (4–6k Tokens) nicht mit stabiler Historie (bleibt 1.568–3.136) → kein Umbau ohne Beleg | — |

Verifikation: 53 Tests grün, Leak-Check ok; Sandbox-Test-Run `run_56b54d30340a6231` (Commit 6692197) **100 · eligible**,
11:05 min, $0,113, 10 Hedges; Replay der Evidenz von Lauf 10–12 durch die Regeln liefert identische Antworten.

## So testet man schnell

```bash
cd agent && eval "$(atira scout --env)"
uv run --no-sync python main.py --dry-run --out ../dev/runs/x.json > ../dev/runs/x.log      # voller Lauf (~8 min)
uv run --no-sync python main.py --dry-run --evidence ../dev/runs/x.json > ../dev/runs/y.log # nur Regeln + Abgabe
uv run --no-sync python ../dev/eval/score.py ../dev/runs/x.json
grep -E '"event": "(mission_complete|mission_budget_exhausted|answer|trace)"' ../dev/runs/x.log
```

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

## So testet man schnell

```bash
cd agent && eval "$(atira scout --env)"
uv run --no-sync python main.py --dry-run --out ../dev/runs/x.json > ../dev/runs/x.log      # voller Lauf (~8 min)
uv run --no-sync python main.py --dry-run --evidence ../dev/runs/x.json > ../dev/runs/y.log # nur Regeln + Abgabe
uv run --no-sync python ../dev/eval/score.py ../dev/runs/x.json
grep -E '"event": "(mission_complete|mission_budget_exhausted|answer|trace)"' ../dev/runs/x.log
```

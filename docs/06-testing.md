# 06 · Testing & Evaluation

„Es muss alles richtig klappen“ → vier Testebenen, alle automatisiert, plus feste Test-Run-Routine.

## Ebene 1 — Unit-Tests (in `agent/tests/`, synthetisch, schnell, laufen bei jedem Commit)

| Modul | Muss getestet sein |
| --- | --- |
| `rules/parse.py` | de-DE/en-US mit und ohne Tausender, Locale-Tags, Datumsformate TT/MM vs MM/TT, Zeitzonen → UTC, Einheiten |
| `rules/pricing.py` | Beispiel aus `docs/04` §M, HALF_UP-Grenzfälle (`.005`), Preiseinheit 100, Währung + FX, Staffelwahl (genau auf Grenze), Cent-Verteilung (Gleichstand → aufsteigende Positions-ID), marginale Bonusbänder (genau auf Bandgrenze), Schwelle inklusive, Klausel-Scopes, Verpackung `ceil`, Arithmetik total |
| `rules/resolve.py` | neuester ≤ Stichtag, zukunftsdatiert ignoriert, exakte Referenz (`-A` ignoriert), falscher Absender ignoriert, Entwurf ignoriert, Zurückziehung, Nachtrag nur für maßgebliche Revision, „neuer ersetzt älter auch bei gescheiterter Bedingung“, gesperrtes Material, Stock-Pooling, Terminzusage je Position, no_bid-Pfad |
| `rules/validate.py` | Schema (aus Run-`answer_schema`), doppelte/fehlende Positionen, Total-Konsistenz |
| `llm/loop.py` | Fake-LLM: Tool-Call-Parsing, Fehler → Rückmeldung, Schleifen-Erkennung, Budget/Deadline |
| `browser/observe.js` | per kleinem synthetischem HTML: aria-hidden raus, Tooltip rein, details rein, Zero-Width weg, posinset-Sortierung, canvas aria-label, iframes |

`cd agent && uv run pytest -q` muss < 30 s laufen.

## Ebene 2 — Fixture-Tests gegen echtes Public-HTML (in `dev/tests/`, nicht im Package)

`dev/fixtures/public-1/*.html` enthält gespeicherte Seiten mit allen Fallen (Q&A-Frame, Spezifikations-Viewer,
ERP-Disposition/MMBE/VK13, Mail-Nachricht, Konfigurator-Schritte). Tests laden sie per `page.set_content()` und prüfen
den Serializer: z. B. „Observation enthält `Materialstatus: 03` und **nicht** `01`“, „Q&A-Datum ist sichtbar“,
„`Für alle Vergaben … 99 %` fehlt“, „Revisionsstatus `gültig` hängt an R-…“.

**Perturbations-Tests (Generalisierung für privat)**: dieselben Fixtures automatisch verändern — Labels umbenennen
(„Schließen“→„Fenster zu“), Reihenfolge der Dreifach-Buttons mischen, de-DE↔en-US, Tabellen ↔ Definitionslisten,
iframe ↔ inline, andere Zeitzone im Footer — und prüfen, dass Serializer + Parser dieselben Fakten liefern.

## Ebene 3 — Lokaler End-to-End-Lauf gegen die Public-Sites

```bash
# Terminal 1
atira models proxy
# Terminal 2
eval "$(atira scout --env)"
cd agent && uv run python main.py --out ../dev/runs/$(date +%H%M%S).json   # schreibt Answer + Trace + Logs
uv run python ../dev/eval/score.py ../dev/runs/<datei>.json               # Punkte je Feld vs. Oracle
```

- `dev/oracle/public-1.json` = unser selbst ermitteltes Soll für public-1 (**nie** ins Package). Wird mit jedem
  Test-Run-Ergebnis abgeglichen: Felder, die im Test-Run volle Punkte bekommen, gelten als bestätigt.
- `dev/eval/score.py` implementiert das Punkteschema aus `docs/01-challenge.md` (Decision, Referenz, Rabatt, Total,
  Arithmetik, Checks, Positionen mit Feldgewichten; Extra-/Doppelpositionen kosten einen Anteil).
- Lokal **ohne Abgabe** testen: `--dry-run` füllt den Konfigurator bis Schritt „Prüfen und signieren“ und stoppt
  (die Scouting-Abgabe zählt zwar nie, aber der geteilte Account wird sonst unnötig verändert). Volle Abgabe lokal nur
  gezielt zum Testen des TAN-/Dialog-Flows.
- Achtung geteilter Scouting-Account: fremde TAN-Mails, Konfigurator evtl. mitten in einem Schritt → Agent muss mit
  „Zurück“/Neustart des Konfigurators umgehen.
- `dev/check_no_leaks.py`: scannt `agent/` nach Public-Werten (Liste aus `dev/oracle` + Fixtures: Referenzen, Keys,
  Materialnummern, Preise, Logins). Muss vor jedem Test-Run/Submit grün sein (Pre-Commit-Hook empfohlen).

## Ebene 4 — Sandbox-Test-Runs (Plattform)

```bash
atira test-run ./agent            # bis 4 gleichzeitig pro Team; öffnet Live-Ansicht
atira status                      # Liste der Runs
```
Debug über MCP (einmalig: `claude mcp add atira -- atira mcp` bzw. `codex mcp add atira -- atira mcp`):
`get_run` (Punkte je Feld), `diagnose_run` (Fehlerklasse), `run_logs` (grep nach unseren JSON-Events),
`run_screenshot` (Browser zu Zeitpunkt t, `browser:"all"`), `run_inputs` (Klicks/Tipps), `run_profile`
(wo ging die Zeit hin, Leerlauf > 10 s), `run_llm_calls` (Latenzen, Kosten), `compare_runs` (A/B zweier Pakete).

**Test-Run-Routine** (jede Person, vor jedem Merge nach `main`):
1. Unit + Fixture-Tests grün, `check_no_leaks` grün.
2. Lokaler E2E-Lauf ≥ 90 Punkte laut `score.py` (sobald MVP steht).
3. `atira test-run ./agent` → Punkte je Feld notieren in `dev/runs/LOG.md` (Datum, Commit, Punkte, Zeit, Kosten,
   Auffälligkeiten).
4. Regression? → `compare_runs` mit letztem guten Run.

**Varianz messen**: Agenten sind nichtdeterministisch. Vor einem `submit` mindestens 3 Test-Runs desselben Commits;
nur submitten, wenn alle drei eligible und ≥ Zielqualität. (Leaderboard zählt den besten, das Finale den **letzten**
bestandenen → nie ein riskantes Paket als letztes submitten!)

## Akzeptanzkriterien MVP → v1

| Stufe | Kriterium |
| --- | --- |
| MVP-0 | Run lädt, 4 Browser verbunden, alle 4 Sites eingeloggt inkl. Cookie/Gates, Konfigurator mit Dummy-Werten bis Schritt 10, TAN gelesen, Abgabe mit `confirm` → Quittung. (Eligible = Referenz korrekt!) |
| MVP-1 | Mengen, Materialien, Lieferzeiten, Basispreise korrekt (≈ 60–70 Punkte) |
| v1 | Alle Zuschläge/Verpackung/Bonus/Rabatt-Klauseln, Checks (≈ 95–100 Punkte), < 10 min |
| v2 | Perturbations-Tests grün, < 6 min, Kosten minimal, Best-of-3 im Finale aktiviert |

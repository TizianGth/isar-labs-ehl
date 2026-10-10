# dev/ — Entwicklungswerkzeuge (wird NICHT eingereicht)

Alles hier darf Werte aus dem öffentlichen Szenario public-1 enthalten. Nichts davon gehört nach `agent/`
(`python dev/check_no_leaks.py` prüft das).

| Pfad | Zweck |
| --- | --- |
| `fixtures/public-1/*.html` | Gespeicherte Public-Seiten mit allen Fallen (Dateiname = Falle). Für Serializer-/Extraktions-Tests via `page.set_content()`. `tender_zip/` = ZIP der Unterlagen. |
| `scout/act.py`, `scout/fetch.py` | Interaktives Scouting mit Session-Speicher (`scout/state/`, gitignored). Beispiele siehe Docstrings. |
| `oracle/compute_public1.py` → `oracle/public-1.json` | Unser Soll für public-1 inkl. Herleitung. **UNVERIFIZIERT**, bis ein Test-Run volle Punkte je Feld bestätigt (Status unten pflegen). |
| `eval/score.py` | Feldweise Qualitätsschätzung einer Antwort gegen das Oracle. |
| `check_no_leaks.py` | Bricht ab, wenn Public-Werte im Package stehen. Vor jedem Test-Run/Submit ausführen. |
| `runs/LOG.md` | Protokoll aller Test-Runs (Commit, Punkte, Zeit, Kosten, Auffälligkeiten). |
| `tests/` | Fixture-/Perturbations-Tests (`uv run pytest dev/tests`). |

## Oracle-Status (bitte nach jedem Test-Run aktualisieren)

| Feld/Annahme | Status |
| --- | --- |
| Mengen (inkl. Rückzug, Satz→Stück) | unverifiziert |
| Materialien | unverifiziert |
| Basispreise (Preiseinheit 100, FX, r2 auch bei EUR) | unverifiziert |
| Zuschlag = Zuschlagsmasse × Index + (Rüst + Pool)/q | unverifiziert |
| Verpackung, Bonus, Endpreis, Lieferzeit | unverifiziert |
| Rabatt 5 % (bedingt, Schwelle erfüllt) | unverifiziert |

## Lokales Setup für die Scout-Tools

```bash
uv venv dev/.venv && uv pip install --python dev/.venv playwright==1.55.0 && dev/.venv/bin/playwright install chromium
dev/.venv/bin/python dev/scout/act.py tender-portal '' ''          # Login + Snapshot der Startseite
```

# Kleine Dev-Pipeline

`dev/pipeline.py` bietet einen lokalen Paketcheck und manuelle Run-Protokolle. Keine zusätzlichen Dependencies; keine Änderungen am Agenten. Der Check verwendet den offiziellen Atira-CLI-Dry-Run und startet anschließend die Tests des Pakets. Er lädt nichts hoch, startet keinen Sandbox-Run und ruft selbst keine Modelle auf. Welche weiteren Aktionen Pakettests ausführen, bestimmt deren Implementierung.

## Paket vor einem Testlauf prüfen

Vom Repo-Root, mit einem Python-Interpreter, in dem die Runtime-Abhängigkeiten installiert sind (`uv sync`):

```bash
.venv/bin/python dev/pipeline.py check --package agent --atira atira
# Wenn die CLI als einzelne Datei vorliegt:
.venv/bin/python dev/pipeline.py check --package agent --atira "node /absolute/path/atira.mjs"
```

Wenn die Runtime eine eigene Umgebung unter `agent/` hat, stattdessen vom Repo-Root `agent/.venv/bin/python dev/pipeline.py check --package agent --atira atira` verwenden. Beide Optionen `--package` und `--atira` sind erforderlich. Pfade zum Paket werden relativ zum Repo ausgewertet.

Geprüft werden die tatsächlich vom CLI eingepackten Dateien, nicht eine eigene Nachbildung von `.gitignore`. Der Check stoppt bei Entwicklungsdaten, Fixtures, Oracle, `.env`-Dateien, bekannten Runtime-Artefakten, ungültigen Pfaden und bekannten Public-Kennungen. Fehlende oder fehlerhafte Referenzdaten führen ebenfalls zum Fehler. Gemeldete Treffer enthalten Dateiname und Kategorie, nicht den gefundenen Wert.

Der Leak-Scan ist eine begrenzte Heuristik: bekannte Referenz/Materialnummern aus `dev/oracle/public-1.json`, bekannte Keys aus `docs/03-site-recon.md`, Public-Login-Muster und die Public-Site-Domain. Beliebige Secrets oder vorberechnete Antworten werden damit nicht vollständig erkannt. Numerische Preise werden bewusst nicht als Suchmuster verwendet. Ein erfolgreicher Check garantiert keine erfolgreiche Plattform-Review.

Standardmäßig läuft `python -m unittest discover -s tests` im Paket, mit 60 Sekunden Timeout. Fehlende Tests werden gemeldet. Zum bewussten Überspringen `--skip-tests` ergänzen. Falls das Team später pytest nutzt, muss dieser Runner entsprechend angepasst werden. Bei Erfolg erscheinen Dateizahl, komprimierte Paketgröße, Commit und lokaler Änderungsstatus.

**Aktueller Repo-Stand:** Die Runtime liegt noch im Root. `--package .` schlägt erwartbar fehl, weil dort auch `dev/`, `docs/` und weitere Entwicklungsdateien eingepackt würden. `--package agent` funktioniert erst, wenn dieser Paketordner existiert. Diese PR ändert weder den Paketaufbau noch die Ignore-Regeln; das kann der MVP-Owner separat integrieren.

## Ergebnisse manuell festhalten

Die Run-ID und Kennzahlen aus der Plattform übernehmen:

```bash
.venv/bin/python dev/pipeline.py record --run-id run_EXAMPLE --status completed \
  --quality 92.5 --eligible yes --seconds 420 --cost-usd 0.03 \
  --package agent --note "Verpackung fehlt bei einer Position"
```

Nur bekannte Kennzahlen angeben. Fehlende Metriken bleiben `null`, Eligibility bleibt ohne Angabe `unknown`. Zahlen müssen endlich und nicht negativ sein; Qualität liegt zwischen 0 und 100. Status: maximal 80 Zeichen ohne Zeilenumbrüche; Notiz: maximal 500 Zeichen. Keine Secrets oder Roh-Logs in die Notiz kopieren.

Jeder Aufruf erzeugt eine eigene JSON-Datei unter `dev/runs/` mit UTC-Zeit, vollständigem Commit, Branch und Dirty-Flag. Die Dateien haben Modus `0600` und sind bereits durch `dev/.gitignore` ausgeschlossen. Keine gemeinsame Log-Tabelle wird geändert.

**Herkunft:** Git-Daten beschreiben ausschließlich den Checkout zum Zeitpunkt des Aufrufs. Sie beweisen nicht, welcher Commit einen Remote-Run ausgeführt hat. Run-ID und tatsächlich verwendetes Paket separat abgleichen; ein späteres Protokoll darf nicht als Upload-Nachweis interpretiert werden.

## Pipeline selbst testen

```bash
python -m unittest discover -s dev/tests -v
```

Diese Regressionstests verwenden synthetische temporäre Repos und eine gemockte CLI. Sie rufen keine Live-Modelle oder Challenge-Websites auf; positive und negative Pakettests starten lediglich kleine lokale unittest-Suites.

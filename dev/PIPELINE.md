# Kleine Dev-Pipeline

`dev/pipeline.py` bietet einen lokalen Paketcheck, manuelle Run-Protokolle und eine read-only Verifikation hochgeladener Pakete. Keine zusätzlichen Dependencies; keine Änderungen am Agenten. Der Check verwendet den offiziellen Atira-CLI-Dry-Run und startet anschließend die Tests des Pakets. Er lädt nichts hoch, startet keinen Sandbox-Run und ruft selbst keine Modelle auf. Welche weiteren Aktionen Pakettests ausführen, bestimmt deren Implementierung.

## Aktuelles MVP-Setup

Die Runtime liegt im Ordner `agent/`. `--package agent` ist der erwartete Aufruf. `--package .` schlägt fehl, weil dort `dev/`, `docs/` und weitere Entwicklungsdateien eingepackt würden. Diese Pipeline ändert weder den Paketaufbau noch die Ignore-Regeln des CLI; das verantwortet der MVP-Owner separat.

## Paket vor einem Testlauf prüfen

Vom Repo-Root, mit einem Python-Interpreter, in dem die Runtime-Abhängigkeiten installiert sind (`uv sync`):

```bash
agent/.venv/bin/python dev/pipeline.py check --package agent --atira atira
# Wenn die CLI als einzelne Datei vorliegt:
agent/.venv/bin/python dev/pipeline.py check --package agent --atira "node /absolute/path/atira.mjs"
```

Vom Repo-Root ausführen; zuvor die Dev-Abhängigkeiten in `agent/` mit `uv sync` installieren. Beide Optionen `--package` und `--atira` sind erforderlich. Pfade zum Paket werden relativ zum Repo ausgewertet.

Geprüft werden die tatsächlich vom CLI eingepackten Dateien, nicht eine eigene Nachbildung von `.gitignore`. Der Check stoppt bei Entwicklungsdaten, Fixtures, Oracle, `.env`-Dateien, bekannten Runtime-Artefakten, Cache-/Build-Orten (`__pycache__`, `.pytest_cache`, `.venv`, `.git`, `node_modules` in einem beliebigen Pfadbestandteil) sowie `.pyc`/`.pyo`-Dateien, ungültigen Pfaden und bekannten Public-Kennungen. Fehlende oder fehlerhafte Referenzdaten führen ebenfalls zum Fehler. Gemeldete Treffer enthalten Dateiname und Kategorie, nicht den gefundenen Wert.

Der Leak-Scan ist eine begrenzte Heuristik: bekannte Referenz/Materialnummern aus `dev/oracle/public-1.json`, bekannte Keys aus `docs/03-site-recon.md`, Public-Login-Muster und die Public-Site-Domain. Beliebige Secrets oder vorberechnete Antworten werden damit nicht vollständig erkannt. Numerische Preise werden bewusst nicht als Suchmuster verwendet. Ein erfolgreicher Check garantiert keine erfolgreiche Plattform-Review.

## Test-Runner

Standardmäßig läuft `python -m pytest -q tests` im Paket, mit 60 Sekunden Timeout. Ein Paket ohne gesammelte Tests schlägt fehl: Pytest-Exit-Code 5 ist ein Fehler, kein erfolgreicher Preflight. Ein fehlendes pytest liefert eine konkrete Meldung (Dev-Dependencies mit `uv sync` in `agent/` installieren) ohne automatischen Fallback. Der Interpreter, der die Pipeline ausführt, wird verwendet.

Der Legacy-Runner `unittest` ist nur opt-in über `--test-runner unittest` aktiv. Auch dort werden null entdeckte Tests abgelehnt. Zum bewussten Überspringen `--skip-tests` ergänzen.

## Check-Receipt

Nach einem erfolgreichen Check (auch bei explizit übersprungenen Tests) erzeugt die Pipeline ein privates JSON-Receipt unter dem ignorierten `dev/runs/`. Schema: `dev-preflight-1`. Es enthält den Git-Stand zum Checkzeitpunkt, den relativen Paketpfad, Runner bzw. `skipped`, die aufgenommenen Einträge `[{path, size, sha256}]` und die komprimierten Bytes aus dem CLI. Der Pfad des Receipts wird ausgegeben; Inhalte oder Credentials werden nie ausgegeben. Die Datei hat Modus `0600` und wird exklusiv angelegt.

Der Paketinhalt wird vor den Tests eingefangen. Nach den Tests wird der offizielle Dry-Run erneut ausgeführt und Pfad- UND Hash-Vergleich durchgeführt, damit Tests das Paket nicht unbemerkt verändern können (ignorierte Caches beeinflussen den Vergleich nicht). Bei einer Änderung gibt es einen Fehler und kein Erfolgs-Receipt. Dieses Receipt ist ein Inhalts-Snapshot, nicht der SHA des hochgeladenen Tarballs.

## Hochgeladenes Paket verifizieren (read-only)

```bash
agent/.venv/bin/python dev/pipeline.py verify-run --run-id run_EXAMPLE --receipt dev/runs/<receipt>.json --atira atira
```

Die Verifikation validiert das Receipt-Schema und die Dateieinträge streng (relative Pfade, keine Duplikate, nicht-negative Ganzzahl-Größen, 64-Hex-Hashes, Git-Objekt). Der Receipt-Pfad muss innerhalb des Repo-`dev/runs` liegen (inkl. Resolve-/Symlink-Grenzprüfung). Das exakte hochgeladene Archiv wird über den offiziellen CLI geholt: `COMMAND package ID --out <TempDir> --json`. Die `saved.path` muss innerhalb des Temp-Verzeichnisses liegen; die tatsächlichen komprimierten Bytes werden gehasht und gegen die Server-Angabe `sha256` geprüft. Tar-Mitglieder werden ohne Entpacken gelesen; unsichere/doppelte Pfade, Links/Nicht-Regular-Dateien werden abgelehnt, die gesamte ungepackte Lesemenge ist auf 20 MiB begrenzt. Normale Verzeichniseinträge dürfen nach Pfadprüfung ignoriert werden. Archivdateinamen, Byte-Größen und sha256 werden exakt mit den Receipt-Einträgen verglichen (keine Zeilenenden-Normalisierung). Bei Abweichung werden nur Namen/Kategorien ausgegeben und Exit 1 zurückgegeben (kein Verify-Receipt). Bei Übereinstimmung wird ein privates `dev-verified-package-1`-JSON unter `dev/runs` gespeichert (Run-ID, tatsächlicher Archiv-SHA, Receipt-Basename, Quell-Git aus dem Receipt, Zeitstempel).

Dies bestätigt, dass Dateinamen und Dateiinhalte des hochgeladenen Pakets zu diesem Preflight-Snapshot passen — nicht, dass der aktuelle Checkout diesem entspricht, und nicht, dass der Code erfolgreich ausgeführt wurde. Ein Beleg mit bewusst übersprungenen Tests kann ebenfalls für diesen Inhaltsvergleich verwendet werden; der Vergleich bestätigt keine bestandenen Tests. Es werden niemals submit/cancel/test-run-Uploads ausgelöst. Fehlende Metriken bleiben `null`; es gibt keine Annahmen über einen Run-Score. Fehlende CLI, Timeouts und korruptes JSON/Archiv werden sauber mit Exit ungleich 0 behandelt.

## Ergebnisse manuell festhalten

Die Run-ID und Kennzahlen aus der Plattform übernehmen:

```bash
agent/.venv/bin/python dev/pipeline.py record --run-id run_EXAMPLE --status completed \
  --quality 92.5 --eligible yes --seconds 420 --cost-usd 0.03 \
  --package agent --note "Verpackung fehlt bei einer Position"
```

Nur bekannte Kennzahlen angeben. Fehlende Metriken bleiben `null`, Eligibility bleibt ohne Angabe `unknown`. Zahlen müssen endlich und nicht negativ sein; Qualität liegt zwischen 0 und 100. Status: maximal 80 Zeichen ohne Zeilenumbrüche; Notiz: maximal 500 Zeichen. Keine Secrets oder Roh-Logs in die Notiz kopieren.

Jeder Aufruf erzeugt eine eigene JSON-Datei unter `dev/runs/` mit UTC-Zeit, vollständigem Commit, Branch und Dirty-Flag. Die Dateien haben Modus `0600` und sind bereits durch `dev/.gitignore` ausgeschlossen. Keine gemeinsame Log-Tabelle wird geändert. Das `record` bleibt manuell und unverändert.

## Drei verschiedene Git-/Nachweis-Ebenen

- **Manuelles `record`:** Git-Daten beschreiben ausschließlich den Checkout zum Zeitpunkt des Aufrufs. Sie beweisen nicht, welcher Commit einen Remote-Run ausgeführt hat.
- **Check-Snapshot (`dev-preflight-1`):** Inhalts-Snapshot des Pakets zum Checkzeitpunkt, inkl. Git-Stand. Kein Upload-Nachweis.
- **Verifiziertes Archiv (`dev-verified-package-1`):** Nachweis gleicher Dateinamen und Dateiinhalte im hochgeladenen Archiv und im Preflight-Snapshot; Archiv-Metadaten und Dateirechte werden nicht verglichen. Kein Beweis, dass der aktuelle Checkout diesem entspricht.

Run-ID und tatsächlich verwendetes Paket separat abgleichen; ein späteres Protokoll darf nicht als Upload-Nachweis interpretiert werden.

## Lokaler Oracle vs. Plattform-Score

Der lokale Leak-Scan und die Oracle-Referenzen sind eine Heuristik. Sie ersetzen nicht die tatsächliche Plattform-Bewertung. Ein grüner Check bedeutet nicht, dass die Plattform denselben Score vergibt.

## Pipeline selbst testen

```bash
agent/.venv/bin/python -m unittest discover -s dev/tests -v
```

Diese Regressionstests verwenden synthetische temporäre Repos und eine gemockte CLI. Sie rufen keine Live-Modelle oder Challenge-Websites auf. Die Regression für eine fehlschlagende pytest-Funktion läuft als echter lokaler Subprozess, um den alten unittest-Falschgrün-Fall abzudecken; unabhängige Prüfungen nutzen `--skip-tests`. Tar-Tests verwenden temporäre synthetische Archive ohne Netzwerk.
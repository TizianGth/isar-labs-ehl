# Synthetische RFQ-Portale

Vier lokale Websites pro Testfall: Tender, Spezifikation, ERP und Mail.
Sechs UI-Profile × fünf erfundene Datensätze = 30 Kombinationen. Nur Loopback,
keine echte Angebotsabgabe, kein Atira-Upload. Diese Dateien bleiben in `dev/`.

Aus dem Repo-Root:

```sh
make fake-test       # Portal-/Scorer-/Isolationstests ohne LLM
make fake-browser    # Chromium-Tests inkl. Forms/TAN/Receipt, ohne LLM
make fake-serve FAKE_PROFILE=scanned FAKE_CASE=baseline
```

Login für alle vier Seiten: `demo` / `synthetic-only`. Standardports 18540–18543.
`FAKE_PORT=0` wählt freie Ports. Mit Ctrl-C schließen. Jede Instanz besitzt eigene
Sessions und einen leeren Abgabestatus. Die Server brauchen nur Python-Stdlib.

Browser-Tests benötigen Playwright/Chromium aus dem bestehenden Agent-Setup.
Falls nötig zuerst `make setup`. Ein vorhandener anderer Interpreter lässt sich
mit `FAKE_PYTHON=/absoluter/pfad/python` wählen; `PLAYWRIGHT_BROWSERS_PATH` wird
bei Bedarf über die Umgebung gesetzt. Die Tests installieren nichts automatisch.
`fake-test` kann Browserprüfungen überspringen, wenn Playwright fehlt;
`fake-browser` führt auch die direkten Browser-Smokes aus und scheitert dann.

## Den Agenten testen

Terminal 1: `atira models proxy`. Terminal 2:

```sh
make fake-agent FAKE_PROFILE=workbench FAKE_CASE=future-withdrawal
make fake-matrix
# Alle 30 echten Agentenläufe nur bewusst starten; dauert deutlich länger:
agent/.venv/bin/python dev/fake_portals/run_matrix.py --agent agent --full
# Zeigt die Auswahl, ohne Browser/Modelle zu starten:
python3 dev/fake_portals/run_matrix.py --agent agent --dry-run
```

`fake-matrix` friert den Agenten einmal ein und führt sechs Tests nacheinander
aus: classic/baseline, modern/locale-boundary, legacy/blocked,
workbench/future-withdrawal, scanned/baseline, busy-office/shared-stock.
So wird jede UI und jeder Datenfall mindestens einmal geprüft, aber nicht jedes
Kreuzprodukt mit dem Modell. Die deterministischen Tests prüfen alle 30 Cluster.
Standard: 480 s pro Agent, davon 180 s Abgabereserve, zwei Modell-Slots.
Andere laufende Team-Tests können dennoch die gemeinsame Proxy-Latenz beeinflussen.

Berichte, Logs und unveränderte Runtime-Snapshots landen unter
`dev/runs/fake-portals/` (Git ignoriert sie). Jeder Lauf zeichnet Hashes,
Commit, Dirty-Status, tatsächliche Formulareingaben und Abgabebeleg auf.
Proxy-/Modellfehler stehen zusätzlich in `model_error_count`/`model_errors`,
auch wenn der Agent mit Exitcode 0 endet und keine Abgabe erreicht.
Die Matrix verwendet in allen Fällen denselben Snapshot, selbst wenn der
ursprüngliche Checkout zwischenzeitlich geändert wird.

## Varianten und Daten

| Profil | UI |
| --- | --- |
| classic | Deutsche Links und Tabellen |
| modern | Englische Labels, andere Pfade, Tabs |
| legacy | Alte Tabellen und echte eingebettete Frames |
| workbench | Dynamische Navigation, Disclosure, Q&A Load-more |
| scanned | Technische Dokumente und Preisbedingungen auf Canvas |
| busy-office | Verschachtelte Frames, Modals und echte Material-Popups |

| Fall | Schwierigkeit |
| --- | --- |
| baseline | Revisionen, Materialabgleich, Q&A, Konditionen, Freigaben, TAN |
| blocked | Aktive Alternative passt technisch nicht; korrektes `no_bid` |
| future-withdrawal | Rücknahme nach Stichtag verändert gültige Mengen nicht |
| locale-boundary | Zahlenformate, GBP, Preis pro 100, Rabatt und Staffelgrenzen |
| shared-stock | Zwei Positionen teilen Material und verfügbaren Lagerbestand |

`server.py` hält Daten/Formulare/TAN/Receipt; `variant_server.py` setzt die UI
und URLs um; `profile_*.py` implementieren die drei schwierigen UIs.
`cases.json`/`extra_cases.py` enthalten erfundene Eingaben. `scoring.py` und
die getrennte Funktion `expected_answer` enthalten manuell berechnete Goldens.
Der Server liefert diese Sollwerte nicht an den Agenten aus. Auch falsche
Geldbeträge werden unverändert gespeichert, statt automatisch korrigiert.

Nur eine tatsächliche Abgabe mit Beleg erhält Abgabepunkte. Der Score ist eine
eigene Entwicklungsmetrik, kein Atira-Score oder Nachweis privater Qualität.
Browser-Smokes prüfen die Testumgebung, nicht die LLM-Navigation. Diese kleinen
Portale simulieren keine Plattform-Ausfälle oder beliebig große ERP-Kataloge.

Der ursprüngliche Server wurde von Antigravity erzeugt, von Codex geprüft und
um UI-Adapter, unabhängige Goldens und Isolationstests ergänzt.

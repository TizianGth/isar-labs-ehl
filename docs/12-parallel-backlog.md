# Parallel-Backlog nach Prüfung von Claudes MVP

Stand: 10.10.2026, 15:49 Uhr. Basis: `f5dbada5587c5e6b023b519016b5a1fed31c43ef`; Runtime-Code zuletzt geändert in `9194b1f423d5bb330c332d2ee6e2951144494a3d`.
Diese Datei plant Arbeit; die vorgeschlagenen Features sind noch nicht implementiert.

## Verifizierter Stand und Grenzen

- Neuester geprüfter Commit: [f5dbada](https://github.com/TizianGth/isar-labs-ehl/commit/f5dbada5587c5e6b023b519016b5a1fed31c43ef), auf `origin/main`. Er ergänzt Cache-Ignore-Regeln und die Workstream-Doku; Runtime unverändert gegenüber `9194b1f`.
- In einer isolierten Kopie: `uv sync --locked`, anschließend `uv run --no-sync pytest -q`: **11 bestanden**. Keine Änderungen am Arbeitsverzeichnis von Claude.
- Bereits vorhanden: vier Browser, parallele Missionen, Fortschritts-Checklisten, Extraktionscache, `fill_fields`, `select(..., then_click=...)`, iframe-Nachladen, Evidence-Replay und 60/150-s-Timeouts pro Modellversuch.
- Während dieser Prüfung erledigt: `agent/.gitignore` schließt Caches und die virtuelle Umgebung aus. Als Feature geschlossen; ob der nächste Upload sie tatsächlich ausschließt, wird noch über das Paketmanifest verifiziert.
- `docs/11-mvp-status.md` meldet lokal 100/100 gegen das eigene Oracle. `dev/eval/score.py` bezeichnet seine Bewertung ausdrücklich als Näherung; das Oracle ist noch nicht durch die Plattform bestätigt. Das ist kein gemessener Plattform-Quality-Score.
- Untersucht: [Run run_27302bf9e8c90794](https://ehl-challenge.atira.ai/runs/run_27302bf9e8c90794), Paket `site-mission-agents-v1`, SHA-256 `e47224115b8a38fb20336409cec913ae9584485d4f8551a872cdbfd90b8eb5cc`.
- Das heruntergeladene Run-Paket entspricht nach Normalisierung der Zeilenenden in 22 von 23 Textdateien dem früheren Commit `8edf545`; nur ein Test unterscheidet sich. Es entspricht **nicht** dem neuesten Commit. Run-Befunde sind Regressionsfälle; die zugehörigen Mechanismen wurden im neuen Code separat geprüft.
- Konkrete Run-Befunde: bei 23,57/26,04/27,79 s dreimal dieselbe Vier-Seiten-Sammlung; bei 85,13/88,07 s dieselbe Fünf-Seiten-Sammlung, danach Wiederholungsfehler; bei 95,09 s `mission_loop_abort` der Spezifikations-Mission. Außerdem ein nicht mehr vorhandener Frame-Ref und eine abgewiesene, nicht beobachtete Viewer-URL.
- Neuester Code wartet in `erp_phase()` weiterhin auf `spec_progress()` bis kurz vor der Evidenzfrist, ohne auf das Ende einer fehlgeschlagenen Spezifikations-Mission zu reagieren. Der Run-Brief zeigte etwa 7.199 s Restbudget; feste Annahmen über 20 Minuten sind daher im Deadline-Code zu vermeiden.
- Eine Plattformaufnahme von Browser 2 zeigt den Dokumentviewer in einem separat scrollbareren iframe. Ein tatsächlicher Hänger durch einen Canvas ohne Textalternative wurde in diesem Run bisher **nicht** nachgewiesen. Vision bekommt synthetische Tests und gezielte Auslöser.

## Sofort startbare Arbeitspakete

Aufwand sind Zeitboxen für eine erste brauchbare Version einschließlich gezielter Tests, keine Zusagen. Nach der Zeitbox den kleinsten funktionierenden Stand integrieren. Neue Module zunächst isoliert bauen; Änderungen an zentralen Dateien übernimmt jeweils genau ein Owner.

| ID | Prio | Ergebnis | Zeitbox | Owner-Bereich |
| --- | --- | --- | --- | --- |
| B01 | P0 | Gesammelte Seiten und Links für das Modell nutzbar; Wiederholen bringt keine Arbeit | 60–90 min | Browser/Explorer |
| B02 | P0 | Abgebrochene Mission erneut gezielt bearbeiten; ERP wartet nicht bis zur Frist | 45–90 min | Claude/Orchestrierung |
| B03 | P0 | Abgabe anhand einer Bestätigung verifizieren und Formularwerte prüfen | 60–90 min | Claude/Submission |
| B04 | P1 | Qwen sieht bei konkreten Sichtbarkeitsproblemen einen Screenshot | 60–90 min | Vision-Modul |
| B05 | P1 | Hover, Tastatur, Frame-Scroll und begrenztes Warten | 45–60 min | Browser; nach B01 |
| B06 | P1 | Modellgewählte Downloads/ZIP/PDF als Evidenz lesbar | 90–120 min | Dokument-Modul |
| B07 | P0 | Revisionen und unvollständige Materialsuche durch Regressionstests absichern | 45–90 min | Regeln/QA |
| B08 | P0 | Gesamte Deadline über Calls, Warteschlangen, Retries und Abgabe einhalten | 45–60 min | Claude/Orchestrierung |
| B09 | P1 | Jeden Run eindeutig einem sauberen Paket und den ausgeführten Tests zuordnen | 45–60 min | Dev-Pipeline/QA |

## B01 — Sammlungen sichtbar machen und Wiederholungen beenden

**Befund:** `read_all_pages` speichert Text intern, gibt dem Modell aber fast nur die Seitenzahl zurück. Die aktuelle Observation bleibt die ursprüngliche Seite. Es gibt kein Tool zum Durchsehen der Sammlung und keinen Extraktionstyp für allgemeine Dokumentlisten. Im Run sammelt das Modell mehrfach und navigiert anschließend zwischen Startseite und Dokumentliste. Die Schleifenerkennung beendet später die Mission.

**Lieferung:** Kleine Sammlung mit `collection_id`, tatsächlichen Seiten-URLs, Anzahl, kurzem Textauszug und auf diesen Seiten beobachteten Links mit Kontext. `read_collection(collection_id, page)` zeigt bei Bedarf mehr. `extract` kann dieselbe Sammlung verwenden. Für eine Dokumentliste zeigt die Antwort die Dokumenttitel/Links; das Modell wählt die relevanten Dokumente. Wiederholtes Sammeln desselben unveränderten Bestands liefert die vorhandene Sammlung. Die Mission sieht „gesammelt, noch nicht extrahiert“ als eigenen Zustand.

**Schnittstelle:** Neue Datei `rfq/browser/collections.py`; Browser-Owner integriert den Adapter in `Explorer`. Keine zweite Person editiert parallel dessen Tool-Liste. Provenienz verwendet echte Seiten-URLs statt nur einer künstlichen `#collected-N`-Adresse.

**Fertig wenn:** Ein synthetischer Vier-Seiten-Index enthält den relevanten Dokumentlink nur auf Seite drei. Dieser Link wird dem Modell angeboten; erneutes Sammeln löst keine neuen Seitenabrufe aus. Ein anschließendes Extrahieren funktioniert und meldet klar, was gefunden wurde. Keine automatische Dokumentauswahl oder Komplett-Crawl.

## B02 — Mission-Abbruch als Ereignis behandeln

**Befund:** `erp_phase()` wartet auf eine erfolgreiche Spezifikations-Checkliste, auch wenn der Explorer längst abgebrochen ist. Eine Recovery für fehlende Anforderungen ist nicht implementiert.

**Lieferung:** Status je Mission `running/complete/incomplete/failed`, explizites Abschlussereignis und strukturierte fehlende Evidenz. Bei Abbruch entscheidet Qwen über eine begrenzte Recovery-Mission mit vorhandener URL, Sammlung und Fehlerhistorie. Pro Informationslücke zunächst höchstens ein zusätzlicher Versuch. Danach beste verfügbare Fakten weitergeben und Unsicherheit ausweisen. ERP kann mit nachweislich nutzbaren Kataloggruppen beginnen, sobald diese bekannt sind; für spätere Gruppen nur neue Arbeit starten.

**Owner:** Claude; zentrale Änderung an `orchestrator.py`, kleiner Anschluss an den Explorer-Status nach B01. Keine neue Agentenbibliothek und kein vollständiger Scheduler.

**Fertig wenn:** Ein simuliertes `mission_loop_abort` weckt den wartenden ERP-Pfad zeitnah. Der Test braucht weder die echte Frist noch Modellzugang. Fehlende Anforderungen werden nicht als vollständige Spezifikation gewertet. Doppelte Kataloggruppen erzeugen keine doppelten Missionen.

## B03 — Abgabe bestätigen und vorab prüfen

**Befund:** `_submit()` setzt weiterhin `submitted=True`, selbst wenn `Site.act()` einen `ERROR`-Text zurückgibt. Die Vorprüfung sucht nur Tender-Referenz und Nettosumme irgendwo im Seitentext. Das beweist weder richtige Positionen noch erfolgreiche Abgabe.

**Lieferung:** Zustand `prepared/attempted/confirmed/failed/unknown`. `validate_review(expected, observed)` liefert Abweichungen pro Position/Feld, Angebotsart und Erklärung. Das Modell liest den Review und ordnet Felder zu; Code vergleicht die strukturierten Werte mit dem berechneten Ergebnis. Nach dem finalen Klick Bestätigung/Beleg oder einen überprüfbaren abgegebenen Angebotsstatus verlangen. Bei unklarem Ausgang zuerst Angebotsstatus prüfen, damit ein Retry keine Doppelabgabe erzeugt. Browserdialog-Freigabe auch im Fehlerfall zurücksetzen.

**Schnittstelle:** Testbare Vergleichslogik in `rfq/agent/review.py`, Rückgabe `mismatches`, `missing`, `verified_fields`. Claude integriert sie und die Zustandsmaschine in `submit.py`. Die konkreten Bestätigungsmerkmale kommen aus Laufzeitbeobachtungen, nicht aus fest verdrahteten Website-Pfaden.

**Fertig wenn:** Klickfehler, sichtbarer Validierungsfehler und ausbleibende Bestätigung melden keinen Erfolg. Richtige Gesamtsumme bei falschem Material wird erkannt. Ein bestätigter Erfolg beendet die Mission. Ein Dry-Run bleibt ausdrücklich „vorbereitet“.

## B04 — Multimodaler Fallback mit kleinem Budget

**Befund:** `Site.screenshot()` existiert, wird aber nicht als Agenten-Tool angeboten. `observe.js` meldet Canvas ohne Textalternative; `llm.chat` verarbeitet derzeit keine vom Explorer gelieferten Bilder. DOM-Text erfasst nicht jede visuelle oder verdeckte Bedienhilfe.

**Lieferung:** Neue Datei `rfq/browser/vision.py`, zunächst ein Tool `inspect_visual(question, target_ref?, frame?)`. Bildaufnahme plus dieselbe aktuelle DOM-Observation gehen an Qwen über den vorhandenen Plattform-Proxy. Sichtbarer Frame-/Elementausschnitt bevorzugt, ansonsten Viewport. Rückgabe: lesbare Information, Unsicherheiten, vorgeschlagene nächste Aktion und passende aktuelle DOM-Refs. Bild selbst nicht in den Faktenbestand schreiben; extrahierte Fakten erhalten URL, Zeitpunkt und Herkunft „visual“.

**Auslöser:** Das Modell fordert das Tool an; fehlende Textalternative; wiederholt nicht erreichbares Element/überdeckte UI; sichtbar gefüllter Viewer mit leerer Textbeobachtung. Wiederholtes Sammeln mit ungenutzter Sammlung benötigt zuerst B01. Bei fehlenden Geschäftsfakten benötigt es zuerst B02.

**Ablauf:** Fehlergrund nennen → frische Observation → Screenshot mit begrenzter Frage → Qwen wählt nächste Aktion → aktuelle Referenz über bestehende Browser-Tools bedienen → beobachtbaren Fortschritt prüfen. Gleichen visuellen Zustand zwischenspeichern, erste Version höchstens zwei Bildaufrufe pro Recovery und mit verbleibender Deadline begrenzen. Erfolg ist neue Evidenz oder eine erfüllte UI-Bedingung, nicht bloß ein anderes Bild.

**Fertig wenn:** Synthetischer Canvas ohne `aria-label` liefert einen überprüfbaren Testwert; verdecktes Menü führt zu `hover`/`press_key` statt erfundenem Link; separater iframe kann passend aufgenommen werden. Widersprüchlicher Bild-/DOM-Wert bleibt als Konflikt erhalten. Live-Smoke über den offiziellen Qwen-Proxy prüft einmal, ob Bilder tatsächlich akzeptiert werden. Ein normaler lesbarer DOM-Schritt löst keinen Bildaufruf aus.

**Integration:** Vision-Owner liefert Modul und Testdaten; Claude verbindet den LLM-Aufruf in `llm.py`, Browser-Owner die Tool-Registrierung. Kein separates OCR-System nötig. Clef erst später für eine konkrete, messbare Auswahlaufgabe erwägen; Qwen reicht für die erste Version. Freie Koordinatenklicks sind zunächst nicht nötig. Falls später erforderlich: an aktuelle Bild-/Frame-Version und Viewport binden, danach frisch beobachten; finale Abgabe weiter durch B03 prüfen.

## B05 — Kleine Browser-Aktionen ergänzen

- `hover(ref)` für Menüs und Tooltips ohne brauchbare Textalternative.
- `press_key(ref?, key)` für Escape, Enter, Tab, F4 und sichtbare Suchhilfen.
- `scroll(ref?, frame?, direction, amount)` für Dokumentviewer und innere Scrollcontainer. `_do_scroll` existiert bereits teilweise, fehlt im Toolset.
- `wait_for_state(condition, ref?, timeout_s)` für enabled, sichtbare Fehlermeldung, Textänderung oder Abschluss einer vom Modell ausgewählten Aktion. Kurzes Maximum und frische Observation zurückgeben; keine langen blinden Sleeps.
- `read_all_pages` zusätzlich mit Buttons/„Mehr laden“ testen. Das Modell wählt den Ausgangspunkt; Code folgt nur dieser Ergebnisliste.

**Owner:** Derselbe Browser-Owner wie B01; gemeinsame Änderungen an `site.py`/`explorer.py` seriell halten. Tests: Hover-Menü, verzögert aktivierter Select, F4-Popup, innerer iframe-Scroll, Button-Pagination. Nach jeder Aktion gültige neue Refs liefern. Keine Website-spezifischen Klickpfade.

## B06 — Dokumente aus der ausgewählten Browseraktion lesen

**Befund:** `read_url` navigiert einen Helper-Tab und serialisiert HTML. Ein echtes Download-Ereignis, PDF oder ZIP wird nicht geparst; `_safe_goto` kann Navigationsfehler schlucken. Die Challenge nennt ZIP-Dateien ausdrücklich, PDF ist eine Generalitätslücke.

**Lieferung:** Neue Datei `rfq/browser/documents.py` mit `read_document(ref_or_observed_url, member?)`. Session-Cookies verwenden; MIME-Typ und Dateiname erfassen; HTML mit bestehendem Serializer, TXT/CSV als Text, ZIP als Dateiliste, anschließend nur das vom Modell ausgewählte Mitglied lesen. Downloads über Links und Buttons berücksichtigen. PDF-Text mit einer kleinen Bibliothek, gescannte Seiten über B04 erst bei Bedarf. Excel erst mit nachgewiesenem Bedarf; keine große Office-/OCR-Installation vorab.

**Grenzen:** Dateigröße, Archiv-Mitglieder, entpackte Gesamtgröße, Pfade und verbleibende Laufzeit begrenzen. Auth-Weiterleitung/HTML-Login als solchen melden. Keine autonome Auswahl aller Archivdateien.

**Fertig wenn:** Synthetisches ZIP mit CSV/TXT, Download-Button ohne `href`, kleines PDF und Login-Weiterleitung funktionieren. Mitgliedsauswahl und Quellen bleiben nachvollziehbar. Keine ZIP-Pfadtraversierung; übergroße Dateien brechen verständlich ab. Neue Abhängigkeit einschließlich Lockfile wird vom Integrator separat übernommen.

## B07 — Regeln gezielt gegen falsche Sicherheit testen

**Weiter vorhanden:** Substring-Erkennung „gültig“ trifft „ungültig“, „valid“ trifft „invalid“. `is_current=False` kann im Orchestrator trotzdem durch Status-Substring überstimmt werden. Materialstatus unbekannt wird teilweise als nutzbar akzeptiert. `no_bid` wird bei vorhandenen ungeeigneten Kandidaten gesetzt, ohne eine vollständige Suche nachzuweisen.

**Lieferung:** Synthetische Regressionen für negative/mehrdeutige Revisionsstatus, widersprüchliches `is_current`, unvollständige Materialliste, fehlende Pflichtattribute, unbekannten Materialstatus und fehlende Endreserve. Explizite belegte Daten, gezielte Nachrecherche und spätere beste Schätzung als verschiedene Zustände führen. Eine Schätzung darf nach dem Teamprinzip vor Frist verwendet werden, aber nicht als gesicherte Erkenntnis oder bewiesene Unmöglichkeit gelten.

**Owner:** Regeln/QA schreibt zuerst `tests/test_evidence_gaps.py`; genau ein Regeln-Owner verändert anschließend `resolve.py`. Anpassung an `current_revision()` mit Claude abstimmen. Keine pauschale dreifache Extraktion jeder Seite: nur Konflikte, fehlende Pflichtwerte und entscheidende unsichere Werte erneut untersuchen.

**Fertig wenn:** Negativer Status wird nicht als aktuell gewählt; unvollständige Suche beweist kein `no_bid`; unbekannter Status bleibt erkennbar unbekannt; ein nachweislich gesperrter vollständiger Trefferbestand wird korrekt verarbeitet. Bestehende Pricing-Tests bleiben grün.

## B08 — Deadline durchgängig einhalten

**Befund:** 60/150 Sekunden begrenzen einen Versuch. Bis zu sechs Versuche, Backoff und Warten auf `_slots` sind nicht an die Mission-Deadline gebunden. `Explorer.run()` kontrolliert Restzeit nur vor einem Aufruf. Abgabe und TAN haben keine durchgehende äußere Zeitbegrenzung.

**Lieferung:** Gemeinsame Deadline bis in Modell-/Tool-Aufrufe weitergeben. Semaphore-Wartezeit, Netzwerkversuch und Retry-Pause müssen hineinpassen; bei zu wenig Restzeit abbrechen. Evidenz- und Abgabereserve aus dem Run-Contract verwenden; Abgabe inklusive TAN mit harter Obergrenze und eindeutigem Ergebnis abschließen.

**Owner:** Claude; `llm.py`, `orchestrator.py`, kleine Anschlüsse an Explorer/Submission. Mit B04 denselben LLM-Adapter verwenden.

**Fertig wenn:** Tests mit künstlich hängendem Client, belegter Semaphore und kurzer Deadline enden pünktlich. Unterschiedliche Run-Budgets funktionieren. Fristende wird als solches protokolliert, nicht als erfolgreiche Abgabe.

## B09 — Paket, Tests und Run nachvollziehbar verbinden

**Befund:** Der geprüfte Run verwendet eine andere Revision als der aktuelle Commit. In diesem älteren Paket sind `__pycache__`/`.pyc`; Ignore-Regeln wurden inzwischen in `f5dbada` ergänzt. Das Run-Log in `dev/runs/LOG.md` ist noch leer. PR #1 liefert bereits einen Preflight, verwendet bisher aber unittest als Standard; das MVP nutzt pytest.

**Lieferung:** [Bestehenden Pipeline-PR #1](https://github.com/TizianGth/isar-labs-ehl/pull/1) auf `agent/` und pytest prüfen/anpassen. Upload-Manifest auf Wirkung der vorhandenen Cache-Ignore-Regeln prüfen, vor einem Run die Paketquelle festhalten und nach dem Upload den tatsächlichen SHA-256 ergänzen. Laufzeitquelltexte mit dem vorbereiteten Stand vergleichen. Manuelle Run-Aufzeichnung: Commit, dirty, Paket-SHA, Run-ID, lokales Oracle-Ergebnis separat von Plattform-Quality, Fehlerphase. Kleine CLI/Markdown reicht; kein Dashboard.

**Owner:** Codex als Prüfer/Orchestrator, Qwen als Implementierer nach bisheriger Teamaufteilung; Änderungen nur unter `dev/`. Manifest prüfen statt anzunehmen, dass jede Packfunktion Git-Ignore-Regeln gleich behandelt.

**Fertig wenn:** Alle MVP-pytest-Tests werden tatsächlich ausgeführt und ein absichtlich fehlschlagender Test blockiert. Ein Paket mit Cache/Dev-Fixtures wird erkannt. Ein hochgeladener älterer Stand wird sichtbar als solcher aufgezeichnet. Oracle-100 wird nicht als Plattform-100 bezeichnet.

## Aufteilung ohne gegenseitiges Überschreiben

| Arbeitsstrom | Sofort | Danach | Zentrale Dateien |
| --- | --- | --- | --- |
| Claude/MVP-Integration | B02 + B03 | B08, kleine Anschlüsse der anderen Module | `orchestrator.py`, `submit.py`, `llm.py` |
| Browser | B01 | B05, Tool-Registrierung für B04/B06 | `explorer.py`, `site.py`; neue `collections.py` |
| Vision | B04 als isoliertes Modul + synthetische Fälle | Qwen-Smoke, Integration mit Browser/Claude | neue `vision.py`, neue Tests |
| Dokumente | B06 als isoliertes Modul + synthetische Fälle | Integration mit Browser | neue `documents.py`, neue Tests |
| QA/Dev-Pipeline | B07-Testfälle + B09 | neue Run-Befunde, Review und Regressionslauf | neue Tests, `dev/`; Regeln-Fix mit einem Owner |

Codex koordiniert Reviews und Run-Auswertung; Qwen/Teammitglieder implementieren nach Aufgabenvergabe. Diese Tabelle weist Rollen zu und startet keine weiteren Agents oder Chats.

Vor Arbeitsbeginn vom aktuellen Team-Stand abzweigen, eigene Worktrees verwenden, Scope im PR festhalten. Integrator übernimmt kleine Änderungen an den gemeinsamen Dateien. Bei neuen Claude-Commits Befunde erneut prüfen, erledigte Tickets schließen. Keine umfassende Verzeichnis-Umbauaktion.

## Sinnvolle Reihenfolge für die nächsten Stunden

1. Jetzt: B01, B02, B03 und B07-Testfälle. B04/B06-Module parallel vorbereiten.
2. Nach dem ersten integrierten Stand: gezielte Tests und Paketprüfung, dann **ein** neuer Sandbox-Test des eindeutig bezeichneten neuesten Pakets. Bestehende Runs nicht ohne menschliche Freigabe abbrechen.
3. Aus dem ersten verifizierten Gesamtlauf den größten verbliebenen Blocker wählen. B04/B05 gezielt aktivieren; neue Screenshots/Logs als Regression sichern, nur unter `dev/`.
4. Geschwindigkeit erst danach: Mail-Inbox/Archiv parallel, ERP-Details bündeln. Der Code bündelt `extract_urls` bereits; zunächst messen, ob dort überhaupt Zeit verloren geht.
5. Regelmäßige Zwischenstände: bestätigt abgegeben? fehlende Fakten? Plattform-Quality? verbleibende Frist? Architektur beibehalten, Integration vor weiteren Feature-Starts.

## Bewusst nachrangig

Neues Multi-Agent-Framework, generischer Webseiten-Crawler, Vollzeit-Vision, routinemäßige Mehrfachextraktion jeder Seite, umfassendes UI-Dashboard und Modell-Routing-Umbau haben derzeit keinen belegten Nutzen gegenüber den offenen Blockern. Reine Kostenoptimierung folgt einem zuverlässig abgegebenen Angebot.

## Quellen

- Repository: `AGENTS.md`, `docs/11-mvp-status.md`, `dev/README.md`, aktueller Code und synthetische Tests, Basis oben genannt.
- Plattform: eigener Run, offizielles CLI-Paketarchiv und Screenshot über `run_screenshot`; bei Erstellung noch `running`, `score=null`.
- [Challenge](https://ehl-challenge.atira.ai/challenges/rfq): Modell wählt Navigation, Suchziele und relevante Dokumente; Helfer dürfen ausgewählte Inhalte lesen, paginieren, rechnen, prüfen und Formulare bedienen.
- [Fair Play](https://ehl-challenge.atira.ai/docs/fair-play.md): Szenariowerte und Run-Aufnahmen gehören nicht als Runtime-Defaults in das Agentenpaket.

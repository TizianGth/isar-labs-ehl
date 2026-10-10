# Isar Labs: Backlog nach dem offiziellen 100-Punkte-Test

Stand: 10.10.2026; geprüfter Code: `257e464`, Agent entspricht laut Team dem erfolgreichen Paket. Bewertung und Prioritäten stammen aus Codeprüfung, offiziellen Run-Daten und synthetischen Reproduktionen. Keine zugesagten Laufzeit- oder Token-Einsparungen.

## Ausgangspunkt

- Offizieller Test `run_ed06b2d2e8a71fac`: **Quality 100, eligible**, 634,055 Sekunden (10:34), 257 Modellaufrufe, $0,098391. Paket SHA-256 `e57a1bbd63ff…`.
- Das ist ein Sandbox-Test, kein Leaderboard-Eintrag. Beim Abruf der Teamdaten gab es keinen erfolgreichen scored Eintrag (`best: null`); die einzige gelistete Submission war fehlgeschlagen.
- Evidenzphase laut Runtime-Log fertig bei 432,35 Sekunden (~7:12). Offizielle Annahme bei ~10:34. Zwischen Evidenzende und Annahme liegen etwa 3:22 Minuten.
- Abgabe-Navigation: 55 erfolgreiche geloggte Modellaufrufe, 321.643 Input-Tokens, davon 163.072 gecacht. Diese Zahlen gelten für diesen Abschnitt; die verfügbaren stdout-Logs enthalten nicht alle 257 Plattformaufrufe.
- Plattformprofil: 99 Sekunden ohne Modellaufruf/Input zu Beginn und später weitere 72 Sekunden. „Keine Modellaktivität“ bedeutet nicht „kein Code arbeitet“; Browsernavigation/Timeouts müssen separat gemessen werden. Bei Browsern zeigt das Profil keine aufgezeichneten Screen-Changes: daraus keine weiteren Idle-Schlüsse ziehen.
- Ein perfekter Public-Lauf belegt keine Private-Robustheit. Andere Layouts, Informationsverteilung, Locale und Modell-/Website-Last sind die relevanten nächsten Tests.

## Reihenfolge

1. Erfolgreiches Originalpaket unverändert aufbewahren; Hash, Run-ID und offizielle Metriken dokumentieren.
2. Kleine Robustheitsfixes separat testen. Keine fünf zeitgleichen Sandbox-/Kontrollläufe: zunächst ein aktiver vergleichbarer Benchmark pro Team.
3. Erst nach einer reproduzierbaren Basis Änderungen einzeln benchmarken. Priorität: korrektes Angebot > zuverlässig rechtzeitig fertig > Kosten × Zeit.
4. Vor einer scored Submission genaue Paketversion und Testresultat zeigen; explizite menschliche Freigabe abwarten.

## P0 — Robustheit und Generalität

### W01: LLM-Slots bei Cancellation garantiert freigeben

**Belegt:** `rfq/llm.py::chat` gibt Slots bei Erfolg und normalen Exceptions frei, aber nicht bei `asyncio.CancelledError`. Ein synthetischer Test cancelt einen laufenden Request; anschließend ist der einzige Slot weiter blockiert.

**Warum kritisch:** `gather_evidence` wird an der Evidenzdeadline abgebrochen. Verlorene Slots können gerade die anschließende Abgabe verlangsamen oder blockieren. Dies ist ein möglicher Fehlerpfad, keine nachgewiesene Ursache eines konkreten Plattformfehlers.

**Änderung:** Semaphor lokal vor acquire festhalten; Request in try/finally, Release genau einmal und vor Retry-Schlaf. Cancellation weiterreichen.

**Abnahme:** Cancellation nach Erwerb, während Warten, normaler Erfolg, Retry und Austausch der globalen Semaphorinstanz; gesamter bestehender Testumfang grün. Separater PR, keine automatische Integration.

**Status:** Separat umgesetzt und geprüft: vier neue Regressionstests, insgesamt 36 Tests grün; Leak-Check und offizieller CLI-Paket-Dry-Run grün. Noch nicht integriert oder als geändertes Paket in der Sandbox getestet.

**Owner:** Codex prüft / Qwen entwirft. Aufwand etwa 30–60 Minuten.

### W02: ERP-Tasks zuverlässig beenden und Tab-Besitz wahren

**Belegt:** Ein synthetischer Test bestätigt, dass ein ERP-Child-Task den Abbruch der Parent-Phase überlebt. `erp_phase` erstellt Tasks mit `create_task`. Wird die Phase während ihrer Warteschleife gecancelt, gibt es kein finally zum Canceln/Awaiten der bereits gestarteten Tasks. Außerdem liefert `_tab` bei Tab-Erzeugungsfehler den bereits von einer anderen Mission verwendeten Basistab zurück.

**Risiko:** Überlebende Recherche kann nach Phasenende mit Abgabe um Slots konkurrieren und das Blackboard nachträglich ändern. Zwei Missionen auf einem Tab können zwischen Observation und Aktion falsche Refs verwenden; ein Lock um einzelne Aktionen schützt nicht den gesamten Entscheidungszyklus.

**Änderung:** ERP-Tasks bei jedem Ausstieg canceln und awaiten (oder TaskGroup). Bei spawn-Fehler kontrolliert sequenziell arbeiten oder den Job zurückstellen, statt denselben Tab gleichzeitig zu verwenden. Resultat-Snapshot beim Übergang zur Abgabe.

**Abnahme:** Synthetische Cancellation während der ERP-Warteschleife hinterlässt keine Task; spawn-Fehler führt zu keiner überlappenden Mission auf dem Basistab; vorhandene Gruppenstarttests bleiben grün.

**Owner:** Orchestrierung. Aufwand etwa 1–2 Stunden.

### W03: Fehlende Fakten an den passenden Finder zurückgeben

**Codebefund:** Die Missionen gehen überwiegend von Tender→Positionen, Spezifikation→Anforderungen, ERP→Material und Mail→Freigaben aus. Der Resolver kommt hauptsächlich nach der Recherche. Private Daten können anders verteilt sein.

**Änderung:** Kleine generische `missing_facts`-Liste je Position/Thema mit Grund, priorisiert nach blockierter Entscheidung. Nach einer fehlgeschlagenen Mission maximal eine gezielte Recovery auf einer anderen vom Modell gewählten Website. Provisional Resolve nutzt dieselben Regeln und protokolliert dabei keine finale Abgabe. Klare Zeitgrenze, kein unbeschränkter Super-Agent.

**Zusatz:** Completion-Checks auf dieselben Gültigkeitsregeln wie den Resolver stützen. `mail_progress` zählt derzeit Themen zur passenden Referenz, ohne dabei Absender/Stichtag/Preview zu prüfen. Ein gefundener Köder kann dadurch ein Thema scheinbar erfüllen.

**Abnahme:** Synthetische Fixture mit einer Freigabe auf einer anderen Website und einer ungültigen Mail zum selben Thema; gezielte Recovery schließt die echte Lücke. Keine Szenariowerte im Package.

**Owner:** Orchestrierung + Tender/Mail. Aufwand etwa 2–3 Stunden; wichtigste Änderung für Private-Qualität.

## P1 — Schneller werden, ohne Informationsverlust

### W04: Zahlendarstellung in der Prüfseite korrekt behandeln

**Belegt:** `review.py::_num` zwingt Strings standardmäßig auf `de-DE`. Erwartet 1234.5, beobachtet `"1234.50"` ergibt fälschlich einen blockierenden Mismatch. Dezimalpunkt kann aus einer anderen Site oder einer normalisierenden LLM-Extraktion kommen. Explizite Locale-Tags funktionieren bereits.

**Änderung:** Klare Schema-Konvention: Rohtext + Locale, oder normalisierte Zahlen als JSON-Zahlen. Unklare Schreibweisen wie `1.234` nicht blind „reparieren“, damit echte falsche Beträge nicht versehentlich akzeptiert werden.

**Abnahme:** Deutsch, Englisch, JSON-Zahl, explizite Locale, mehrdeutige Tausenderdarstellung und tatsächlich falscher Betrag. Die vorhandene Zwei-Versuche-/Deadline-Fallback-Politik explizit protokollieren; automatische harte Abgabe-Sperre würde zulässige Teilpunkte riskieren.

**Owner:** Pricing/Submission. Aufwand etwa 45–90 Minuten.

### W05: Formularaktionen bündeln und Werte direkt zurücklesen

**Messgrundlage:** Abgabe braucht ~3:22 Minuten und 55 Navigationsaufrufe. `fill_fields` existiert bereits: kein zweites ähnliches Tool einführen.

**Änderung:** Bestehendes Tool um strukturierte Rückgabe ergänzen: Feldlabel, gewünschter/aktueller Wert, disabled/validierungsfehler. Vom Modell gewählte unabhängige Felder gemeinsam füllen, einen Apply-/Next-Schritt ausführen und Ergebnis prüfen. Kaskadierende Selects bleiben abhängig und werden erst nach Apply erneut beobachtet.

**Token-Seite:** Im aktuellen Submit-Missionskontext wird die komplette Antworttabelle pro Schritt erneut übertragen. Sie ist teilweise gecacht, also Kostenersparnis nicht mit Tokenersparnis gleichsetzen. Nach modellseitiger Erkennung der aktuellen Position deren Daten fokussiert anzeigen; Gesamtübersicht für Review verfügbar halten.

**Abnahme:** Synthetische Formularfixture mit deaktiviertem Folgefeld, Apply, falsch gespeichertem Wert und anderer Positionsreihenfolge. Dann A/B gegen Baseline: 100 Punkte, weniger Aufrufe; keine feste Klickfolge und keine Layoutannahmen.

**Owner:** Browser + Submission. Aufwand etwa 1–2 Stunden.

### W06: Browserwartezeiten messbar machen, danach Startbarriere abbauen

**Codebefund:** `main.py` wartet auf alle vier `site.start()` mit jeweils 100-s-Timeout, bevor der Orchestrator startet. Ein langsamer Tab hält die anderen Missionen zurück. Das passt zur beobachteten frühen Pause; die verfügbaren Logs beweisen die genaue Ursache nicht.

**Änderung:** Zuerst Laufzeiten für connect/start/goto/settle/read_url/LLM-slot/request loggen. Briefanalyse und unabhängige Site-Missionen beginnen lassen, sobald deren Site bereit ist; unfertige Sites separat recovern. Kleine Navigation-/Helper-Timeouts statt pauschaler aggressiver Verkürzung.

**Abnahme:** Eine absichtlich langsame Site blockiert die drei anderen Missionen nicht. Keine doppelten Starts und keine konkurrierende Navigation im selben Tab. Benchmark bei normaler und künstlich verzögerter Site.

**Owner:** Browser/Orchestrierung. Aufwand etwa 1–2 Stunden.

### W07: Extraktion bündeln und mehrfach gestartete gleiche Requests vermeiden

**Codebefund:** `extract_urls` öffnet bis zu zwölf vom Modell gewählte Links parallel; Collection-Seiten werden parallel einzeln extrahiert. Ein `_cache` existiert, wird aber erst nach dem Request befüllt. Gleichzeitige identische Extraktionen können daher doppelte Calls erzeugen. Eine Extraktion nimmt bis zu 60.000 Zeichen; längere Dokumente werden abgeschnitten.

**Änderung:** Cache-Key auf Kind + Kontext + Quelle/Content abstimmen; laufende identische Requests zusammenführen. Kleine, vom Modell ausgewählte Dokumentgruppen in einer strukturierten Extraktion mit separaten Quellen behandeln. Lange ausgewählte Dokumente sichtbar chunkweise lesen, statt still zu kürzen. Chunking/Batches mit begrenzter Parallelität; keine Vollseiten-Crawler.

**Abnahme:** Zwei gleichzeitige gleiche Requests verursachen einen Call; unterschiedlicher Kontext oder Quelle wird nicht falsch zusammengeführt. Entscheidende Information am Dokumentende bleibt erhalten. Provenienz je Fact. Batch gegen Einzelaufrufe benchmarken: gleiche Feldkorrektheit, Kosten/Latency messen.

**Owner:** Evidence/LLM. Aufwand etwa 1–2 Stunden. Erst nach W01/W02.

## P2 — Kleine Pipeline- und Harness-Aufgaben

### W08: Ein Benchmark-Bericht pro Paket, keine konkurrierenden Kontrollläufe

- Bereits vorhandenen Pipeline-PR #1 auf aktuellen Root/Makefile prüfen; keinen zweiten Preflight implementieren.
- Run-ID, Codename, Commit, exakter Paket-Hash, Quality/Eligibility, Calls, Tokens/gecacht, Kosten, Laufzeit und bestätigtes Abgabeergebnis zusammenführen.
- Öffentlicher Score und lokales Oracle getrennt; Test-Run und scored Submission getrennt.
- Ein erfolgreicher Public-Test reicht für einen Baseline-Nachweis, nicht für eine Erfolgswahrscheinlichkeit. Wenn Slots/Zeit reichen, einmal identisches Paket wiederholen; keine Prozentquote aus zwei Läufen behaupten.
- Labels als Komfort, Paket-Hash als Identität. Kein „Hamburg“ im Runtime-Code erforderlich; CLI-Paketlabel verwenden.
- Plattform kann direkt nach Annahme den Run-Token sperren. Im 100-Punkte-Lauf erzeugte der anschließende Outcome-LLM-Call mehrfach 401. Nicht als schlechte Quality werten; permanente 401 nicht sechsmal retryen und unklaren lokalen Status mit offiziellem Run-Ende abgleichen. Kein erfundenes `confirmed`.

**Owner:** Codex/Dev-Pipeline. Aufwand etwa 30–60 Minuten.

### W09: Kleine adversariale Fixture-Suite statt weiterer Frameworks

Fünf gezielte Fälle: (1) andere Locale, (2) gültige/ungültige Revision nebeneinander, (3) Köder-Mail vom falschen Absender oder nach Stichtag, (4) beendete/abgebrochene Task und langsame Site, (5) umsortierte Formularfelder + Kaskaden-Selects. Danach (6) verschobene Informationsquelle und (7) langes Dokument mit relevantem Schluss.

Erwartungen sind synthetisch, unabhängig von der Public-Musterlösung. Tests prüfen Entscheidungen/erhaltene Fakten/gespeicherte Felder, nicht bloß ob das Tool keinen Fehler wirft. Ganze End-to-End-LLM-Tests getrennt von schnellen deterministischen CI-Tests.

**Owner:** Teammitglied für QA. Aufwand etwa 1–2 Stunden.

## Konkrete Verteilung für fünf Personen

| Person/Rolle | Zuerst | Danach |
|---|---|---|
| Claude / Kernagent | W02 Task-Lifecycle | W03 gezielte Missing-Fact-Recovery |
| Browser-Owner | W06 Start-/Wartezeitmessung | W05 Formular-Rücklesung gemeinsam mit Submission |
| Pricing/Submission | W04 Locale-Vertrag | W05 fokussierter Submit-Kontext |
| Evidence/QA | W09 synthetische Variationen | W07 Batching/Cache nur bei stabiler Basis |
| Codex / Orchestrator | W01 geprüfter separater PR, Baseline/Backlog | W08 Paket-Provenienz, Benchmarkvergleich |

Datei-Ownership abstimmen: W01 und W07 berühren beide `llm.py`; W02/W03 beide `orchestrator.py`. Diese Änderungen nacheinander integrieren. Neue Runtime-Versionen zunächst Test-Run, nicht direkt Submission. Keine Nachrichten an andere Team-Chats ohne menschliche Beauftragung.

## Was jetzt keinen guten Return bringt

Neues Agent-Framework, Modellwechsel ohne Qualitätsbenchmark, Embedding-/Vektordatenbank, umfassendes Multi-Agent-Debattieren, pauschal doppelte Extraktion jeder Seite, Screenshot bei jedem Schritt oder fest verdrahtete Public-Klickpfade. Eine kleinere funktionierende Tool-Schicht, saubere Cancellation und überprüfte Fakten sind innerhalb der verbleibenden Hackathonzeit wertvoller.

## Erfolgskriterium

Ein offiziell erfolgreicher, eindeutig identifizierter scored Eintrag; ein Agent, der unter Variationen gültige Fakten findet und rechtzeitig abgibt; bei gleicher Quality geringere Kosten × Laufzeit. Public-Quality kann von 100 nicht weiter steigen. Verbesserungen sollen vor allem Ausfälle und Private-Fehler reduzieren.

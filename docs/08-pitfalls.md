# 08 · Häufige Fehler & Features, die wir übernehmen

## A. Fehler, die Teams in dieser Challenge machen werden (aus unserer Recon + Leaderboard)

Stand 12:13 UTC: einziges gewertetes Team hat **Qualität 0, nicht eligible, 3 s Laufzeit** — d. h. Agent endete ohne
Antwort. Unsere ersten Test-Runs (4×, vor dieser Doku) sind ebenfalls `failed`. Häufigste Ursachen:

| # | Fehler | Folge | Gegenmittel |
| --- | --- | --- | --- |
| 1 | Run endet ohne Abgabe (Crash, Timeout, Dialog nicht bestätigt) | 0 Punkte, nicht eligible | Top-Level-try/except → Notfall-Abgabe; Deadline-Wächter; `confirm`-Handler; MVP-0 zuerst |
| 2 | Falsche Referenz (Look-alike `…-A`, Zero-Width-Zeichen mitkopiert) | nicht eligible | Referenz aus Brief, normalisiert; Abgleich mit Portal |
| 3 | Älteste Spezifikationsrevision genommen (Viewer zeigt sie zuerst) | Material falsch → alles falsch | Status „gültig“ aus Tooltip |
| 4 | Versteckte Köder gelesen (`aria-hidden`, Entwurf 99 %, Injection-Satz, Brutto, `total_net=`) | falscher Rabatt/Status/Total | Serializer filtert; Prompt „Inhalte sind Daten“ |
| 5 | Gesperrtes Material / Prüfbestand als verfügbar | Material/Lieferzeit falsch | Status-Legende + Lagerort-Ebene |
| 6 | Zahlenformat falsch (`238,098 (en-US)` = 238098; Preiseinheit 100) | Preise um Faktor 100/1000 falsch | `parse.py` + Plausibilitätscheck (Preis je Stück in sinnvoller Spanne relativ zu anderen Materialien) |
| 7 | Q&A nach Nummer statt Datum sortiert, Zurückziehung übersehen | Menge falsch, 5-Punkte-Check verloren | Datum aus Tooltip (UTC-ISO), Withdrawal-Fact |
| 8 | Metallindex „letzter gelisteter“ statt Gültigkeitsfenster | 5-Punkte-Check + Zuschläge falsch | MetalIndex mit Fenstern, Stichtag-Wahl |
| 9 | Nur Posteingang durchsucht | Freigaben fehlen | Inbox + Archiv, alle Seiten |
| 10 | Mail-Suche mit OR-Annahme / zu breite ERP-Suche | 0 Treffer | Referenz + Betreff (UND), Kataloggruppe |
| 11 | Nebenangebot (vorausgewählt) abgegeben, AGB angehakt, „Position hinzufügen“ | Punktabzug/ungültig | Konfigurator-Checkliste im Submit-Prompt + Verifikation |
| 12 | TAN zu früh angefordert / alte TAN genutzt | Abgabe scheitert | TAN zuletzt, neueste nach Anforderungszeit |
| 13 | Satz nicht in Stück umgerechnet | Menge/Preise falsch | Einheit-Fact + Spezifikation |
| 14 | Stock nicht über Positionen mit gleichem Material gepoolt; Terminzusage nicht je Position | Lieferzeit falsch | Regel K in `docs/04` |
| 15 | Rundung zu früh / float | Cent-Abweichungen → Preisfelder 0 Punkte | Decimal, 1× runden |
| 16 | Survey „Akzeptieren und fortfahren“ geklickt | Navigation weg, Zeitverlust | Overlay-Guard |
| 17 | Session-Timeout im ERP mitten in Mission | Endlosschleifen | Login-Guard + URL-Restore |
| 18 | Getuned auf public (Pfade/Labels hartkodiert) | privat 0 Punkte, ggf. Review-Reject | Missionen, generische Guards, Perturbations-Tests |
| 19 | Nur das Leaderboard optimiert, riskantes Paket zuletzt submitted | Finale nutzt das **letzte** bestandene Paket | Release-Disziplin (`docs/06`) |
| 20 | Qwen-Concurrency-Limit (32/Team) durch parallele Test-Runs aller Teammitglieder | Queue-Wartezeiten, Timeouts | Test-Runs koordinieren; Semaphore im Client |

## B. Bekannte Fehlermodi von LLM-Web-Agents (Literatur & Praxis)

- **Stille Fehlentscheidungen**: plausible, aber falsche Ergebnisse ohne Fehlermeldung → jede Zahl mit Zitat/Provenienz,
  deterministische Validierung, Audit-Pass ([Skyvern-Vergleich](https://www.skyvern.com/blog/browser-use-vs-stagehand-which-is-better/),
  [alexcloudstar](https://alexcloudstar.com/blog/ai-browser-agent-stagehand-browser-use-playwright/)).
- **Token-Explosion durch rohes HTML** → Accessibility-Tree-artige, kompakte Observations
  ([DEV-Vergleich 2026](https://dev.to/agdex_ai/ai-native-browser-automation-in-2026-browser-use-stagehand-steel-and-playwright-mcp-compared-160n)).
- **Versteckte Injection & Distraktoren** (HTML-Kommentare, data-Attribute, hidden text, Formular-Hidden-Fields,
  semantische Attribute): Agents fallen in ~25 % der Fälle auf Injections herein
  ([BrowseSafe-Bench](https://arxiv.org/html/2511.20597v1), [WASP](https://arxiv.org/pdf/2504.18575v1.pdf)). Hier
  konkret: versteckte „99 %“-Sätze, Entwurfs-Vorschauen, Umfrage-CTAs.
- **Benchmarks**: hand-geschriebene Skripte ~98 % vs. Agent-Frameworks ~72–78 % auf WebVoyager (unbestätigte Zahlen
  aus [nxcode](https://www.nxcode.io/ko/resources/news/stagehand-vs-browser-use-vs-playwright-ai-browser-automation-2026))
  → Lehre: so viel *Ausführung* wie möglich deterministisch (Guards, Formular-Füllen, Rechnen), LLM nur für
  Entscheidungen — genau das, was die Regeln erlauben.
- **Schleifen** (gleiche Aktion wiederholt), **Halluzinierte Refs**, **veraltete Observation nach async Updates**
  → Loop-Detektor, Refs nur aus aktueller Observation akzeptieren, nach jeder Aktion neu beobachten.

## C. Features, die wir „klauen“ (Open-Source-Vorbilder)

| Feature | Vorbild | Umsetzung bei uns |
| --- | --- | --- |
| Nummerierte Element-Refs in einer kompakten DOM-Darstellung | browser-use (DOM-Serializer mit Indizes), Playwright MCP (`aria snapshot` mit `ref=`) | `data-agent-ref` + `[eN]` im Serializer |
| `observe` / `act` / `extract` getrennt | Stagehand | Explorer-Tools `observe`, Aktionen, `report_facts` mit Schema |
| Strukturierte Extraktion mit Schema statt Freitext | Stagehand `extract(zod)` | pydantic-Schemas, Validierungsfehler zurück ans Modell |
| DOM-Distillation je Aufgabe (nur relevanter Teil) | Agent-E | `observe(scope=…)`, Diff-Modus |
| Planner + Executor getrennt | Agent-E, Magentic-One | Orchestrator plant Missionen, Explorer führt aus |
| Set-of-Mark-Screenshots als Fallback | WebVoyager/SeeAct | nur bei Canvas/Bild ohne Label |
| Selbstprüfung vor Abschluss | Reflexion-Pattern | Audit-Pass vor Abgabe, Konsistenzchecks |
| Deterministische Replays für Debugging | Skyvern/Stagehand Caching | JSON-Event-Log + `run_inputs`/`run_screenshot` der Plattform |

## D. Kosten × Zeit — Stellschrauben

1. Thinking nur wo nötig (Navigation ohne Thinking ≈ 3× schneller, gemessen).
2. 4 Browser parallel; Missionen ohne Abhängigkeit sofort starten.
3. Ausgewählte Dokumente/Listen per HTTP im Browser-Context holen statt durchklicken.
4. Stabiler System-Prompt-Prefix → Cached Input ($0.016/M statt $0.15/M).
5. Kein Screenshot, wenn DOM reicht.
6. `"models": ["qwen"]` (ohne Clef, falls nicht genutzt) — Warm-up zählt zwar nicht, aber unnötige Abhängigkeit.

# 12 · Optimierungsanalyse: Ist-Zustand, Evidenz, Alternativen, Entscheidung

Stand: 2026-10-11 · geprüfter Code: Branch `feat/agent-improvements`, Commit `006bc96` (Paket `d4e94c07f4b3`).
Die umsetzbare Roadmap steht in [`docs/13-optimization-roadmap.md`](13-optimization-roadmap.md).
Dieses Dokument liefert die Begründung.

Markierungen:
- **[gemessen]** stammt aus Plattformdaten.
- **[Schätzung]** ist abgeleitet; die Annahmen stehen jeweils dabei.
- **[unverifiziert]** ist offen und braucht eine Messung.

Zeilenangaben beziehen sich auf `006bc96`.

## 0. Kurzfassung

- **Stand:** Die aktuelle Version C liegt auf dem Leaderboard auf Platz 1: Qualität 100, eligible, Kosten × Zeit 31,1 $·s; Platz 2 liegt bei 49,7.
  - Vier Sandbox-Runs von C: 299–431 s, $0.100–0.110, C×T 31,1–47,5 (Median 33,2).
  - Die Streuung innerhalb *eines* Pakets ist größer als die meisten Einzeleffekte. Jede Entscheidung braucht deshalb gepaarte Wiederholungen.
- **Kosten:** Navigations-Calls kosten ≈ 62 % (≈ 157 Calls à ≈ $0.00044). Ungecachter Navigations-Input allein macht ≈ 50 % der Gesamtkosten aus. Extraktionen kosten ≈ 38 %.
  - Der größte Hebel ist die **Zahl der Navigationsschritte**, nicht der Preis pro Token.
  - Grob 2,5× mehr Navigationsentscheidungen als nötig. Die Treiber: 18–22 `wait_for` pro Run als Warteschleife auf Hintergrund-Extraktionen, getrennte Schritte für „extrahieren“ und „weiter navigieren“, wiederholte Gates und Suchen pro Tab.
- **Laufzeit, kritischer Pfad:**

  | Abschnitt | Dauer |
  |---|---|
  | Brief | ≈ 13–17 s |
  | Spezifikation bis Anforderungen (ERP-Start bei ≈ 100 s) | ≈ 85 s, in der Sandbox nicht messbar |
  | ERP, langsamste Gruppe | ≈ 110–140 s |
  | Abgabe | ≈ 72–90 s |

- **Neue Nebenwirkungen der Hintergrund-Extraktion:**
  - Die 10 Modell-Slots sind 14–62 s pro Run voll belegt; Navigations-Calls warten hinter Extraktionen.
  - Hedging verdoppelt gesunde, nur lange Calls; verlorene Zwillinge werden mit abgerechnet.
- **Blinder Fleck:** Die Plattform behält nur die letzten 256 stdout-Zeilen. Die ersten ≈ 100 s jedes Sandbox-Runs sind unsichtbar.
- **Robustheitslücken für den privaten Datensatz:** Dort sind die Informationen laut Plattform anders verteilt.
  - Es fehlt eine gezielte Suche fehlender Evidenz auf einer anderen Site.
  - Zwei Rollen auf derselben Site teilen sich einen Tab.
  - Es gibt keine Perturbations-Tests.
- **Entscheidung:** Die bestehende Architektur gezielt optimieren und zuerst Mess- und Robustheits-Gates einziehen. Kein Umbau. Clef, Frontier-Modelle und eine Planner/Executor-Neuarchitektur bringen keinen belastbaren Vorteil.
- **Erwarteter Korridor nach der Roadmap [Schätzung]:**
  - Median C×T ≈ 33 → **≈ 23** (zentral; ≈ 285 s, ≈ $0.081)
  - optimistisch ≈ 17, pessimistisch ≈ 31
  - Qualität und Eligibility sind Bedingung, nicht Teil der Rechnung.

## 1. Datengrundlage und Methode

| Quelle | Inhalt | Grenzen |
|---|---|---|
| `GET /v1/runs/{id}` | offizielle Zeit (`score.duration_ms`), Kosten, Qualität, Eligibility, Paket | keine |
| `GET /v1/runs/{id}/llm-calls` | jeder Modell-Call: Start, Latenz, `queue_ms`, Tokens (ungecacht/gecacht/Output), Kosten, Status | kein Tag der Mission; `queue_ms` ≈ Latenz − 0,3 s, daher als Wartezeit unbrauchbar [unverifiziert] |
| `GET /v1/runs/{id}/logs.jsonl` | unsere JSON-Zeilen | nur die letzten 256 stdout-Zeilen pro Run (§3.6) |
| `dev/runs/n123.log` | vollständiges lokales Log (Version B, Scouting-Sites) | lokales Netz instabil (`ERR_NETWORK_CHANGED`, Proxy-Timeouts): nur für Abläufe, nicht für Zeiten |
| Lokale Cache-Probe über `atira models proxy` | gecachte Tokens bei wachsendem stabilem Präfix | Dienst lieferte während der Probe Timeouts und 500er |

**Klassifikation der Calls:**
- **Navigation:** Prompt einschließlich Cache ≥ 3.300 Tokens. Das sind System-Prompt + Tools ≈ 3.136 Tokens plus Inhalt; darunter fallen auch `steps`-Calls der Abgabe.
- **Extraktion:** alle übrigen Calls (JSON-Modus). Der Brief zählt dazu.

**Versionen:**
- **A:** Ausgangsstand vor dieser Optimierungsrunde (Commits `011dadc`/`cbf0f2e`/`6692197`)
- **B:** `steps`-Tool, Review-Prefetch, Start-Overlap (Paket `96cfe31c9b11`)
- **C:** zusätzlich Hintergrund-Extraktion für ERP/Mail und parallele ERP-Tabs (`006bc96`, Paket `d4e94c07f4b3`)

## 2. Ist-Zustand: so ist es implementiert

### 2.1 Ablauf und Abhängigkeiten

```
main.py:70-72      Sites laden im Hintergrund (Site.begin_start); Orchestrator startet sofort
run_all (orchestrator.py:573)
 ├─ read_brief (:125)            1 JSON-Call (≈ 2.950 in / ≈ 680 out), regex-Fallback für die Referenz
 ├─ gather_evidence (:378)       parallel:
 │    tender  (Vergabeportal)    synchron extrahierend, Checkliste tender_progress (:207)
 │    specification (Lieferant)  synchron, spec_progress (:256), bis 3 Versuche
 │    mail    (Webmail)          background_extraction=True (:414), mail_progress (:310)
 │    erp_phase (:482)           je Kataloggruppe eine Mission, gestartet sobald Positionen+Anforderungen
 │                               bekannt (:498-509), eigener Tab je Gruppe (on_new_tab), background_extraction
 ├─ resolve (:559)               deterministische Regeln (rules/resolve.py), Trace
 ├─ recheck_no_bid (:429)        nur bei no_bid: Q&A frisch neu lesen
 └─ submit_bid (submit.py:215)   Explorer mit SUBMIT_TOOLS inkl. steps, get_tan (Mail-Tab), submit_bid
                                 (Review-Lesung aus der TAN-Wartezeit, review.py-Vergleich, confirm-Dialog)
```

**Explorer** (`explorer.py:153-231`): ein Tool pro Schritt.
- Prompt aus System + Tools, Mission-Intro, „STEPS SO FAR“ (letzte 20 Zeilen, `:185`), letztes Ergebnis (≤ 3.000 Zeichen), Hintergrund-Ergebnisse, Checkliste, Beobachtung (≤ 14.000 Zeichen, `site.py:20`).
- Loop-Schutz über die Signatur Aktion + Beobachtung (`:197-219`).
- Die Mission endet, sobald die Code-Checkliste vollständig ist (`:168-176`).

**Hintergrund-Extraktion** (`explorer.py:270-299`): `extract`/`extract_urls` laufen als Task.
- Ergebnisse erscheinen beim nächsten Schritt.
- `finish` oder eine vollständige Checkliste warten auf offene Tasks (`:169-171`, `:199-205`).

**Extraktion** (`orchestrator.py:77-122`):
- 1 JSON-Call pro Seite bzw. pro 60k-Teil.
- Cache und Zusammenlegung gleichzeitiger identischer Requests über Text-Hash.
- Ergebnis wandert ins Blackboard.

**LLM-Client** (`llm.py`):
- ein globales Semaphor mit 10 Slots (`:20`)
- Retries und Deadline (`:51-112`)
- Hedging nach 12 s (Tools) bzw. 30 s (JSON), adaptiv 3× Median der letzten 30 Calls *je Typ* (`:27`, `:115-118`), höchstens 2 Zwillinge (`:28`)

### 2.2 Was bereits wirkt (behalten)

| Mechanismus | Beleg |
|---|---|
| Code-Checklisten mit Auto-Abschluss | jede Mission endet am Checklistenende (`mission_complete`-Events in allen C-Runs) |
| Parallele Missionen je Site, ERP je Gruppe in eigenem Tab | ERP-Gruppen enden in C bei 138–249 s statt 214–318 s in B |
| Hintergrund-Extraktion | Evidenzende C 214–267 s gegenüber B 287–319 s [gemessen] |
| `steps`-Tool (Formularpakete mit Ref-Rematch) | Abgabe 27 statt ≈ 51–57 Calls, 89–100 s (C, ohne Ausreißer) statt 135–259 s (A/B) |
| Review-Lesung während der TAN-Wartezeit | in allen 4 C-Runs genutzt, 44/44 Felder verifiziert |
| Start-Overlap (Brief parallel zum Site-Start) | erster Call bei ≈ 4,6 s statt 6–102 s (A) |
| Deadline-Kette, Hedging gegen hängende Calls, Tab-Recovery | keine C-Run-Ausfälle durch eigene Hänger |
| Site-Memory (Seitentypen) | in Checklisten sichtbar; Wirkung nicht isoliert gemessen |

### 2.3 Abweichungen zwischen Dokumentation und Code (nicht als Ist-Zustand lesen)

- `docs/02-architecture.md` beschreibt `report_facts`, `ask_blackboard`, ein `observe`-Tool, pydantic-Facts und Thinking für Extraktionen. **Implementiert sind:**
  - Extraktion über separate JSON-Calls ohne Thinking (`orchestrator.py:27`, Default aus)
  - Facts als `dict` im Blackboard
  - keine `ask_blackboard`-Tools
- `docs/06-testing.md` nennt `llm/loop.py`, `rules/pricing.py` und `rules/validate.py`. Die Logik liegt tatsächlich in `explorer.py` und `rules/resolve.py`. `dev/tests/` enthält nur `show_observe.py`; es gibt **keine** Fixture- oder Perturbations-Tests.
- `docs/10` §3 empfiehlt „2× extrahieren und vergleichen“. Das ist nicht implementiert, und hier wird es auch nicht empfohlen (§4, A15).

### 2.4 Bekannte Korrektheits- und Generalisierungsprobleme

1. **Zwei Rollen auf derselben Site** (`orchestrator.py:422-424`, `:486`):
   - `site_for(role)` liefert dasselbe `Site`-Objekt. Zwei Explorer arbeiten dann gleichzeitig auf *einem* Tab mit wechselnden Refs.
   - Privat realistisch: Laut Plattform-Regeln ist die Information „anders verteilt“, etwa ein Lastenheft als Dokument im Vergabeportal.
2. **Keine Suche auf einer anderen Site** (W03 offen, `docs/11`):
   - Findet z. B. die Spezifikations-Mission keine Anforderungen, startet keine ERP-Mission, weil `erp_groups` die Anforderungen braucht (`:533-539`).
   - Folge: kein Material → `no_bid`. `recheck_no_bid` liest nur die Q&A neu (`:429-461`).
   - Ein solches falsches `no_bid` kostet ≈ 85–95 Punkte.
3. **Hintergrund-Extraktion verzögert Rückmeldungen:**
   - Das Modell sieht „NOTHING of kind …“ oder Ladefehler einen oder zwei Schritte später.
   - Auf unbekannten Layouts kann das Umwege erzeugen; auf public nicht beobachtet.
   - Es gibt keinen Schalter, um sie ohne Codeänderung abzuschalten (fest `True`, `:414`, `:555`).
4. **Doppelte Mail-Extraktionen:** 22–30 `mail_message` pro Run bei ≈ 19 relevanten Nachrichten [gemessen, `evidence_done.kinds`].
5. **Beobachtbarkeit:** Die Frühphase ist in Sandbox-Runs nicht einsehbar (§3.6).

## 3. Evidenz

### 3.1 Runs und Varianz

| Version | Run | Art | Zeit (s) | Kosten | C×T | Calls | Hedges | Evidenzende (s) | Abgabephase (s) |
|---|---|---|---|---|---|---|---|---|---|
| C | `run_f0fadd6cb4e855ce` | scored (`sub_9c28…`) | 298,6 | 0,1041 | **31,1** | 254 | 1 | 214,1 | 88,7 |
| C | `run_5ec3ef02a134d258` | scored (`sub_7d5a…`) | 317 | 0,1035 | 32,8 | 249 | 7 | 221,0 | 100,4 |
| C | `run_e0c6871f8b2222c8` | Test | 335 | 0,0999 | 33,5 | 249 | 3 | 242,4 | 97,1 |
| C | `run_fef81fa10373c7ee` | Test | 431 | 0,1101 | 47,5 | 268 | 8 | 266,6 | 169,1 (Tab-Recovery bei 401 s) |
| B | `run_ab2f9cfa8bff8ff6` | Test | 435 | 0,0890 | 38,7 | 226 | – | 287 | 154 |
| B | `run_6a0ed208207b0870` | Test | 449 | 0,0988 | 44,4 | 230 | 9 | 319 | 135 |
| B | `run_12e64ab307dab512` | Test | 535 | 0,0945 | 50,6 | 207 | – | – | – |
| A | `run_6e07bf4619c1ff9c` | scored | 439 | 0,0951 | 41,7 | 241 | 0 | 243 | ≈ 195 |
| A | `run_09624e2411da68ac` | Test | 598 | 0,1018 | 60,9 | 262 | – | 370 | – |
| A | `run_ed06b2d2e8a71fac` | Test | 634 | 0,0984 | 62,4 | 257 | – | 432 | ≈ 200 |
| A | `run_56b54d30340a6231` | Test | 665 | 0,1128 | 75,0 | 270 | 10 | 409 | 259 |

- Alle hier gelisteten Runs: Qualität 100, eligible.
- **Medianwerte C:** 326 s, $0.1038, C×T 33,2.
- **Ausreißer:** `fef81…` mit langsamer Mail-Mission (Ende 266,6 s) und einer Tab-Recovery in der Abgabe.
- **Ausgefallene Runs** (`run_08a1…`, `run_2c64…`, `run_3256…`): Plattform- oder Umgebungsfehler, nicht gewertet.
- **Varianz:** Im selben Paket schwankt C×T um Faktor 1,5. Einzelruns entscheiden nichts.

**Gewertete Runs aus Test-Runs:** `run_5ec3…` und `run_f0fa…` wurden als Test-Runs gestartet und sind jetzt gewertete Runs der Submissions `sub_7d5a7f3ba471725d` und `sub_9c28163b79f4a06e`, mit identischem Paket-Hash.
- Wer das Paket eingereicht hat, ist nicht dokumentiert.
- **Folge:** Die zuletzt bestandene Submission, die ins Finale geht, ist Version C.

### 3.2 Kostenstruktur (Version C, je Run) [gemessen]

| Block | Calls | Tokens je Call | Kosten je Run | Anteil |
|---|---|---|---|---|
| Navigation, ungecachter Input | 145–169 | ≈ 2.180–2.370 | $0.051–0.057 | ≈ 50 % |
| Navigation, gecachter Input | (dieselben) | ≈ 2.900–3.170 | $0.007–0.009 | ≈ 7 % |
| Navigation, Output | (dieselben) | ≈ 70 | $0.005–0.006 | ≈ 5 % |
| Extraktion, Input (praktisch ungecacht) | 92–104 | ≈ 1.420–1.530 | $0.021–0.024 | ≈ 22 % |
| Extraktion, Output | (dieselben) | ≈ 335 | $0.015–0.016 | ≈ 15 % |

**Cache:**
- Gecacht wird in Blöcken von 1.568 Tokens. Navigations-Calls treffen meist genau System + Tools (3.136).
- Extraktions-Präfixe (System + Schema ≈ 500–600 Tokens) liegen unter einem Block und werden nie gecacht.
- Die lokale Probe zeigte: Bei Prompts in Navigationsgröße (4–6k) wächst der gecachte Anteil *nicht* mit einer stabilen Historie (blieb bei 1.568–3.136). Bei identischen 9k-Präfixen erreichte er bis 7.840.
- Ein Cache-Umbau ist ohne neuen Beleg also wertlos.

**Ein Navigations-Call kostet ≈ $0.00044:** 2.300 × 0,15 + 3.000 × 0,016 + 70 × 0,47 (je $/M). Jeder vermiedene Schritt spart diesen Betrag plus ≈ 2–3 s.

### 3.3 Laufzeit und kritischer Pfad (Version C) [gemessen, wo nicht anders markiert]

| Abschnitt | Wert | Quelle |
|---|---|---|
| Brief | erster Call bei 1,8–4,6 s, Latenz 12,7–15,4 s (≈ 2.950 in / 622–686 out) | `llm-calls` von `run_ab2f…`/`run_6a0e…` (B, identischer Brief-Code); lokal 15,9 s |
| Spezifikation bis Anforderungen | ERP-Gruppen starten bei 103–104 s (`run_e0c6…`); Spezifikation fertig bei 115,7 s | Frühphase in der Sandbox sonst unsichtbar [unverifiziert]; lokal (`n123.log`): Anforderungen nach 57–61 s, Spezifikation 7 Schritte, darunter ein 16-s-Hänger und eine unnötige Registerextraktion |
| ERP, langsamste Gruppe | Ende 213–249 s; Missionsdauer ≈ 110–140 s | `mission_status`-Events |
| Mail | Ende 125–267 s; in `run_fef81…` war Mail die letzte Mission | `mission_status` |
| Übergabe Evidenz → erster Abgabeschritt | 3–12 s | `evidence_done` bis erste `submit`-Action |
| Abgabe | 72–90 s (Ausreißer 169 s); 5 Positionen à ≈ 7–10 s, TAN ≈ 8 s | Actions der Abgabe-Mission, `run_f0fa…` |

**Modellzeit gegenüber Wandzeit:**
- Summierte Call-Zeit pro C-Run 16:35–24:35 min bei 5–7 min Laufzeit, also im Mittel 3,1–4,8 parallele Calls.
- Laufen alle drei Abschnitte (Spezifikation → ERP → Abgabe) seriell, entscheidet nur der langsamste Pfad.

**Pro Schritt in der kritischen ERP-Mission** (`run_5ec3…`, langsamste Gruppe):
- Klickschritte 1,2–4 s.
- Nach dem Start einer Hintergrund-Extraktion trotzdem 6–17 s bis zum nächsten Schritt. Vermutete Ursache: Slot-Wartezeit und Last durch parallele Extraktionen (§3.5) [unverifiziert, die lokale Wartezeit wird nicht gemessen].
- Am Ende 4 `wait_for`-Schritte über 33 s, bis die letzte Extraktion eintraf.

### 3.4 Wiederholte und überflüssige Arbeit [gemessen, sichtbarer Logteil]

| Muster | Umfang pro Run (C) | Kosten/Zeit |
|---|---|---|
| `wait_for` in ERP-Missionen (Warten auf Hintergrundergebnisse) | 18–22 von 59–97 sichtbaren ERP-Schritten | ≈ 20 × $0.00044 ≈ $0.009 und Slot-Last; Bedingungen wie „text:Konditionssatz 2“ treten nie ein und laufen jeweils 8–10 s ins Timeout |
| Extraktion und Navigation als getrennte Schritte | 19–33 Extraktionsschritte in ERP | ≈ 1 Navigations-Call pro Extraktion |
| Mail-Mehrfachextraktion | 22–30 `mail_message` bei ≈ 19 relevanten | ≈ $0.002–0.004 |
| ERP-Gate und Suchaufbau pro neuem Tab | 3–5 Schritte × 4 Gruppen | ≈ 12–20 Calls |
| Hedge-Zwillinge | 3–8 Paare in 3 von 4 Runs, beide mit Status 200 und voller Buchung | ≈ $0.002–0.005 |
| `guard_overlays`-Logzeilen (Mail) | 58 von 256 behaltenen Zeilen (`run_5ec3…`) | Sichtverlust, keine Modellkosten |

**Notwendiger Umfang [Schätzung, public]:**
- **Extraktionen:**
  - ≈ 65 Seiten
  - **Aufschlüsselung:** Positionen 1–3, Q&A 6, Revisionsliste 1, Anforderungen 5, ERP je passendem Material ≈ 6–7 Sichten plus Suchlisten, Mail ≈ 7 Listenseiten plus ≈ 12 Nachrichten
  - **Beobachtet:** 92–104 Calls, also ≈ 1,5×.
- **Navigationsentscheidungen:**
  - **Untergrenze:** ≈ 55–65 (mit Bündelung über `extract_urls`)
  - **Beobachtet:** 145–169, also ≈ 2,5×.

### 3.5 Wartezeiten: Slots, Hedges, Retries, Recovery [gemessen]

**Slot-Sättigung** (Zeit mit ≥ 10 Calls gleichzeitig; Navigations-Calls, die bei ≥ 9 laufenden Calls starten):

| Version | Run | Zeit ≥ 10 Calls | mittlere Parallelität | Navigation startet bei ≥ 9 Calls |
|---|---|---|---|---|
| A | 6e07 / 56b5 | 20 s / 14 s | 2,5 / 2,7 | 12 / 3 |
| B | ab2f / 6a0e | 10 s / 6 s | 2,3 / 2,5 | 2 / 1 |
| C | 5ec3 / e0c6 / fef8 / f0fa | 14 / 39 / 32 / 62 s | 3,2 / 4,1 / 3,7 / 4,9 | 5 / 17 / 18 / 45 |

- Die Plattform zählt Calls erst, *nachdem* wir einen Slot haben. Die lokale Wartezeit vor dem Slot ist also unsichtbar [unverifiziert].
- Die Zunahme in C ist die Nebenwirkung der Hintergrund-Extraktion: Bis zu 5 Seiten pro `extract_urls` belegen gleichzeitig Slots.

**Latenzen (C, 4 Runs):**
- **Navigation:** p50 2,1 s, p90 4,1 s, p99 6,6 s, max 18 s (n = 584; nur 1 Call über 12 s).
- **Extraktion:** p50 8,2 s, p90 14,7 s, p99 21,7 s, max 26 s (n = 436; keiner über 30 s).
- Die Extraktionslatenz folgt ≈ 1,6 s + 21 ms × Output-Tokens. Lange Extraktionen sind also lang, weil sie viel ausgeben, nicht weil sie hängen.

**Hedges:**
- Die Schwelle für Tool-Calls liegt bei 12 s (`llm.py:27`). Calls mit langen Tool-Argumenten werden deshalb gedoppelt: `steps` mit ≈ 460 Tokens oder `extract_urls` mit vielen URLs.
- Im Mittel 4,75 Hedges pro Run (Spanne 1–8). Wie oft der Zwilling gewinnt, ist für C nicht erhalten (Log-Retention); in `run_56b5…` (A) gewann er 2 von 10.
- In A gab es echte Hänger: lokal 7 Requests mit 60–150 s, Grund für das Hedging. In C gab es keine.

**Retries:** 1 `llm_error` in `run_5ec3…`, sonst keine. Alle Calls mit Status 200.

**Recovery:** `run_fef81…`: Tab-Recovery des Vergabeportals bei 401 s, dadurch Abgabe 169 s statt ≈ 95 s. Das ist die Ausnahme; Umgebungsausfälle siehe A.

### 3.6 Beobachtbarkeit

- **Retention:** In drei geprüften Runs (`run_5ec3…`, `run_e0c6…`, `run_6a0e…`) sind exakt 256 stdout-Zeilen erhalten (`logs.jsonl`). Sichtbar ist je nach Run erst ab t ≈ 95–166 s.
- **Rauschen:** `guard_overlays` (58 Zeilen, Mail-Gates bei jedem Helfer-Tab, `site.py:237`) und 32 einzelne `trace`-Zeilen (`orchestrator.py:569`).
- **Es fehlen:**
  - die Zeitlinie für Brief, Tender und Spezifikation
  - die Slot-Wartezeit
  - die Browserzeit pro Schritt
  - die Zusammensetzung des Prompts (Historie, Beobachtung, Ergebnisse)

### 3.7 Lokale und Sandbox-Messungen

Das lokale Netz war während der Messungen instabil (Proxy-Timeouts, `ERR_NETWORK_CHANGED`).
- Spezifikation bis Anforderungen: lokal ≈ 45 s nach dem Brief, in der Sandbox ≈ 85 s.
- Lokale E2E-Läufe taugen für Funktion und Ablauf (Oracle-Score), **nicht** für Zeitvergleiche.

### 3.8 Fehlende Messdaten (Entscheidungen hängen daran)

| Messung | entscheidet über | Beschaffung |
|---|---|---|
| Zeitlinie 0–100 s in der Sandbox (Brief, Tender, Spezifikation, erste ERP-Schritte) | X-6 (Spezifikations-Gate, ERP-Vorbereitung) | Roadmap O-1 (`run_summary`) |
| Lokale Slot-Wartezeit je Lane | P-4 (Lanes) | O-1 |
| Prompt-Zusammensetzung je Navigations-Call | X-3 (Historie verdichten) | O-1 |
| Browserzeit (`read_url`, `goto`, `settle`) | Grenzen von X-1/X-2 | O-1 |
| Echte Hänger-Rate unter aktueller Last | Hedge-Schwellen (P-1) | O-1 plus `bench.py`-Paaranalyse |
| Werden verlorene Zwillinge immer berechnet? (`run_5ec3…`: 7 Hedges, 0 erkannte Paare) | Kosten von P-1 | `bench.py` über mehrere Runs |
| Extraktionsgenauigkeit bei gekürzten Schemata | X-4 | G-3 Offline-Replay |
| Verhalten auf anderen Layouts und Verteilungen | alle Schritte | G-3 Perturbationen; privat grundsätzlich ungemessen |

## 4. Alternativenvergleich

Bewertung relativ zu Version C. ΔC×T [Schätzung]: negativ ist besser. Die Spalte „Privat“ bewertet die Robustheit auf unbekannten Layouts.

| # | Ansatz | Problem, das er löst | Calls weg / neu | ΔKosten | ΔZeit | Risiko | Privat | Aufwand | Urteil |
|---|---|---|---|---|---|---|---|---|---|
| A1 | Baseline C unverändert | – | – | 0 | 0 | bekannt | Lücken aus §2.4 | 0 | Referenz |
| A2 | **Gezielte Optimierung** (Roadmap) | Warteschleifen, Slot-Konflikte, Fixkosten, Blindheit | −40…−70 Navigation / +1 Brief | −15…−30 % | −20…−75 s | gering bis mittel, schrittweise | gleich oder besser (Gates) | 2–4 Tage | **empfohlen** |
| A3 | Planner/Executor (Plan pro Mission, Ausführung ohne Modell) | viele kleine Entscheidungen | −30…−50 % Navigation / +1 Plan je Mission, Re-Plan bei Abweichung | −10…−25 % | −10…−40 s | hoch: Pläne veralten auf unbekannten Seiten, Ausführung braucht Grounding | schlechter | 5–8 Tage | verworfen |
| A4 | Tiefere Hierarchie (z. B. Mission je Material) | Parallelität im ERP | ± / +Missions-Overhead (Gate, Suche je Tab) | +5…+15 % | ≈ 0, die längste Gruppe hat ein Material | mittel | gleich | 1–2 Tage | verworfen |
| A5 | Weniger, größere Entscheidungen: mehrere Tools pro Antwort | Schrittzahl | −20…−30 % / – | −8…−12 % | −15…−30 s | mittel: unklar, ob der Qwen-Endpoint mehrere Tool-Calls zuverlässig liefert [unverifiziert] | gleich | 1 Tag | ersetzt durch A6/X-1 |
| A6 | **Begrenzte Aktionspakete** mit Abbruch bei Zustandsänderung (`steps`, `extract … then_click`) | Schrittzahl, Latenz | −20…−35 Navigation / – | −8…−12 % | −10…−25 s | gering: bestehende Mechanik (`_rematch`, Stopp beim ersten Fehler) | gleich | 0,5–1 Tag | **ja** (X-1) |
| A7 | Bedarfsgesteuerte Evidenz (nur was die Regeln brauchen) | überflüssige Extraktion | −5…−10 Extraktionen | −2…−4 % | ≈ 0 (nicht kritisch) | mittel: unbekannte Themen werden übersehen | schlechter | 1 Tag | nur in bestehender Form (Checklisten); keine Ausweitung |
| A8 | Gebündelte Extraktion (mehrere Seiten eines Materials in einem Call) | wiederholte Felder, Overhead | −30 % Extraktions-Calls / größere Calls | −3…−6 % | ±, weniger Parallelität | mittel: Felder werden zwischen Sichten verwechselt (Lager frei vs. Prüfbestand) | schlechter | 1 Tag | verworfen |
| A9 | **Kompakter Zustand:** Historie verdichten, Links aus extrahierten Seiten anbieten | Prompt-Größe, Klicks | −5…−15 % Input je Call; −10 Klicks | −5…−10 % | −5…−10 s | mittel: Schleifen, wenn Kontext fehlt | gleich | je 0,5 Tag | **Experiment** (X-2, X-3) |
| A10 | Mehr Code-Hilfe (Paginierung, Formulare, Links listen) im Fair-Play-Rahmen | Schrittzahl | wie A6/A9 | wie A6/A9 | wie A6/A9 | Fair-Play-Grenze: Code darf nur auf modellgewählten Seiten arbeiten | gleich | – | über X-1/X-2 abgedeckt |
| A11 | Clef für Navigation oder Relevanz | Latenz | ersetzt höchstens Link-Klicks; fügt Calls hinzu, wo Argumente nötig sind | +10…+45 % (zentral) | −0…−10 s | hoch: keine Navigations-Benchmarks, Kalibrierung unveröffentlicht | schlechter | 2–3 Tage | verworfen |
| A12 | Frontier-Modell für schwere Schritte, adaptive Eskalation | Qualität | +teure Calls | +100…+1000 % | ± | gering | eventuell besser | 1 Tag | nicht für Tempo; nur als Qualitätsoption bei nachgewiesenem Bedarf |
| A13 | **Scheduling:** Lanes, Brief-Split, Formular-Vorbereitung, ERP-Vorbereitung | Slot-Konflikte, Fixzeiten | +1 Brief-Call | ≈ 0 | −15…−40 s | gering bis mittel | gleich | je 0,5 Tag | **ja** (P-2, P-4, X-5, X-6) |
| A14 | Abgabe vereinfachen: deterministischer Formularfüller | Abgabezeit | −15 Calls | −6 % | −30 s | **Fair Play:** fester Klickpfad auf unbekanntem Konfigurator ist verboten und versagt | schlechter | – | verworfen; Paket-Ansatz (`steps`) genügt |
| A15 | Fakten direkt aus dem Navigationsmodell (keine separaten Extraktionscalls) | doppelter Seiten-Input | −95 Extraktionen / Navigation mit ≈ 335 Output-Tokens | −10…−15 % | **+60…+150 s**: Output liegt auf dem kritischen Pfad, keine Parallelität | mittel | gleich | 2 Tage | verworfen |
| A16 | Verifier / Self-Consistency (doppelte Extraktion) | Extraktionsfehler | +100 % der betroffenen Extraktionen | +20…+40 % | ± | – | eventuell besser | 1 Tag | nicht für Tempo; nur gezielt (gibt es bereits als `recheck_no_bid`) |
| A17 | **Robustheits-Gates** (Tab-Isolation, Suche auf anderer Site, Perturbationen) | private Verteilung | 0 im Normalfall / Recovery nur bei Lücke | 0 (public) | 0 (public) | gering | **besser** | 1–1,5 Tage | **ja, vor allen Tempo-Schritten** |
| A18 | Cache-Layout (stabile Historie, Blockausrichtung) | ungecachter Input | – | 0 laut Probe | 0 | – | – | – | verworfen bis neuer Beleg |

### 4.1 Kernbegründungen der wichtigsten Urteile

**A3 Planner/Executor:**
- Unsere Missionen sind kurz (15–30 Schritte) und reaktiv: Links und Gates zeigen sich erst auf der Seite.
- Ein Plan spart nur, wenn er stimmt. Auf unbekannten Layouts muss der Executor jeden Plan-Schritt an ein Element binden. Das braucht wieder ein Modell oder unscharfes Matching (Fehlerquelle).
- Ein Executor ohne Modell grenzt zudem an den verbotenen „festen Klickpfad“.
- Der Kern des Vorteils, mehrere Aktionen pro Entscheidung, lässt sich mit A6 lokal holen, ohne Umbau.

**A11 Clef:**
- Ein Navigations-Call kostet hier ≈ $0.00044, weil 3.136 Tokens gecacht sind. Clef kostet $0.24/M ohne Cache-Rabatt.
- Kosten-Break-even: Zustand < Akzeptanzrate × ≈ 1.830 Tokens. Real liegen die Zustände bei ≥ 2.000 Tokens.
- Die meisten Schritte brauchen Argumente (URL-Listen, Formularwerte), die ein Entscheidungsmodell nicht liefern kann. Es käme also ein Call *hinzu*.

**A15:**
- Verlagert die Extraktion auf den kritischen Pfad, denn Output-Tokens sind seriell: 21 ms/Token.
- Bricht die Parallelität, die C gerade erst gewonnen hat.

**A14:** scheitert an Fair Play und am privaten Konfigurator, der anders aussieht.

### 4.2 Wechselwirkungen und Verschiebung des kritischen Pfads

- **Hintergrund-Extraktion hat Slot-Druck und Warteschleifen erzeugt.**
  - P-3 (Warten auf Ergebnisse statt `wait_for`-Schleifen) und P-4 (Lanes) sind die direkte Korrektur.
  - Ohne P-3 verbraucht A6 (`then_click`) zusätzliche Slots schneller. Daher kommt P-3 vor X-1.
- **Verschiebung des kritischen Pfads:**
  - Wird ERP um 20–30 s schneller, wird in manchen Runs Mail kritisch (Ende bis 267 s, `run_fef81…`). X-1/X-2 gelten deshalb für ERP *und* Mail.
  - Danach dominieren Spezifikations-Gate (≈ 85 s) und Abgabe (≈ 80 s). Deshalb ist zuerst die Messung O-1 nötig, sonst fehlt für X-6 die Grundlage.
- **Effekte überlappen:** P-3 und X-1 reduzieren verschiedene Call-Arten (Warten gegenüber Extraktion + Navigation) und addieren sich weitgehend. X-3 (Historie) wirkt prozentual auf die *verbleibenden* Calls und wird mit jedem anderen Schritt kleiner.
- **Hedging:** P-1 reduziert Zwillinge und damit auch Slot-Last, was P-4 hilft.
- **Gates:** G-2 kostet auf public nichts, verlängert aber privat bei Lücken die Evidenzphase um bis zu 150 s. Das ist bewusst gewollt (Qualität vor Tempo).

## 5. Quantifizierung [Schätzung]

**Basis:** Median Version C: T = 326 s, C = $0.1038, C×T = 33,2.

**Annahmen:**
- Ein vermiedener Navigations-Call spart $0.00044 und ≈ 2–3 s, falls er auf dem kritischen Pfad liegt.
- ERP ist kritisch, solange ERP-Ende > Mail-Ende; Mail ist es in ≈ 1 von 4 Runs.

### 5.1 Einzelschritte (zentral, mit Spanne)

| Schritt | ΔKosten | ΔZeit | Grundlage |
|---|---|---|---|
| P-1 Hedge-Schwelle je Mission | −$0.003 (−0,001…−0,005) | 0 | 1–8 Zwillinge à ≈ $0.0006 |
| P-2 Brief-Split | +$0.0005 | −11 s (−8…−13) | Brief-Output 680 → ≈ 80 Tokens für den Kern |
| P-3 Warten statt `wait_for`-Schleife | −$0.008 (−0,005…−0,011) | −5 s (0…−15) | 18–22 `wait_for` pro Run; Timeouts von 8–10 s entfallen |
| P-4 Lanes Navigation/Extraktion | 0 | −5 s (0…−20) | bis zu 45 Navigations-Calls unter Volllast; lokale Wartezeit ungemessen → Mess-Gate |
| X-1 `extract … then_click/then_open` | −$0.008 (−0,004…−0,011) | −10 s (−5…−20) | 19–33 Extraktionsschritte in ERP; ≈ 50 % zusammenlegbar |
| X-2 Links aus extrahierten Seiten | −$0.003 | −5 s | 17–33 Klicks in ERP; Unter-Sichten liegen auf extrahierten Seiten |
| X-3 Historie verdichten | −$0.005 (0…−0,010) | 0 | Historie ≈ 30–50 % des ungecachten Navigations-Inputs bei langen Missionen [unverifiziert → O-1] |
| X-4 Schema kürzen | −$0.003 | −3 s | `description` und `body` werden von keiner Regel gelesen (`rules/resolve.py`) |
| X-5 Formular vorbereiten | 0 | −10 s (0…−14) | Abgabe-Schritte 1–5 dauern 8–15 s |
| X-6 ERP-Vorbereitung | ±0 | 0…−15 s | erste 3–5 ERP-Schritte pro Tab; abhängig von O-1 |

### 5.2 Kombinationen

| Szenario | T | Kosten | C×T | Annahmen |
|---|---|---|---|---|
| Basis (Median C) | 326 s | $0.1038 | 33,2 | 4 Runs |
| Nach Phase 2 (P-1…P-4), pessimistisch | 318 s | $0.0998 | 31,7 (−4 %) | P-4 bringt nichts, P-3 nur −10 Calls |
| Nach Phase 2, zentral | 305 s | $0.0933 | 28,5 (−14 %) | Tabelle 5.1 |
| Nach Phase 2, optimistisch | 286 s | $0.088 | 25,2 (−24 %) | Slot-Wartezeit war groß |
| Nach Phase 3, zentral (erwartungsgewichtet, ≈ 60 % der Experimente übernommen) | ≈ 285 s | ≈ $0.081 | **≈ 23 (−30 %)** | ERP bleibt teilweise kritisch; Mail wird es öfter |
| Nach Phase 3, optimistisch (alle übernommen) | ≈ 255 s | ≈ $0.068 | ≈ 17 (−48 %) | |
| Nach Phase 3, pessimistisch (keines übernommen) | ≈ 318 s | ≈ $0.098 | ≈ 31 (−6 %) | |

Die Streuung zwischen Runs (Faktor 1,5) ist größer als jeder Einzeleffekt. Ohne gepaarte Wiederholungen ist keine dieser Zahlen nachweisbar (Validierung: `docs/13` §4).

### 5.3 Break-even-Bedingungen

**Hedging:** Ein Zwilling kostet einen vollen Call (c ≈ $0.0006 = 0,58 % von C). Er lohnt sich, wenn

    s · Δ · k / T  ≥  c / C

- s = Anteil der gehedgten Calls, die sonst bis zum Timeout hängen
- Δ = gesparte Sekunden: 60 − 12 = 48 s für Navigation
- k = Wahrscheinlichkeit, auf dem kritischen Pfad zu liegen, ≈ 0,5

**Ergebnis:** s ≥ 0,0058 · 326 / (48 · 0,5) ≈ **0,08**.
- Heute hängt praktisch keiner der gehedgten Calls (alle gesund, nur lang), also s ≈ 0.
- Hedging lohnt nur mit Schwellen, die gesunde lange Calls nicht treffen (P-1).

**Lanes:** Kosten 0. Lohnt, wenn die lokale Slot-Wartezeit für Navigation > 0 ist *und* höhere Parallelität die Latenz pro Call nicht erhöht.
- **Entscheidung:** Navigations-Slot-Wartezeit −50 %, Navigations-p50 nicht über +10 %.
- Teamlimit: 32 Slots über alle Runs; 16 pro Run lassen Platz für einen zweiten Run.

**Recovery auf anderer Site (G-2):**
- **Kosten nur bei Lücken:** ≈ $0.005–0.01 und ≤ 150 s.
- **Gewinn bei Erfolg:** typischerweise 5 Punkte (Rabatt) bis ≈ 90 Punkte (kein Material → `no_bid`).
- Qualität ist lexikografisch vorrangig, deshalb lohnt es immer, solange die Abgabe rechtzeitig bleibt. Recovery nur, wenn ≥ 120 s bis zum Evidenzende bleiben.

**Router oder Verifier mit kleinerem Modell:** Er ersetzt den großen Call nur, wenn seine Entscheidung *ohne* den großen Call ausführbar ist.
- In unserem Loop tragen ≈ 80 % der Schritte Argumente: Refs, URL-Listen, Formularwerte.
- Ein Router *ergänzt* dann den großen Call. Kosten-Break-even für Clef: Zustand < Akzeptanzrate × 1.830 Tokens.
- Ein Verifier verdoppelt die betroffenen Calls. Er lohnt nur, wenn p(Fehler) × Punktverlust die Qualitätsdifferenz zur nächsten Teamplatzierung bedroht. Kein Tempo-Instrument.

**Parallele Requests** (spekulativ zwei Varianten): kosten 2×. Lohnen nur bei Hängern (siehe Hedging). Nicht empfohlen.

### 5.4 Messungen, die Entscheidungen kippen würden

| Wenn … | dann … |
|---|---|
| O-1 zeigt Navigations-Slot-Wartezeit < 5 s pro Run | P-4 entfällt (Lanes bleiben aus) |
| O-1 zeigt Spezifikation bis Anforderungen ≤ 45 s nach dem Brief | X-6 entfällt; Fokus auf ERP/Mail |
| O-1 zeigt Historie < 25 % der Prompt-Zeichen | X-3 entfällt |
| Paarvergleich zeigt bei X-1 Schleifen oder falsche Klicks (mehr blockierte Wiederholungen) | X-1 aus |
| Offline-Replay zeigt < 100 % Feldübereinstimmung bei gekürztem Schema | X-4 für diese Art aus |
| Hängerrate steigt wieder (Plattformlast) | P-1-Schwellen nur für lange Calls anheben, Hedging nie ganz entfernen |
| Perturbations-Tests (G-3) scheitern am Serializer | erst dort reparieren, Tempo-Schritte zurückstellen |

## 6. Architekturentscheidung

**Gewählt:** gezielte Optimierung der bestehenden Architektur (A2), in der Reihenfolge
1. Messbarkeit (O)
2. Robustheits-Gates (G)
3. risikoarme Tempo-Schritte (P)
4. Experimente mit festem Entscheidungsablauf (X)

**Begründung gegen die stärksten Alternativen:**
- **Gegen Planner/Executor (A3):** Der Vorteil (weniger Entscheidungen) lässt sich mit Aktionspaketen (A6) lokal und reversibel erreichen. Die Kosten des Umbaus und das Generalisierungsrisiko auf unbekannten Layouts sind hoch. Der Nutzen ist nicht belegt.
- **Gegen Clef und Mischmodelle (A11):** Ein Navigations-Call ist hier dank Cache billiger als ein Clef-Call. Die meisten Schritte brauchen Argumente. Eine Messung bisher unbekannter Clef-Latenz würde nur Zeit sparen, nicht Kosten.
- **Gegen Fakten aus dem Navigationsmodell (A15):** Das verlängert den kritischen Pfad deutlich.
- **Gegen „gar nichts tun“:**
  - Platz 1 auf public ist erreicht. Für das private Finale ist aber nicht Tempo entscheidend, sondern Robustheit (G-1, G-2). Und ohne Messbarkeit (O-1) bleiben weitere Tempo-Schritte blind.
  - Die Tempo-Schritte P-1…P-3 beseitigen Nebenwirkungen der letzten Änderung (Warteschleifen, Slot-Druck, Hedge-Doppelungen). Sie machen das System eher stabiler als riskanter.

**Bewusst nicht verfolgt:** A3, A4, A8, A11, A14, A15, A16 (als Tempo-Instrument), A18 sowie eine Anhebung des Gesamt-Semaphors über 16.

## 7. Anhang: wie die Zahlen entstanden sind

**Run-Metadaten** (offizielle Zeit, Kosten, Qualität):

    curl -H "x-api-key: $(atira token)" -A rfq-analysis https://ehl-challenge.atira.ai/v1/runs/<run_id>

**Modell-Calls:**

    curl ... /v1/runs/<run_id>/llm-calls?limit=2000

- Klassifikation: Navigation, wenn `input_tokens + cache_read_tokens ≥ 3300`.
- Sättigung: Zeitintegral über gleichzeitig offene Calls.
- Hedge-Paare: gleiche Prompt-Länge, zweiter Start 9–45 s nach dem ersten, vor dessen Ende.

**Logs:**

    curl ... /v1/runs/<run_id>/logs.jsonl

- stdout-Zeilen als JSON parsen.
- Missionsende über `mission_status`, Evidenzende über `evidence_done`, Abgabe über `submit_phase_done` bzw. Actions mit `mission` = `submit@…`.

Diese Auswertungen sollen künftig reproduzierbar über `dev/eval/bench.py` laufen (Roadmap O-2).

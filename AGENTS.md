# AGENTS.md — isar-labs · Atira EHL Challenge „From RFQ to bid"

Dieses Dokument ist die verbindliche Arbeitsanweisung für alle Coding-Agents (Codex, Claude Code, Cursor) und Menschen
im Team. Lies es vollständig, bevor du Code schreibst. Details stehen in `docs/` (Index unten); bei Widerspruch gilt
die Reihenfolge: **Challenge-Brief der Plattform > dieses Dokument > docs/**.

## 1. Mission in einem Absatz

Wir bauen ein Agentensystem, das in einer Sandbox (20 min, bis zu 4 Chromium-Browser über CDP, nur Plattform +
4 Websites erreichbar) ein öffentliches Tender beantwortet: Vergabeportal (Ausschreibung, Bieterfragen, Konfigurator),
Lieferantenportal (Lastenheft/Spezifikation), ERP (Material, Bestand, Konditionen) und Webmail (Freigaben, TAN).
Am Ende wird **ein** signiertes Angebot im Konfigurator des Vergabeportals abgegeben. Gewertet wird:
**eligible (richtige Tender-Referenz) → Qualität 0–100 → Kosten × Zeit**. Gewinner: zuerst **privater Datensatz**
(andere Websites, anderes Layout, andere Navigation, Informationen anders verteilt!), dann public, dann Eleganz.

➡️ Wir optimieren für **Generalität und Korrektheit**, nicht für das öffentliche Szenario.

## 2. Nicht verhandelbare Regeln (Fair Play — Verstoß = Disqualifikation)

1. **Keine hardcodierten Szenario-Werte** in `agent/` — weder Code, Config, Prompts, Kommentare, Defaults, Fallbacks
   noch Tests: keine Tender-Referenzen, Materialnummern, Preise, Rabatte, Keys (`VP-…`, `TZ-…`, `K-…`, `RP-…`, `PB-…`,
   `MET-…`, `FX-…`, `ER-…`, `GR-…`), Logins, TANs, URLs-Pfade der Public-Sites. Werte aus dem **Brief** (den jeder Lauf
   bekommt) dürfen zur Laufzeit gelesen werden. Die **Regeln** (Rechenlogik) dürfen wir implementieren.
2. **Ein LLM entscheidet die Navigation**: wohin klicken, was suchen, welches Dokument relevant ist. Code darf helfen
   (Seite extrahieren, ausgewähltes Dokument parsen, vom Modell gewählte Ergebnisliste paginieren, rechnen, validieren,
   retry, Formular füllen). **Verboten**: fester Klickpfad, Crawler der alles einsammelt und am Ende einem Modell gibt.
3. Keine eigenen Browser in der Sandbox starten (nur `CDP_URLS`), keine lokalen Modelle, kein Netzwerk außer
   Plattform/Websites/Proxy, alle LLM-Calls über den Proxy (`*_BASE_URL`-Variablen).
4. Keine Secrets im Repo. Eigene API-Keys nur als Team-Secret auf der Plattform.
5. Nichts im Package, was den AI-Reviewer „steuern“ soll (Kommentare an den Reviewer zählen gegen uns).
6. `submit`/`cancel` auf der Plattform **nur nach ausdrücklicher Freigabe eines Menschen**. Test-Runs sind frei.

Details: `docs/01-challenge.md` §Fair Play.

## 3. Repo-Layout (Zielbild)

```
agent/                 # == eingereichtes Package (agent.json liegt HIER). Nur generischer Code!
  agent.json           # {"setup":["uv","sync"],"run":["uv","run","--no-sync","python","main.py"],"browsers":4,"models":["qwen"]}
  pyproject.toml uv.lock main.py
  rfq/
    contract.py        # Run laden (API / CHALLENGE_SCOUTING), Deadline, Logging
    browser/           # session.py observe.py observe.js actions.py guards.py documents.py
    llm/               # client.py (Qwen/Clef/BYO), loop.py (Tool-Loop), prompts/*.md
    facts.py           # typisierte Evidenz mit Provenienz (pydantic)
    agents/            # orchestrator.py explorer.py missions.py
    rules/             # parse.py (Zahl/Datum/Einheit) resolve.py pricing.py validate.py
    submit/            # configurator.py tan.py
  tests/               # NUR synthetische Testdaten
dev/                   # NICHT eingereicht: Scout-Tools, HTML-Fixtures der Public-Sites, Oracle, Eval-Harness
docs/                  # Wissensbasis (siehe Index)
```

Einreichen/Testen immer aus `agent/`: `atira test-run ./agent` (CLI packt den Ordner ohne `.gitignore`-Dateien).
Alles mit Public-Werten (Fixtures, Oracle, Golden Answers) liegt ausschließlich in `dev/`.

## 4. Architektur (Kurzfassung — Details `docs/02-architecture.md`)

```
Brief ──► TaskContext (LLM-Extraktion: Referenz, Stichtag, Firma, USt-Id, Direktor-Mailbox, autorisierte Absender)
            │
            ▼
   Orchestrator (Python, asyncio, Deadline-Wächter, Blackboard)
     ├─ Explorer-Agent × 4  (je 1 Browser, LLM-Tool-Loop, Missionen = Informationsbedarf, NICHT Site-Skripte)
     │     Tools: observe · click · fill · select · check · set_range · expand · goto_link · back · read_document
     │            · paginate(ref) · search(ref,query) · report_fact(...) · ask_blackboard(...) · done
     │     Guards (Code): Cookie-Wall, Overlays/Umfragen schließen, Session-Relogin, 5xx/„Fehler“-Retry,
     │                    Zugangs-Gates (Slider+Checkbox+Belegnummer), Popups/Fenster, iframes
     ├─ Blackboard: typisierte Facts mit Provenienz (Quelle, Datum, Referenz, Absender, Rohtext)
     ├─ Resolver + Pricing (reines Python, Decimal, ROUND_HALF_UP) ─► Answer-JSON + Rechenweg-Trace
     └─ Submit-Agent: Konfigurator ausfüllen (LLM wählt Felder, Code verifiziert jeden Schritt),
                      TAN anfordern → neueste TAN aus Mail → confirm()-Dialog akzeptieren
```

Kernprinzipien:
- **LLM navigiert und interpretiert, Python rechnet.** Keine Arithmetik im LLM.
- **Observation = eigener DOM-Serializer** (a11y-basiert, alle Frames, Tooltips + geschlossene `<details>` sichtbar,
  `aria-hidden`/`display:none`-Köder raus, Canvas/`role=img` über `aria-label`, Zero-Width-Zeichen entfernt).
  Screenshots nur als Fallback für Vision (Qwen ist multimodal).
- **Jede Zahl hat Provenienz** (Quelle + Rohtext + Locale). Resolver wendet „neuester datierter Eintrag ≤ Stichtag,
  exakte Referenz, autorisierter Absender“ deterministisch an.
- **Immer eine Antwort abgeben.** Deadline-Wächter: spätestens bei `expires_at − 4 min` mit bestem Stand signieren.
  Leere Felder = 0 Punkte, falsche Felder = 0 Punkte → immer beste Schätzung eintragen, Positionen nie auslassen,
  außer sie lassen sich technisch nicht konfigurieren.

## 5. Workstreams & Branches (Details + Schnittstellen: `docs/07-workstreams.md`)

| Branch | Owner-Rolle | Liefert |
| --- | --- | --- |
| `feat/agent-orchestrator` | Orchestrierung & LLM | `contract.py`, `llm/`, `agents/orchestrator.py`, `explorer.py`, Deadline, Logging, Package-Layout `agent/` |
| `feat/browser-tools` | Browser-Robustheit | `browser/*` (Serializer, Actions, Guards, Documents), Fixture-Tests gegen `dev/fixtures` |
| `feat/tender-specification` | Vergabe + Spezifikation | Missionen + Fact-Schemas für Tender, LV, Bieterfragen, Spezifikation/Revisionen, Nachträge |
| `feat/erp-mail-evidence` | ERP + Mail | Missionen + Fact-Schemas für Material/Bestand/Konditionen, Mail-Freigaben, Keys |
| `feat/pricing-submission` | Rechnen + Abgabe | `rules/*` (Parser, Resolver, Pricing, Validate), `submit/*` (Konfigurator, TAN) |

Schnittstellen sind in `agent/rfq/facts.py` definiert (Owner: pricing-submission, Änderungen per PR mit Review
durch orchestrator). Merge-Reihenfolge: orchestrator (Layout) → browser-tools → facts.py → Rest.

## 6. Arbeitsweise

- Python 3.12, `uv`, asyncio, Playwright (async), `pydantic` v2, `openai` SDK (für Qwen, OpenAI-kompatibel).
  Keine weiteren schweren Abhängigkeiten ohne Absprache (Setup max. 5 min, Package ≤ 20 MB).
- Geld & Mengen: **`decimal.Decimal`**, niemals `float`. Rundung `ROUND_HALF_UP` exakt dort, wo die Regeln es sagen.
- Zahlen/Datum immer über `rules/parse.py` (Locale-Tags `(de-DE)`/`(en-US)`, `(TT/MM)`/`(MM/TT)`, Zeitzonen).
- Logging: jede LLM-Antwort, jede Aktion, jeder Fact als JSON-Zeile auf stdout (die Plattform zeigt stdout im Run-Log).
  Keine Secrets loggen. Format: `{"t":…,"agent":…,"event":…,…}`.
- Prompts liegen als Markdown in `agent/rfq/llm/prompts/`, sind **site-agnostisch** und enthalten nur Regeln,
  Glossar und Vorgehen — nie Public-Werte.
- Kleine PRs, je Branch eigener Bereich; `facts.py` und Prompts nur per Review ändern.
- Commits: aussagekräftig, Englisch, Imperativ.

## 7. Testen (Details: `docs/06-testing.md`)

1. `cd agent && uv run pytest -q` — Unit-Tests (Parser, Pricing, Resolver, Serializer) mit synthetischen Daten.
2. `uv run pytest dev/tests -q` — Fixture-Tests gegen gespeichertes Public-HTML (Fallen-Regression).
3. Lokal End-to-End: Terminal 1 `atira models proxy`; Terminal 2 `eval "$(atira scout --env)" && cd agent && uv run python main.py`
   (lokal wird nichts gewertet; Abgabe mit Scouting-Logins wird „acknowledged“, zählt nie).
4. `uv run python dev/eval/score.py <answer.json>` — Feldweise Bewertung gegen `dev/oracle` (Punkteschema der Challenge).
5. Sandbox: `atira test-run ./agent` → Punkte je Feld; Debug über `atira mcp` (`diagnose_run`, `run_logs`, `run_screenshot`).
6. Robustheit: Perturbations-Tests (umbenannte Labels, andere Reihenfolge, andere Locale) — Ziel privater Datensatz.

**Definition of Done** für jedes Feature: Unit-Test grün · keine Public-Werte in `agent/` (`dev/check_no_leaks.py`) ·
läuft lokal gegen Scouting-Sites · Logs nachvollziehbar.

## 8. Doku-Index

| Datei | Inhalt |
| --- | --- |
| `docs/01-challenge.md` | Plattform, Run-Contract, Env-Variablen, Scoring, Fair Play, Answer-Schema, Abgabe-Ablauf |
| `docs/02-architecture.md` | Zielarchitektur, Datenfluss, Phasen, Zeitbudget, Modellstrategie |
| `docs/03-site-recon.md` | Was wir auf den 4 Public-Sites gefunden haben: jede Falle, generalisiert |
| `docs/04-business-rules.md` | Exakte Rechenregeln (alle 29), Formeln, Rundung, Reihenfolge, Edge Cases |
| `docs/05-browser-playbook.md` | Playwright-Regeln, Serializer-Spezifikation, Guards, Dialoge, Popups, Sessions |
| `docs/06-testing.md` | Teststrategie, Eval-Harness, Test-Runs, Debugging über MCP |
| `docs/07-workstreams.md` | Aufgaben je Person/Branch, Schnittstellen, Meilensteine |
| `docs/08-pitfalls.md` | Häufige Fehler (eigene Funde + Literatur) und Features, die wir übernehmen |
| `docs/09-glossary-de.md` | Deutsch-Glossar (für Runtime-Prompts geeignet, keine Szenario-Werte) |
| `docs/10-risks-error-handling.md` | Fehlerbehandlung in Schichten, Deadline, LLM-/Browser-Edge-Cases, Review-Sicherheit |
| `docs/prompts/*.md` | Kickoff-Prompts je Workstream zum Einfügen in Codex/Claude |
| `docs/reference/` | Brief, Answer-Schema, Plattform-Docs (Snapshot) |

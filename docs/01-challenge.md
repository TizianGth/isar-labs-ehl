# 01 · Die Challenge auf einen Blick

Quellen: https://ehl-challenge.atira.ai/challenges/rfq.md, /start.md, /docs/*.md (Snapshot: `docs/reference/`).
Stand: 2026-10-10, `challenge_version` 6.

## Aufgabe

Deutscher Hydraulik-Hersteller (Bieter) gibt **ein** netto-EUR-Angebot auf eine öffentliche Ausschreibung für
Hydraulikzylinder ab. Daten verteilt auf 4 Websites:

| siteId (public) | Rolle | Typische Inhalte |
| --- | --- | --- |
| `tender-portal` | Vergabeportal | Bekanntmachung, Leistungsverzeichnis (LV), Bieterfragen/Q&A, Konfigurator, Abgabe |
| `customer-portal` | Lieferantenportal des Auftraggebers | Lastenheft/Spezifikation mit Revisionen |
| `erp` | Eigenes ERP (SAP-artig) | Material (MM03), Bestand (MMBE), Konditionen (VK13) |
| `mail` | Webmail „Werkpost“ | Rabattfreigabe Vertriebsleitung, Fachfreigaben, Signatur-TAN |

⚠️ Privater Datensatz: **andere Software, Layout, Navigation, Site-Namen, andere Inhalte, Informationen anders
verteilt.** `siteId`s können anders heißen. Nichts an Site-Namen/URLs/Menüpunkten festmachen.

## Ablauf eines Runs

1. Setup (`agent.json.setup`, max 5 min, mit Netz, ohne Secrets) — zählt nicht.
2. Modell-Warm-up (nur Modelle in `"models"`) — zählt nicht.
3. `run` startet → **Uhr läuft (Scored: 20 min, Test-Run: bis 2 h)**.
4. `GET $CHALLENGE_API_URL/v1/runs/current` mit `x-api-key: $CHALLENGE_RUN_TOKEN` (+ eigener User-Agent, sonst 403
   „Error 1010“) → `brief`, `access[]` (siteId, url, credentials), `submission` (`kind:"site"`, siteId, url),
   `answer_schema`, `started_at`, `expires_at` (epoch ms), Kosten bisher, `llm_*`.
5. Agent arbeitet, gibt im Konfigurator des Vergabeportals ab. Abgabe beendet den Run (Logins sterben, 10 s bis Exit).

Lokal: kein Run-Token. `eval "$(atira scout --env)"` setzt `CHALLENGE_SCOUTING` (JSON mit brief/access/submission/
answer_schema), `CHALLENGE_SITE_ACCESS`, `QWEN_BASE_URL`/`QWEN_API_KEY` (→ `atira models proxy` auf 127.0.0.1:8765),
`CLEF_*`. Die Scouting-Logins sind **für alle Teams geteilt** → Zustand (Konfigurator-Schritt, TAN-Mails anderer
Teams, „Teilnahme registriert“) kann verschmutzt sein. Im echten Run sind alle Logins frisch.

## Env-Variablen in der Sandbox

`CHALLENGE_API_URL`, `CHALLENGE_RUN_TOKEN`, `CHALLENGE_SITE_ACCESS`, `LLM_PROXY_URL`, `QWEN_BASE_URL`,
`QWEN_API_KEY`, `CLEF_BASE_URL`, `CLEF_API_KEY`, `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `GOOGLE_GEMINI_BASE_URL`,
`CDP_URL` (Browser 1), `CDP_URLS` (JSON-Array, bis 4), `BROWSER_COUNT`, `DISPLAY` (:1 … :4, je 1280×800),
`HTTP(S)_PROXY=http://127.0.0.1:3128` (einziger Weg nach draußen; Chromium ist schon konfiguriert),
`NO_PROXY=localhost,127.0.0.1`. Team-Secrets (z. B. `ANTHROPIC_API_KEY`) nur im Run-Schritt.

Sandbox: 4 vCPU, 8 GB RAM, Python 3.12 + uv, Node 24, Rust. **Headful** Chromium, bereits gestartet.
Keine eigenen Browser starten (werden gekillt). Tabs/Contexts in den gegebenen Browsern sind frei.

## Modelle

| Modell | Preis (simuliert, zählt in Kosten) | Eigenschaften (gemessen) |
| --- | --- | --- |
| Qwen3.8-Flash-Next (`model="qwen3.8-flash-next"`, OpenAI-kompatibel) | $0.15/M in, $0.016/M cached, $0.47/M out | 256k Kontext, multimodal, Tool-Calls ✔. Reasoning-Modell: ~3,6 s für einfachen Tool-Call mit Thinking, **~1,1 s mit `extra_body={"chat_template_kwargs":{"enable_thinking": False}}`**. Max 32 parallele Calls/Team. |
| Cloudflare Clef (`POST $CLEF_BASE_URL/v1/decide`) | $0.24/M in | Entscheidungsmodell: `choice`, `noul` (P(ja)), `score`; bis 4 Screenshots. Max 16 parallel. |
| Eigene Keys (Anthropic, OpenAI, Google, …) | Listenpreise | Über Proxy, Key als Team-Secret. Teurer → nur für schwere Schritte. |

Ranking: Kosten × Zeit (Kosten min. $0.01, Zeit min. 1 s). Bei gleicher Qualität gewinnt das kleinere Produkt.
Erst kommt aber **Qualität** — wenn ein Frontier-Modell 5 Punkte mehr bringt, lohnt es sich.

## Scoring (Bid-Fall)

| Teil | Punkte |
| --- | --- |
| Decision (`bid`/`no_bid`) | 5 |
| Tender-Referenz (auch Eligibility!) | 5 |
| `discount_percent` | 5 |
| `total_net` (auf Cent, konsistent mit Positionen) | 5 |
| Arithmetik (jede Position + Summe konsistent) | 5 |
| Check: zurückgezogene Q&A-Menge (falls vorhanden) | 5 |
| Check: datumsgewählter Metallindex (falls vorhanden) | 5 |
| Positionen (gleich gewichtet; je Position: Material 20 %, Menge 15 %, Basispreis 15 %, Zuschlag 10 %, Verpackung 10 %, Projektbonus 10 %, Endpreis 10 %, Lieferzeit 10 %) | 75 − Checks |

No-Bid-Fall: Decision 75, Referenz 5, keine Positionen 5, Total 0 → 15.
Positionen werden über die Positionsnummer gematcht; **zusätzliche oder doppelte Positionen kosten je einen
Positionsanteil**. Geld wird auf den Cent verglichen. Test-Runs zeigen Punkte je Feld (nie Sollwerte),
Scored-Runs nur Gesamtqualität.

Konsequenzen:
- Referenz falsch ⇒ nicht eligible ⇒ ganz unten. Referenz exakt aus Brief/Portal übernehmen (keine Look-alikes wie `…-A`).
- Falsches `no_bid` kostet fast alles. `no_bid` nur, wenn sicher **kein aktives** Material für eine Position existiert.
- Arithmetik-Punkte sind „gratis“: Total immer aus den tatsächlich eingegebenen gerundeten Endpreisen berechnen.
- Optionale Felder (Basis, Zuschlag, Verpackung, Bonus) immer ausfüllen; „0,00“ für bekannte Null.

## Answer-Schema (`docs/reference/answer-schema.json`)

```
tender_reference: str ≤100      decision: "bid"|"no_bid"      currency: "EUR"     discount_percent: 0..100
positions[≤100]: { position: str ≤20, material_number: str ≤50, quantity: int ≥0, unit_price: num ≥0,
                   lead_time_weeks: int ≥0, base_unit_price?, surcharge_per_unit?, packaging_per_unit?, rebate_total? }
total_net: num ≥0     notes?: str ≤4000     additionalProperties: false
```

Abgabe erfolgt **auf der Website** (submission.kind = "site"), nicht per API. Wir bauen trotzdem intern genau dieses
JSON (validiert gegen `answer_schema` aus dem Run) und übertragen es in den Konfigurator.

## Abgabe-Ablauf im Public-Portal (Beispiel, privat kann anders aussehen)

Aus dem Brief: „Teilnahme bekunden“ → „Angebotsabgabe“ → Bieter registrieren (Firmenname + USt-IdNr. aus Brief) →
Hauptangebot (bid) / Verzichtserklärung (no_bid) → Material-Konfigurator je Position → Netto-Einzelpreise + Summe
im **deutschen Zahlenformat** → Vergabeunterlagen anerkennen → TAN aus Webmail → Zusammenfassung prüfen →
„Angebot verbindlich abgeben“ im **Browser-Dialog (`window.confirm`) bestätigen**. Genau eine Abgabe. Zurück löscht
spätere Schritte. Neue TAN-Anforderung invalidiert die alte, TAN gilt 5 min. Session-Timeout verwirft Ungespeichertes.
Angezeigte Bruttosumme (inkl. 19 % USt) ist **nicht** der Nettowert. Siehe `docs/03-site-recon.md` §Konfigurator.

## Fair Play (Zusammenfassung von /docs/fair-play.md)

Erlaubt: beliebiges Agentendesign, mehrere Agents/Browser parallel, eigene Tools, Wissen aus Scouting über *wie* die
Sites funktionieren. Verboten: Hardcoding von Szenario-Werten (auch als Default/Fallback/Prompt/Notiz),
Pre-Solving, Skripte statt Agent (fester Klickpfad, Crawler → ein LLM-Call), Hilfe von außen, lokale Modelle,
eigene Browser, Angriffe. Prüfung: AI-Reviewer liest das ganze Package vor jedem Scored-Run (passed/flagged/rejected).
Der Brief nennt u. a. selbst: Klausel 11 „Agent requirement“ — Helpers dürfen ausgewählte Seiten extrahieren,
ausgewählte Dokumente parsen, vom Modell gewählte Ergebnislisten paginieren, rechnen, validieren, retry, Formulare
füllen; sie dürfen **nicht autonom** die Websites durchlaufen, um die komplette Evidenz vor der modellgesteuerten
Untersuchung zu sammeln.

## Submissions & Finale

- `atira test-run ./agent` (bis 4 gleichzeitig, volle Punkteaufschlüsselung) — beliebig oft.
- `atira submit ./agent` — Review, dann 1 Scored-Run auf public. Nur eine Submission gleichzeitig. Bestes Ergebnis
  zählt fürs Leaderboard, **die letzte bestandene** geht ins Finale.
- Finale: 1 Versuch oder **Best of 3** (`atira final --best-of 3` vor Start des Finales) → wir sollten Best-of-3
  aktivieren (bester Einzelversuch zählt, kein Nachteil außer BYO-Kosten bis 3×).
- GitHub-Repo auf der Teamseite verlinken (Organisatoren vergleichen Source und Package).

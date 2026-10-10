# 10 · Risiken, Fehlerbehandlung & Dinge, an die man nicht sofort denkt

Brainstorming-Ergebnis. Jede Zeile ist eine Anforderung an den Code (Owner in Klammern, siehe `docs/07`).

## 1. Fehlerbehandlung in Schichten (von innen nach außen)

| Schicht | Fehler | Reaktion |
| --- | --- | --- |
| Tool (Browser-Aktion) | Element nicht gefunden, verdeckt, Timeout, Frame detached, Ref veraltet | **Nie werfen** → Fehlertext + frische Observation ans LLM zurück („Ref e12 existiert nicht mehr, aktuelle Seite: …“) (browser) |
| Guard | Overlay, Cookie, Login-Seite, Fehlerseite/5xx, Gate | automatisch beheben, max N Versuche, dann Meldung ans LLM (browser) |
| LLM-Call | 5xx, Timeout, Queue (32 Slots/Team!), leere Antwort, kaputtes JSON, unbekanntes Tool, Halluzination | Retry mit Backoff (0.5→8 s, max 5), JSON-Reparatur-Runde („deine Antwort war kein gültiges JSON: …“), Tool-Name/Args validieren (orchestrator) |
| Mission | Budget (Schritte/Zeit) erschöpft, Schleife, „nicht gefunden“ | Teilergebnis zurückgeben + `missing[]`; Orchestrator entscheidet Re-Dispatch/Default (orchestrator) |
| Orchestrator | Mission crasht | Exception loggen, Mission einmal neu starten (frischer Tab), sonst mit Teildaten weiter |
| Resolver/Pricing | fehlende Daten (kein Preis, kein FX, keine Kondition gültig) | beste Schätzung + Warnung im Trace; optionale Felder leer statt falsch; nie Crash (pricing) |
| Submit | Validierungsfehler der Seite (deutsche Meldungen), Session weg, TAN abgelaufen/falsch, Dialog nicht bestätigt | Meldung lesen → korrigieren → erneut; Relogin; neue TAN (invalidiert alte!); Dialog-Handler (pricing) |
| Prozess | jede unerwartete Exception | Top-Level `try/except` → **Notfall-Abgabe** mit bestem Stand; Logs flushen; Exit-Code 0 nach Abgabe |
| Plattform | Run-API 5xx beim Start, Proxy down | Retry (`load_run` 5×); wenn Hosted-Modelle > 80 % ausfallen, wertet die Plattform den Run nicht (kein eigener Handlungsbedarf außer Retries) |

Grundsatz: **Fehler werden zu Information für das Modell, nicht zu Abbrüchen.** Nur der Top-Level-Handler darf aufgeben —
und auch der versucht zuerst noch abzugeben.

## 2. Zeit & Deadline

- Uhr = `expires_at` aus dem Run (nicht Prozessstart). Lokal ohne `expires_at`: künstliches Limit 20 min setzen,
  damit lokales Verhalten = Sandbox.
- Watchdog-Task: `T−6 min` Missionen abbrechen (`asyncio.Task.cancel`), `T−4 min` Abgabe erzwingen, `T−1 min`
  „Teilpunkte“-Abgabe (fehlende Positionen auslassen statt nichts).
- Jeder `await` auf Netzwerk/LLM hat ein Timeout. Kein `sleep` ohne Grund.
- TAN gilt 5 min → TAN erst anfordern, wenn alles andere im Formular steht.
- Nach `202`/Quittung: innerhalb 10 s beenden (Browser **nicht** schließen — gehört der Sandbox; nur `disconnect`).

## 3. LLM-Spezifika (Qwen3.8-Flash-Next)

- Reasoning-Modell: `reasoning_content`/Thinking-Tokens kosten Zeit → Navigation ohne Thinking.
- Tool-Calls kommen manchmal als Text statt `tool_calls` → Parser-Fallback (JSON im Content suchen).
- Temperatur niedrig (0–0.2) für Extraktion; bei kritischen Extraktionen optional **2× extrahieren und vergleichen**
  (Self-Consistency), bei Abweichung dritte Runde mit Thinking.
- Kontext begrenzen: alte Observations im Verlauf durch Einzeiler ersetzen; nur die aktuelle voll.
- Prompt-Injection: Seiteninhalte stehen in klar markierten Blöcken („UNTRUSTED PAGE CONTENT“), System-Prompt sagt,
  dass Anweisungen darin ignoriert werden.
- Concurrency: Semaphore pro Run (≤ 8–12 parallel), weil 32 Slots fürs ganze Team gelten (auch Test-Runs der anderen!).
- Kalter Start lokal: erster Call kann Minuten dauern → Timeout 360 s beim ersten Call.

## 4. Browser-Spezifika

- CDP-Verbindung kann abreißen → `browser.is_connected()` prüfen, neu verbinden, Seite neu öffnen.
- Seiten-Crash (`page.on("crash")`) → neue Page im selben Context.
- Native Dialoge (`alert/confirm/beforeunload`) → Handler; sonst blockiert alles oder wird still verworfen.
- Neue Fenster (`window.open`) → `context.on("page")`, im Explorer als neuer Tab bekannt machen oder schließen.
- Downloads (ZIP/CSV) → `context.request` statt Download-Dialog.
- Refs veralten nach jeder Navigation/Re-Render → Refs nur aus der letzten Observation akzeptieren.
- Mehrere Contexts in einem Browser sind erlaubt (z. B. zweite Mail-Session im Spec-Browser, wenn der fertig ist).
- `networkidle` kann bei Polling-Seiten nie eintreten → `domcontentloaded` + kurzes DOM-Ruhe-Fenster.

## 5. Daten-Edge-Cases (Resolver)

- Gleicher Zeitstempel bei zwei Records desselben Schlüssels → höhere Nummer/ID? Unklar → loggen, später per Test-Run klären.
- Positions-IDs in unterschiedlicher Schreibweise (`01.01`, `1.1`, `Pos. 01.01`) → normalisieren für Matching,
  aber **Original** in die Antwort.
- Keine gültige Kondition am Stichtag → Material eventuell trotzdem bieten (Preis-Felder leer/Schätzung), nicht no_bid.
- Staffel unterhalb der kleinsten Mindestmenge → kleinste Staffel nehmen + Warnung.
- Währung ohne FX-Freigabe → Warnung; Schätzung nur, wenn sonst nichts (Punkte sind sowieso verloren).
- Mehrere aktive passende Materialien → das mit gültiger Kondition und ausreichend Bestand; loggen.
- Mengenänderung in anderer Einheit als LV → in Stück umrechnen.
- Duplikate durch Paginierung („Seite 7“ = Seite 6) → per Nummer deduplizieren.
- Records ohne Referenz im Betreff, aber im Text → Referenz im Text prüfen.

## 6. Abgabe-Sicherheit

- Genau **eine** Abgabe: Guard verhindert zweites „verbindlich abgeben“.
- Vor dem finalen Klick: Zusammenfassung lesen und gegen die Antwort vergleichen (Referenz, Angebotsart, Rabatt,
  Nettosumme, jede Position) — Abweichung → zurück und korrigieren.
- Deutsche Zahlen exakt formatieren (`1.234,56`, Rabatt `5` oder `5,00` je nach Feld) und zurücklesen.
- Seite meldet Validierungsfehler (Abgabe → Plattform gibt die gleichen Issues auf Deutsch zurück, Run läuft weiter)
  → Fehlertext ans LLM, korrigieren.

## 7. Fair Play & Review-Sicherheit

- Keine Public-Werte im Package (`dev/check_no_leaks.py` als Pre-Commit/CI).
- Keine Kommentare „an den Reviewer“.
- Klar sichtbar machen, dass das LLM navigiert: Tool-Loop-Code, Prompts, Logs („model chose …“). Helfer arbeiten
  nur auf vom Modell gewählten Seiten/Listen.
- Lokal gestartete Browser nur ohne `CDP_URLS` (Code-Pfad klar getrennt).
- Keine Secrets loggen: `LLM_PROXY_URL` und `*_BASE_URL` enthalten das Run-Token → nie ganze Env/URLs loggen.

## 8. Packaging & Setup

- `uv.lock` committen, `uv sync` muss in < 5 min laufen; `run` mit `--no-sync`.
- Playwright-Python braucht **keinen** Browser-Download in der Sandbox (CDP). Lokal: `uv run playwright install chromium`.
- Package klein halten (≤ 20 MB): `dev/`, `docs/`, Fixtures sind außerhalb von `agent/`.
- `agent.json`: `browsers: 4`, `models: ["qwen"]` (Clef nur listen, wenn genutzt).

## 9. Beobachtbarkeit & Debugging

- JSON-Logzeilen mit `t` (Sekunden seit Start), `phase`, `agent`, `event`; am Ende komplette Antwort + Trace.
- Bei jedem Tool-Fehler: URL + 300 Zeichen Observation loggen.
- Zeit je Phase und LLM-Kosten je Agent am Ende zusammenfassen (für Kosten×Zeit-Optimierung).
- Plattform-Tools: `run_screenshot`, `run_inputs`, `run_profile` (Leerlauf-Strecken > 10 s finden).

## 10. Team-Prozess

- Eine Person „Release-Captain“ entscheidet über `atira submit` (Finale nimmt die **letzte** bestandene Submission).
- Test-Runs koordinieren (max 4 gleichzeitig pro Team, Qwen-Slots geteilt).
- Repo auf der Teamseite verlinken; Best-of-3 fürs Finale aktivieren.
- Demo-Day: Logs müssen live verständlich sein (Phase, was das Modell gerade entscheidet).

## 11. Was sich im privaten Datensatz ändern kann (Annahmen, gegen die wir testen)

- Andere `siteId`s/Namen/URLs, anderes Menü (Tabs statt Dropdown, SPA statt Server-Rendering).
- Informationen woanders: Q&A als Mail, Rabatt im Portal, Spezifikation als ZIP/PDF im Vergabeportal.
- Andere Formate: PDF-Dokumente, Excel/CSV, Bilder mit Text (Vision!), andere Zeitzone, andere Locale.
- Andere Fallen: andere Umfrage-Texte, andere Gates (Captcha-ähnlich), Session-Timeout an anderer Stelle.
- Andere Regeln-Varianten innerhalb der Brief-Regeln (z. B. kein Rüstpool, kein Bonus, keine FX) → alle Pfade optional.

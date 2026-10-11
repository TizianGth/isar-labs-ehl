# 13 · Optimierungs-Roadmap: Umsetzungsstand und Übergabe

Stand: 2026-10-11, Branch `feat/agent-improvements`, lokal committet, **nicht gepusht**. Basis-Tag: `perf-base-c` (= `006bc96`).
Begründung und Messwerte: [`docs/12-optimization-analysis.md`](12-optimization-analysis.md).
Die Datei `13-winning-backlog.md` ist ein älteres, separates Dokument.

## 1. Regeln für jede weitere Arbeit
- AGENTS.md gilt. Insbesondere:
  - keine Szenario-Werte in `agent/`
  - **kein `atira submit`/`cancel` ohne menschliche Freigabe**
  - `dev/check_no_leaks.py` muss grün sein
- **Neues Verhalten startet per Schalter aus** (`agent/rfq/flags.py`). Eingeschaltet wird es erst nach gepaarten Test-Runs (§3).
  - Grund: `agent/` wurde schon direkt eingereicht, und die zuletzt bestandene Submission geht ins Finale.
- Pro Schritt gilt:
  - Tests: `cd agent && uv run pytest -q`
  - dev-Tests: `uv run --project agent pytest -q dev/tests`
  - Leak-Check
  - ein Commit

## 2. Stand der Schritte

| ID | Inhalt | Schalter (Standard) | Status |
|---|---|---|---|
| O-0 | Basis einfrieren: Tag `perf-base-c`; 4 C-Runs in `dev/runs/LOG.md` | – | erledigt |
| O-1 | `rfq/metrics.py`; `run_summary`-Zeilen am Ende: Zeitmarken, Missionen, Slot-Wartezeit je Lane, Prompt-Größen, Browserzeiten; Gates und Trace als wenige Zeilen | – (immer an) | erledigt, sandbox-ungetestet |
| O-2 | `dev/eval/bench.py` (Tabelle und `--pairs`-Entscheidung), `dev/eval/make_variant.py` | – | erledigt; reproduziert die Zahlen aus docs/12 |
| G-0 | Rollback-Schalter für bereits ausgelieferte Optimierungen | `RFQ_BACKGROUND_EXTRACTION`, `RFQ_REVIEW_PREFETCH`, `RFQ_STEPS_TOOL`, `RFQ_START_OVERLAP` (alle an) | erledigt |
| G-1 | Eigener Tab, wenn zwei Rollen dieselbe Website nutzen; sonst nacheinander | `RFQ_TAB_ISOLATION` (an) | erledigt, Unit-Tests |
| G-2 | Fehlendes auf einer vom Modell gewählten anderen Website suchen (15 Schritte, ≤ 150 s, nur mit ≥ 120 s Restzeit) | `RFQ_GAP_RECOVERY` (an) | erledigt, Unit-Tests; greift nur bei unvollständigen Missionen |
| G-3 | Perturbations-Tests (`dev/tests/test_perturbations.py`, 8/8 grün); Offline-Replay (`dev/eval/extract_replay.py`) | – | erledigt |
| P-1 | Hedge-Schwelle Tool-Calls 12 → 20 s | `RFQ_HEDGE_TOOLS_S` (20) | erledigt, aktiv |
| P-2 | Brief teilen: Kernfelder zuerst | `RFQ_BRIEF_SPLIT` (aus) | umgesetzt, **Test-Runs offen** |
| P-3 | `wait_for` kehrt mit dem nächsten Hintergrundergebnis zurück | `RFQ_WAIT_ON_BACKGROUND` (aus) | umgesetzt, **Test-Runs offen** |
| P-4 | Eigene Slots für Navigation (6) | `RFQ_LLM_LANES` (aus) | umgesetzt; einschalten erst, wenn `slot_wait_s:nav` ≥ 10 s in 2 von 3 Runs |
| X-1 | `extract`/`extract_urls` mit `then_click`/`then_open` | `RFQ_EXTRACT_THEN` (aus) | umgesetzt, **Test-Runs offen** |
| X-2 | Neue Links extrahierter Seiten im Ergebnis | `RFQ_LINKS_IN_RESULTS` (aus) | umgesetzt, **Test-Runs offen** |
| X-4 | Material-Schema ohne `description` | `RFQ_SCHEMA_TRIM` (aus) | Offline bestanden (33/33, −16 % Output); Mail-Kürzung **verworfen** (8/27 falsche Entwurfs-Flags); Test-Runs offen |
| X-3 | Historie verdichten (letzte 6 Schritte wörtlich, ältere einzeilig) | `RFQ_COMPACT_HISTORY` (deklariert) | **offen**: erst die O-1-Daten zu `prompt_chars_avg.history` abwarten; nur bauen, wenn die Historie ≥ 25 % der Prompt-Zeichen ausmacht |
| X-5 | Bieterdaten-Schritt des Angebotsformulars während der Evidenzphase füllen | `RFQ_PREPARE_BID` (deklariert) | **offen** |
| X-6 | ERP-Vorbereitung vor Bekanntsein der Kataloggruppen | – | **offen**; entscheiden nach O-1: nur wenn `first:requirements − brief_done` > 45 s |

## 3. Validierung (fester Ablauf)
1. **Basisläufe mit O-1:** 2 Test-Runs von `agent/` unverändert, also mit dem `run_summary`. Prüfen, ob die Zeilen erscheinen und die Zeitlinie ab t = 0 vollständig ist. Danach X-3, X-6 und P-4 anhand der Daten entscheiden.
2. **Kandidat erzeugen:**

       uv run --project agent python dev/eval/make_variant.py cand RFQ_WAIT_ON_BACKGROUND=1 RFQ_BRIEF_SPLIT=1 RFQ_EXTRACT_THEN=1 RFQ_LINKS_IN_RESULTS=1 RFQ_SCHEMA_TRIM=1

   - Erst als Bündel testen.
   - Bei REJECT einzeln testen, in dieser Reihenfolge: P-3, P-2, X-1, X-2, X-4.
3. **Gepaarte Läufe:** ≥ 3 Paare, Basis (`agent/`) und Kandidat (`dev/variants/cand`) jeweils gleichzeitig starten:

       atira test-run agent
       atira test-run dev/variants/cand

   - Keine anderen Team-Runs parallel (Limit 32 Slots).
4. **Entscheiden:**

       uv run --project agent python dev/eval/bench.py --pairs BASE1:CAND1 BASE2:CAND2 BASE3:CAND3

   - **ADOPT / ADOPT-COST:** Standard des Schalters in `rfq/flags.py` auf an setzen, committen.
   - **REJECT:** Schalter bleibt aus.
   - **INCONCLUSIVE:** 2 weitere Paare, dann neu entscheiden.
   - **INVALID:** Paar wiederholen.
   - Qualität und Eligibility sind binäre Gates.
5. **Extraktionsänderungen** zuerst offline prüfen:
   - lokaler Proxy (`atira models proxy`)
   - `extract_replay.py run --variant … --reps 3`, dann `compare base …`
   - Annahme nur, wenn die Übereinstimmung ≥ der Selbstübereinstimmung der Basis ist und der Output um ≥ 15 % sinkt.
6. **Vor jeder Submission:**
   - 3 aufeinanderfolgende Test-Runs des finalen `agent/` mit 100/eligible
   - Perturbations-Tests grün
   - **menschliche Freigabe**

## 4. Startprompt für das Implementierungsmodell
> Lies AGENTS.md, dann `docs/13-optimization-roadmap.md` und `docs/12-optimization-analysis.md`. Setze die offenen Punkte aus §2 in der Reihenfolge aus §3 um.
> - Neues Verhalten nur hinter dem genannten Schalter in `agent/rfq/flags.py`, Standard aus.
> - Je Schritt: Unit-Test schreiben, `cd agent && uv run pytest -q`, `uv run --project agent pytest -q dev/tests`, `uv run --project agent python dev/check_no_leaks.py`, ein Commit (Englisch, Imperativ).
> - Schalter-Standards nur nach dem Entscheidungsablauf in §3 ändern. Bei REJECT bleibt der Schalter aus; das Ergebnis kommt in die Tabelle §2.
> - Niemals `atira submit` oder `cancel` ohne ausdrückliche menschliche Freigabe.
> - Keine Szenario-Werte in `agent/`.

# Test-Run-Log

| Datum | Commit | Run-ID | Qualität | Zeit | Kosten | Auffälligkeiten |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-10 14:17 | 7ce4c30 | run_1a09996fe58f1bab | – (von mir abgebrochen) | hing ab 124 s | $0.00 | Browser 1 lud Vergabeportal nicht; evaluate ohne Timeout blockierte start() → behoben in cbf0f2e |
| 2026-10-10 14:32 | cbf0f2e | run_ed06b2d2e8a71fac | **100 · eligible** | ≈ 10,5 min (Evidenz 432 s, Abgabe ≈ 200 s) | $0.098 (257 Calls) | erster Plattform-Score; Oracle bestätigt; 1× fresh_tab im ERP gegriffen |
| 2026-10-10 15:4x | cbf0f2e | run_f33ba429c19874f5 | 0 · keine Abgabe | 18 min | – | Vergabeportal-Tab hing ab 77 s, Abgabe lief auf totem Tab; Spezifikation verlaufen → behoben in fa5f706 |
| 2026-10-10 16:12 | fa5f706 | run_c8fe786cf41aea8d | 59.2 · eligible | 13,5 min | $0.081 | Viewer-Seite 5 lud in der Sandbox nie (13×) → A-5/01.05 fehlten → behoben in 011dadc |
| 2026-10-10 16:40 | 011dadc | run_becee3d9dd562e33 | – (von Tizian abgebrochen) | 4 min | $0.065 | bis dahin gesund: nach 201 s fast alle Missionen fertig |
| 2026-10-10 16:58 | 011dadc | run_09624e2411da68ac | **100 · eligible** | ≈ 10 min (Evidenz 370 s) | $0.102 (262 Calls) | 1 hängende ERP-Seite umgangen, 1 ERP-Recovery, Review 44/44 |
| 2026-10-10 17:22 | 011dadc (`sub_6bdf6c7dfe36193f`) | run_6e07bf4619c1ff9c | **100 · eligible · Submission (scored)** | **7:19** (Evidenz 243 s, Review 438 s) | $0.0951 (241 Calls) | Leaderboard Platz 1; Review 44/44; nach Annahme 3× 401 (Token gesperrt, harmlos) |
| 2026-10-10 19:34 | 7406df9 (lokal, Scouting) | run12 (dry-run) | Oracle 100 · **Abgabe nicht erreicht** | 17:55 | ≈ $0.10 (256 Calls) | 7 hängende Qwen-Requests (2× 150 s Extraktion, 5× 60 s Navigation ≈ 600 s) + langsames ERP → Abgabe-Deadline → Hedging in 6692197 |
| 2026-10-10 18:02 | 6692197 | run_56b54d30340a6231 | **100 · eligible** | 11:05 (Evidenz 409 s, Abgabe 259 s) | $0.113 (270 Calls) | 10 Hedges (2× vom Zwilling gewonnen); 1 Extraktion trotzdem 150 s; Review ohne Abweichung |
| 2026-10-10 18:19 | f3ad49e | run_dc330a81b260392a | 0 · **keine Abgabe** | 18 min | $0.091 (221 Calls) | Mail-Site ab ≈ 146 s für unseren Browser unerreichbar (auch Startseite); Formular fertig, aber keine TAN; Startseite stand dauerhaft im Hang-Memo → `fresh_start()` vor TAN/Abgabe |

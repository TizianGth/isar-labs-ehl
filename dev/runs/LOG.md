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
| 2026-10-10 19:09 | 26f3b45 (Branch, von Mateen gestartet) | run_6c71b1a0e846697e | **5 · eligible (no_bid)** | 6:30 | $0.076 (201 Calls) | Q&A-Nachtrag „Hub 482 mm“ fehlte in der Evidenz, Tender-Mission trotzdem „complete“ → Nennhub 507 → kein Material → no_bid → Seitenprüfung Q&A + no_bid-Gegenprüfung |
| 2026-10-10 19:14 | 26f3b45 | run_ec25b8696be4aae5 | 0 · keine Abgabe | 18 min | $0.079 (204 Calls) | Evidenz komplett (482 s, 9× Hedge gewonnen), dann Vergabeportal ab 484 s unerreichbar |
| 2026-10-10 19:36 | 2798ec6 (**main, Kontrolle ohne Hedging**) | run_2398af48f4d9ee08 | 0 · keine Abgabe | 19 min | $0.050 (121 Calls) | 9 hängende Qwen-Requests (3× 150 s Spezifikation), Mail-Seiten luden nicht, alle Positionen fehlten → Umgebung gestört, nicht der Branch; Team-Runs 19:42/19:45 ebenfalls gescheitert |
| 2026-10-10 19:59 | 01c8371 (3 parallel) | run_628958b450683b5c | 0 · keine Abgabe | 18 min | $0.077 | Bieterfragen-Tab (iframe) lud nicht → no_bid; Tender-Detailseite hing in der Abgabe (Startseite lud) |
| 2026-10-10 19:59 | 01c8371 (3 parallel) | run_c149b0e54aaee796 | **100 · eligible** | 8:17 | $0.097 (253 Calls) | sauber; Abgabe 47 Modellschritte (7 je Position) |
| 2026-10-10 19:59 | 01c8371 (3 parallel) | run_4cb6547f1f3ded33 | 0 · keine Abgabe | 18 min | $0.088 | Evidenz komplett (404 s, Q&A 6/6), dann 11 min Abgabe ohne Erfolg: 16 Seiten-Timeouts am Vergabeportal |
| 2026-10-10 22:19 | fc2c5cc (main) | run_af2c9746c819df92 | **100 · eligible** | 14:07 (Plattform-Störung) | $0.118 (268 Calls) | lief durch, obwohl CLI/Modell-Host zeitweise nicht erreichbar waren |
| 2026-10-10 22:48 | fc2c5cc (main, parallel) | run_3c2af28a03720c51 | **100 · eligible** | 8:39 | $0.087 (217 Calls) | Evidenz 334 s; Abgabe 26 Schritte (6× verkettetes fill_fields, 0 Abbrüche), 187 s |
| 2026-10-10 22:47 | fc2c5cc (main, parallel) | run_6ed27b8b18f38d85 | **100 · eligible** | 8:05 | $0.079 (212 Calls) | Evidenz 330 s; Abgabe 24 Schritte (vorher 47), 158 s |
| 2026-10-11 00:42 | 3feed5c (fix/spec-values) | run_d5734b160416bc3a | **100 · eligible** | **6:10** | $0.100 (237 Calls) | Schema „certification“ präzisiert, Gegenprüfung liest Anforderungen neu, feldweises Zusammenführen |
| 2026-10-11 00:5x | 3feed5c (fix/spec-values) | run_68c3334e56608de4 | **100 · eligible** | **4:43** | $0.091 (221 Calls) | |
| 2026-10-11 00:5x | 3feed5c (fix/spec-values) | run_21bdb7757c80d963 | **100 · eligible** | **5:07** | $0.084 (221 Calls) | |

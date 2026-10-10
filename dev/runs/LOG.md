# Test-Run-Log

| Datum | Commit | Run-ID | Qualität | Zeit | Kosten | Auffälligkeiten |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-10 14:17 | 7ce4c30 | run_1a09996fe58f1bab | – (von mir abgebrochen) | hing ab 124 s | $0.00 | Browser 1 lud Vergabeportal nicht; evaluate ohne Timeout blockierte start() → behoben in cbf0f2e |
| 2026-10-10 14:32 | cbf0f2e | run_ed06b2d2e8a71fac | **100 · eligible** | ≈ 10,5 min (Evidenz 432 s, Abgabe ≈ 200 s) | $0.098 (257 Calls) | erster Plattform-Score; Oracle bestätigt; 1× fresh_tab im ERP gegriffen |
| 2026-10-10 15:4x | cbf0f2e | run_f33ba429c19874f5 | 0 · keine Abgabe | 18 min | – | Vergabeportal-Tab hing ab 77 s, Abgabe lief auf totem Tab; Spezifikation verlaufen → behoben in fa5f706 |
| 2026-10-10 16:12 | fa5f706 | run_c8fe786cf41aea8d | 59.2 · eligible | 13,5 min | $0.081 | Viewer-Seite 5 lud in der Sandbox nie (13×) → A-5/01.05 fehlten → behoben in 011dadc |
| 2026-10-10 16:40 | 011dadc | run_becee3d9dd562e33 | – (von Tizian abgebrochen) | 4 min | $0.065 | bis dahin gesund: nach 201 s fast alle Missionen fertig |
| 2026-10-10 16:58 | 011dadc | run_09624e2411da68ac | **100 · eligible** | ≈ 10 min (Evidenz 370 s) | $0.102 (262 Calls) | 1 hängende ERP-Seite umgangen, 1 ERP-Recovery, Review 44/44 |

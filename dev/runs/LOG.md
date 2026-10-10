# Test-Run-Log

| Datum | Commit | Run-ID | Qualität | Zeit | Kosten | Auffälligkeiten |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-10 14:17 | 7ce4c30 | run_1a09996fe58f1bab | – (von mir abgebrochen) | hing ab 124 s | $0.00 | Browser 1 lud Vergabeportal nicht; evaluate ohne Timeout blockierte start() → behoben in cbf0f2e |
| 2026-10-10 14:32 | cbf0f2e | run_ed06b2d2e8a71fac | **100 · eligible** | ≈ 10,5 min (Evidenz 432 s, Abgabe ≈ 200 s) | $0.098 (257 Calls) | erster Plattform-Score; Oracle bestätigt; 1× fresh_tab im ERP gegriffen |

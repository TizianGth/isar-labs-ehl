# Kickoff: feat/pricing-submission

(Gemeinsamer Teil: docs/prompts/_common.md einfügen.)

Deine Aufgabe (Owner Regeln, Rechnen, Abgabe), zusätzlich lesen: docs/04-business-rules.md (komplett, Wort für Wort),
docs/03-site-recon.md §1 Konfigurator, docs/05-browser-playbook.md §Zahlen, docs/06-testing.md.

1. `agent/rfq/facts.py` nach docs/07 (Vertrag für alle) — zuerst, früh nach main mergen.
2. `rules/parse.py` (Locale-Zahlen, Datum mit Format-Tag, Zeitzonen → UTC, Einheiten), `rules/resolve.py`
   (Abschnitte A–C, E, F, K), `rules/pricing.py` (D, G–J; Decimal, ROUND_HALF_UP, Largest-Remainder mit Tie-Break),
   `rules/validate.py` (Schema aus Run, Arithmetik, Plausibilität). Jede Entscheidung in einen `trace` schreiben.
   Volle Unit-Test-Abdeckung mit synthetischen Daten (Liste in docs/06 Ebene 1).
3. `submit/configurator.py`: LLM-geführt (site-agnostisch), Werte aus Answer; deutsches Zahlenformat; Fallen:
   vorausgewähltes Nebenangebot, „Position hinzufügen“, „Position auslassen“, AGB-Checkbox, Brutto/verstecktes Total;
   nach jedem Schritt Werte zurücklesen; Positionsreihenfolge kommt zufällig.
4. `submit/tan.py`: TAN anfordern → Mail-Session (erp-mail) liest neueste TAN → eintragen →
   `page.once("dialog", accept)` → „verbindlich abgeben“ → Quittung loggen. `--dry-run` stoppt vor TAN.
5. dev/: `oracle/public-1.json` (Soll für public-1, nur dev!), `eval/score.py` (Punkteschema), `check_no_leaks.py`.

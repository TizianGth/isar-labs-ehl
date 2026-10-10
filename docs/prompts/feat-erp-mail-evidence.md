# Kickoff: feat/erp-mail-evidence

(Gemeinsamer Teil: docs/prompts/_common.md einfügen.)

Deine Aufgabe (Owner ERP & Mail), zusätzlich lesen: docs/03-site-recon.md §0, §3, §4, docs/04-business-rules.md
§A, §D–K, docs/09-glossary-de.md.

1. Missions-Prompts (site-agnostisch) `materials.md`: Suche per Kataloggruppe (≤ 20 Treffer, sonst eingrenzen),
   für **jeden** Kandidaten: technische Merkmale, Status (Legende beachten), Packinhalt, Umlagegewicht,
   Zuschlagsmasse, Preisindex-Key, Lieferzeiten, **frei verwendbarer** Bestand (nicht physisch/Prüfbestand),
   **alle** Konditionssätze (Gültigkeit, Währung, FX-Key, Preiseinheit, Zahlenformat, Staffeln im Unterregister).
   `approvals.md`: Mail-Suche Referenz + Betreff (UND), Posteingang **und** Archiv, alle Seiten, Original hinter Gate
   öffnen, Entwürfe/Vorschauen ignorieren, Absender + Datum (UTC) + exakte Referenz erfassen.
2. Interpretations-Prompts Mail-Prosa → DiscountApproval (Prozent, Bedingung Mindestwert), ClauseRule
   (Scope-Enum je Gruppe), EndReserve, PackagingTariff, DeliveryCommitment (Artikel, Wochen, Grenze),
   FxRate, SetupPool (Betrag + Verteilregel), BonusPlan (Bänder + Verteilregel), MetalIndex (Fenster) — mit `quote`.
3. TAN-Leser für Submit: neueste „Signatur-TAN“-Mail zur exakten Referenz nach Anforderungszeitpunkt.
4. ERP-Session-Timeouts und 503 über Guards abfangen (mit browser-tools abstimmen).
5. Tests: synthetische Prosa-Varianten (umformuliert!) → erwartete Facts; Fixture-Snapshots in dev/tests/.

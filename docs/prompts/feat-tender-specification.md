# Kickoff: feat/tender-specification

(Gemeinsamer Teil: docs/prompts/_common.md einfügen.)

Deine Aufgabe (Owner Vergabe & Spezifikation), zusätzlich lesen: docs/03-site-recon.md §0–2,
docs/04-business-rules.md §A–C, docs/09-glossary-de.md.

1. Missions-Prompts (site-agnostisch!) in agent/rfq/llm/prompts/: `tender_core.md` (Referenz exakt, Teilnahme
   bekunden falls nötig, LV/Positionen inkl. Einheit + Anforderungsreferenz, Dokumente inkl. ZIP/iframe),
   `clarifications.md` (alle Q&A-Seiten, Datum aus Tooltip/ISO, jede Antwort klassifizieren),
   `specification.md` (Dokument finden, Freigabeformular korrekt ausfüllen, Revision mit Status „gültig/aktuell“
   wählen — nicht die zuerst angezeigte, alle Anforderungsseiten lesen, Originalwortlaut auswerten).
2. Interpretations-Schritt (Qwen mit Thinking, strenges JSON): Q&A-Antwort → QuantityChange | Withdrawal |
   TechAmendment | KeyAssignment[] | SetupCost | irrelevant, jeweils mit `quote`. Spezifikation → Requirement
   (Kataloggruppe, nutzbarer Weg + Endreserve-Key, Stück je Satz, Zertifizierung „keine“ → None).
3. Facts-Modelle in facts.py mit pricing-Owner abstimmen; Unit-Tests für die Interpretation mit synthetischen Texten
   (eigene Formulierungen, keine Public-Werte), Snapshot-Tests gegen dev/fixtures in dev/tests/.
4. Erfolgskriterium: lokaler Lauf liefert vollständige, korrekte Positionen/Mengen/Anforderungen für public-1
   (Abgleich dev/oracle), auch wenn Labels/Reihenfolgen perturbiert sind.

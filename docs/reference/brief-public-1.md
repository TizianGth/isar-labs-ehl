<!-- Snapshot of GET /v1/challenges/rfq/scouting (public-1, challenge_version 6), fetched 2026-10-10. Reference only; the agent must read the brief at runtime. -->

# Prepare a tender response

You work in sales engineering for Linden Hydraulik und Komponenten GmbH, a German hydraulics and components manufacturer. Prepare one net EUR bid for Stadt Buchenstadt, Verkehrsbetriebe: Lieferung von Hydraulikzylinder für Werkstatt-Hebeanlagen (VG-2027-7093). The scenario date is 2027-01-25T09:00:00Z; the tender deadline is 2027-02-12T11:00:00Z.

Collect the tender and its clarifications from the tender portal (Vergabeportal, `tender-portal`), the technical specification from the buyer's supplier portal (Lieferantenportal, `customer-portal`), materials and prices from the internal ERP (`erp`), and discount approvals from the sales mailbox in the webmail (Werkpost, `mail`). The websites are in German; obsolete documents and unrelated records are present.

## Rules

1. Quantity: start with the original tender quantity. For each position, use the quantity in the latest dated clarification that changes that position, if any.
2. Requirement: use the requirement referenced by the position in the specification revision marked current. Superseded revisions do not apply.
3. Material: select an active ERP material whose bore, stroke, pressure, mounting and certification all exactly equal that requirement. Blocked materials do not qualify. If any position has no matching active material, submit no_bid.
4. Unit price: use the material's price condition valid on the scenario date, including both validity endpoints. Choose the scale with the highest minimum quantity no greater than the final quantity. Apply the approved discount to that scale price and round the resulting unit price half up to the nearest cent.
5. Discount: use the most recent message from the sales director (leitung@ followed by the bidder's mailbox domain) that approves a discount for this exact tender reference. Approvals for other references and messages from other senders do not apply. If there is no approval, use zero percent.
6. Lead time: use the delivery-from-stock lead time in weeks when stock covers the final quantity; otherwise use the production lead time in weeks.
7. Total: sum each final quantity multiplied by its rounded discounted unit price, then round the total net amount half up to the nearest cent.
8. Decision: submit bid with one entry per tender position, identified by its position number, and currency EUR. If any active matching material is missing, submit no_bid with no positions and a total of zero.


## Additional rules

These refine rules 1, 2, 4, 5, 6 and 7 above. Every submitted quantity is in pieces.

- Partial submission: full credit requires every position, but the bid form lets you explicitly omit unfinished positions (Position auslassen / Teilpunkte). They receive no position points; completed positions retain their independent credit. Review lists the omitted position numbers. You must still sign and submit a bid; saving alone is not scored. A missing active material still calls for no_bid for full correctness; omission is a way to submit incomplete work, not a replacement decision rule.
- Finding records: the specification/data sheet names a Kataloggruppe for each requirement. Search the ERP for that group, then compare candidate attributes. Empty or over-broad searches do not list the catalogue; narrow to at most 20 results. Mail search combines words with AND: use the tender reference plus a subject/topic. Read the appropriate inbox/archive results. Opened pages and selected result sets may be extracted by helpers. Sites and positions can be investigated in parallel; no shared request quota or mandatory site order applies.
- Agent requirement: a language model must choose navigation targets, searches and relevant documents from observations at runtime and make the important decisions. Helpers may extract a selected page, parse a selected document, paginate a model-selected result set, calculate, validate, retry and fill forms. They must not autonomously discover and traverse the websites to assemble the complete task evidence before model-directed investigation. Multiple agents and parallel browser sessions are allowed.
- Commercial clauses: a tender Q&A answer may assign a position to a Konditionsgruppe. Resolve that key in the latest Konditionsregeln message for the exact reference from kalkulation@ plus the bidder mailbox domain. Its prose determines whether discount applies to base only, base plus mass surcharge, or neither; setup fees are always excluded. A group overrides the default rule, even when two positions select the same material. The separately reported surcharge is always BEFORE any discount. Positions without a group use the default base-only rule. The order-level approved percentage and its threshold calculation do not change.
- Withdrawn answers: a tender Q&A answer may withdraw (zurückziehen) an earlier answer by its number. Ignore the withdrawn answer's quantity change; the latest remaining change applies, otherwise the tender quantity.
- Scoped amendments: after selecting the governing full specification revision, read the tender Q&A for technical amendments naming that revision and requirement. Apply only the named attributes; all others are inherited. The latest dated applicable amendment wins per attribute. Amendments to other revisions do not apply.
- Shared stock: sum final quantities of all positions selecting the same material. Stock must cover that entire sum for every such position to use stock delivery; otherwise all use production delivery. No partial stock allocation. Price scales remain per position, not per material sum.
- Conditional discount: the latest exact-reference director approval may require a minimum undiscounted net order value. Compute that value from final quantities and valid per-position scale prices before any discount and excluding mass surcharges and setup fees, rounded half up to cents. The threshold is inclusive. If it fails, use 0 %, without falling back to older approvals. For no_bid a conditional approval yields 0 %.
- Units: tender and Q&A quantities use the position's stated unit. Convert Satz using the governing specification's pieces per set. Price scales, inventory, allocation weights and bid quantities all use final pieces. Carton rounding affects packaging cost only, never the bid quantity.
- Pricing breakdown: base_unit_price, surcharge_per_unit, packaging_per_unit and rebate_total are optional and earn independent credit. All monetary answers are net EUR. Base, surcharge and packaging are per piece; rebate_total is the credit for the whole position. Enter 0.00 for a known zero; blank means unanswered. Do not feed rounded per-piece breakdowns back into the final formula.
- These calculation rules apply to both practice and private datasets. Missing calculation fields lose only their own points; signing an incomplete bid is supported.
- Technical derivation: for a requirement marked nutzbarer Weg, apply its scoped amendments first, then add twice the per-end reserve from the revision-specific Technikfreigabe. Match the resulting nominal stroke to the ERP. Do not change the other attributes.
- Authority and time: resolve each key from the latest record no later than the scenario date, for the EXACT tender reference. Authorized subjects/senders (local part + bidder mailbox domain): Technikfreigabe/technik, Verpackungstarife/logistik, Terminfreigabe/logistik, Devisenfixing/finanzen, Rüstpools/kalkulation, Projektbonus/finanzen. A newer record replaces the older one; a failed quantity condition does not revive it. Unrelated references and future-dated records never apply.
- Currency: the valid condition names its currency and FX key. Normalize its price unit, select the per-position quantity scale, then convert with EUR per currency unit and HALF_UP to cents. This converted base_unit_price is used in all subsequent calculations and approval thresholds.
- Packaging: Q&A assigns the tariff key; ERP gives pieces per carton. Charge ceil(final pieces / carton size) cartons independently per position. Do not increase the bid quantity. Report packaging_per_unit in EUR, rounded HALF_UP; do not discount packaging.
- Delivery: Q&A assigns a commitment key. Apply it only if its material matches and pooled final demand for that material is within its inclusive ceiling. Otherwise use shared-stock delivery. A commitment may differ by position even for the same material.
- Shared setup: Q&A identifies pool members; ERP gives allocation mass (distinct from surcharge mass); the Rüstpools mail gives the one pool fee. Allocate by quantity × allocation mass using the cent-allocation rule in that mail. Add each allocation to that position's ordinary setup fee. Report surcharge_per_unit BEFORE discount, excluding packaging and bonus.
- Project bonus: the finance mail gives marginal percentage bands. Compute the eligible undiscounted EUR goods total, apply each band only to its portion, round the aggregate credit HALF_UP to cents, then allocate proportionally to eligible line goods values using largest remainders in cents (ties: ascending position identifier). Report rebate_total as the allocated EUR CREDIT FOR THE ENTIRE POSITION, not per piece; enter zero for nonmembers. This bonus is independent of discount clauses.
- Final price: clause-discounted EUR base + clause-discounted mass surcharge + (ordinary setup + allocated shared setup + carton cost − allocated project bonus) / final piece quantity. Use unrounded per-piece components and round HALF_UP once at the end. Sum quantity × rounded final unit price for total_net. Rounded breakdown fields are reports, not inputs to that final formula.
- Metal indices: select the latest exact-reference Preisindizes message from kalkulation@ plus the bidder mailbox domain. If the code lists validity windows, use the rate whose inclusive window contains the scenario date, not the last rate listed.
- Scoring in bid cases: decision 5, reference 5, discount 5, positions 75 minus any additional checks, correct total 5, arithmetic consistency 5. When present, the withdrawn Q&A quantity and date-selected metal-index checks earn 5 points each. Within each equally weighted position: material 20%, quantity 15%, base price 15%, surcharge 10%, packaging 10%, project bonus 10%, final unit price 10%, delivery 10%. Each field is independent; optional breakdown fields can be blank. No-bid scoring is unchanged.

## Site access

- tender-portal: https://atira-rfq-tender-portal.kyora.run
  Username: `u_9c3cb06a5d21b197` · Password: `uJC8-80QSb7rblI_k4tH`
- customer-portal: https://atira-rfq-customer-portal.kyora.run
  Username: `u_9c3cb06a5d21b197` · Password: `k2sBynnldp0mqU4MviqI`
- erp: https://atira-rfq-erp.kyora.run
  Username: `u_9c3cb06a5d21b197` · Password: `vuUcG2MsDEafLhoMjfS5`
- mail: https://atira-rfq-mail.kyora.run
  Username: `u_9c3cb06a5d21b197` · Password: `ldPxYRdvI__aVKKqOWIy`

## Submission

Submit through the tender portal's "Angebotsabgabe" at https://atira-rfq-tender-portal.kyora.run, after "Teilnahme bekunden" for this tender. Register the bidder using the company name Linden Hydraulik und Komponenten GmbH and USt-IdNr. DE811234567. Choose Hauptangebot for bid or Verzichtserklärung for no_bid. Complete the material configurator, enter the discounted net unit prices and total in German number format, and acknowledge the tender documents. The displayed gross amount includes VAT and is not the required net total. Retrieve the signing TAN from the bidder's webmail, review the summary, and confirm "Angebot verbindlich abgeben" in the browser dialog.

Exactly one submission is allowed; submitting ends the run. Wall-clock time and LLM tokens count toward your result. Unsaved entries are lost when the portal session expires. Going back clears the later steps. A new TAN request invalidates the previous TAN; each TAN is valid for five minutes.

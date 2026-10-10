# 07 · Workstreams, Schnittstellen, Meilensteine

5 Personen, 5 Branches (von Codex angelegt, aktuell alle = Starter-Kit). Jede Person besitzt einen Bereich; Kontakt
über die Schnittstellen unten. Kickoff-Prompts zum Einfügen in Codex/Claude: `docs/prompts/<branch>.md`.

## Reihenfolge der ersten 60–90 Minuten

1. **orchestrator** zieht das Starter-Kit nach `agent/` um, legt das Paket-Skelett (Ordner aus `AGENTS.md` §3) +
   `facts.py`-Stub an und merged nach `main` (≤ 30 min). Alle anderen rebasen danach.
2. Parallel sofort startbar (ohne Abhängigkeit): browser-tools (Serializer + Guards gegen Fixtures), pricing
   (Parser + Pricing mit synthetischen Tests), tender-spec & erp-mail (Fact-Schemas + Missions-Prompts + Extraktion
   gegen Fixtures).
3. **MVP-0 bis ~T+2 h**: End-to-End-Durchstich mit echter Abgabe im Test-Run (auch mit falschen Preisen) — sichert
   Eligibility und zeigt Punkte je Feld.

## Schnittstellen (Verträge)

```python
# agent/rfq/facts.py  (Owner: pricing-submission; Änderungen per PR + Review orchestrator)
class TaskContext(BaseModel): tender_reference: str; scenario_at: datetime; deadline_at: datetime | None
    bidder_company: str; bidder_vat: str; director_localpart: str; authorized: dict[str, str]  # Betreff -> Localpart
    submission_site: str; brief_rules_text: str
class Position(BaseModel): pos: str; short_text: str; qty: Decimal; unit: str; requirement_ref: str
class QAEntry(BaseModel): number: str; at: datetime; question: str; answer: str
class QuantityChange(BaseModel): qa_number: str; at: datetime; pos: str; qty: Decimal; unit: str
class Withdrawal(BaseModel): qa_number: str; at: datetime; withdrawn_number: str
class TechAmendment(BaseModel): qa_number: str; at: datetime; revision: str; requirement_ref: str; attribute: str; value: str
class KeyAssignment(BaseModel): qa_number: str; at: datetime; pos: str; kind: Literal["condition_group","packaging",
    "delivery","setup_pool","bonus_plan"]; key: str | None   # None = explizit „kein …“
class SetupCost(BaseModel): qa_number: str; at: datetime; pos: str; amount_eur: Decimal
class SpecRevision(BaseModel): id: str; date: date | None; status: Literal["current","superseded","unknown"]
class Requirement(BaseModel): revision: str; ref: str; bore_mm: Decimal; stroke_mm: Decimal; pressure_bar: Decimal
    mounting: str; certification: str | None; catalog_group: str; usable_stroke: bool; end_reserve_key: str | None
    pieces_per_set: int | None
class Material(BaseModel): number: str; catalog_group: str; bore_mm: Decimal; stroke_mm: Decimal; pressure_bar: Decimal
    mounting: str; certification: str | None; active: bool; carton_size: int | None; allocation_mass_g: Decimal | None
    surcharge_mass_g: Decimal | None; index_key: str | None; lead_stock_weeks: int; lead_production_weeks: int
    free_stock: Decimal
class PriceCondition(BaseModel): material: str; valid_from: datetime; valid_to: datetime; currency: str
    fx_key: str | None; price_unit_qty: Decimal; scales: list[tuple[Decimal, Decimal]]   # (ab Menge, Preis je Preiseinheit)
class MailRecord(BaseModel): id: str; folder: str; subject: str; sender: str; at: datetime; reference: str | None
    body: str; is_draft: bool
# + interpretierte Mail-Inhalte: DiscountApproval, ClauseRule, EndReserve, PackagingTariff, DeliveryCommitment,
#   FxRate, SetupPool, BonusPlan, MetalIndex  (je mit source_mail_id + quote)
class Evidence(BaseModel): ctx: TaskContext; facts: list[...]   # Blackboard-Snapshot → Resolver
```

- Explorer → Blackboard: `report_facts(list[Fact])` (validiert gegen obige Modelle).
- Resolver: `resolve(evidence) -> Resolution` ; Pricing: `price(resolution) -> Answer(+trace)` ;
  Submit: `submit(answer, sessions) -> Receipt`.
- Alle Datetimes tz-aware UTC; Geld/Mengen `Decimal`.

## Workstream-Details

### 1 · `feat/agent-orchestrator` — Orchestrierung & LLM
- Paket-Layout `agent/`, `agent.json` (`browsers: 4`, `models: ["qwen"]`), `main.py`, `contract.py` (Run/Scouting,
  `seconds_left`, eigener User-Agent).
- `llm/client.py`: Qwen-Client (Timeout 360 s, Retry mit Backoff auf 5xx/Timeout, Thinking an/aus), optional
  BYO-Provider per Env, Token/Kosten-Logging je Call. `llm/loop.py`: Tool-Loop, Schema-Validierung, Loop-Erkennung,
  Budget, Deadline.
- `agents/orchestrator.py`: Phasen P0–P4, Blackboard, Dispatch/Re-Dispatch, Deadline-Wächter (−6/−4 min), Abbruch.
- `agents/explorer.py` + System-Prompt-Rahmen (Missionen kommen von 3/4).
- Logging-Format, `--out`, `--dry-run`; Integrationstest mit Fake-Browser/Fake-LLM.

### 2 · `feat/browser-tools` — Browser-Robustheit
- `browser/session.py` (Connect lokal/CDP, Handler aus `docs/05`), `observe.js`/`observe.py` (Serializer-Spec),
  `actions.py`, `guards.py` (Overlay, Consent, Login, Error-Retry, Gate, Settle), `documents.py` (CSV/TXT/HTML/ZIP/PDF).
- `dev/fixtures/public-1` pflegen, Fixture- + Perturbations-Tests.
- Messgröße: Observation-Tokens pro Seite, Fehlklick-Rate, Zeit pro Aktion.

### 3 · `feat/tender-specification` — Vergabe & Spezifikation
- Missionen `tender_core`, `clarifications`, `specification` (Prompts in `llm/prompts/`, site-agnostisch).
- Interpretations-Prompts: Q&A-Antwort → {QuantityChange | Withdrawal | TechAmendment | KeyAssignment[] | SetupCost |
  irrelevant} mit Zitat; Spezifikation → Requirement inkl. nutzbarer Weg/Satz.
- Teilnahme bekunden (Expression of Interest), Unterlagen inkl. ZIP/iframe, Revisionen + Status.
- Tests: Extraktion gegen Fixtures (LLM-Aufrufe gegen lokalen Proxy, Ergebnis-Snapshots in `dev/`).

### 4 · `feat/erp-mail-evidence` — ERP & Mail
- Missionen `materials` (Suche per Kataloggruppe/Merkmalen, MM03-Tabs, MMBE bis Lagerort/frei verwendbar, VK13 alle
  Konditionssätze + Staffeln) und `approvals` (Suchstrategie Referenz + Betreff, Inbox **und** Archiv, alle Seiten,
  Original hinter Gates, Entwürfe ignorieren, TAN-Lesen für Submit).
- Interpretations-Prompts für Mail-Prosa → DiscountApproval (inkl. Bedingung), ClauseRule (Scope-Enum), EndReserve,
  PackagingTariff, DeliveryCommitment, FxRate, SetupPool (+Verteilregel), BonusPlan (Bänder +Verteilregel),
  MetalIndex (Fenster).
- `tan.py`-Teil „TAN aus Mail lesen“ zusammen mit Workstream 5.

### 5 · `feat/pricing-submission` — Regeln, Rechnen, Abgabe
- `facts.py` (Vertrag, s. o.), `rules/parse.py`, `rules/resolve.py`, `rules/pricing.py`, `rules/validate.py` exakt
  nach `docs/04`; vollständige Unit-Tests.
- `submit/configurator.py`: LLM-geführt, aber werte-getrieben; deutsches Zahlenformat; Fallen (Nebenangebot vorbelegt,
  Position hinzufügen, AGB, Brutto, versteckter Total); Verifikation je Schritt; Teil-Abgabe nur als Notfall.
- `submit/tan.py`: TAN anfordern → Mail-Session liest neueste TAN (nach Anforderungszeit) → eintragen →
  `confirm` akzeptieren → Quittung loggen.
- `dev/oracle/public-1.json` + `dev/eval/score.py` + `dev/check_no_leaks.py` (zusammen mit allen).

## Meilensteine (Vorschlag, anpassen an Event-Zeitplan)

| Zeit | Meilenstein | Verantwortlich |
| --- | --- | --- |
| T+0:30 | `agent/`-Layout + `facts.py` auf `main` | 1, 5 |
| T+1:30 | Serializer + Guards bestehen Fixture-Tests; Pricing-Unit-Tests grün | 2, 5 |
| T+2:00 | **MVP-0**: Test-Run mit signierter Abgabe (eligible) | alle |
| T+3:30 | **MVP-1**: Mengen/Material/Basis/Lieferzeit korrekt im Test-Run | 3, 4, 5 |
| T+5:00 | **v1**: ≥ 95 Punkte, < 10 min; erste Scored-Submission | alle |
| danach | v2: Perturbations-Robustheit, Speed/Kosten, Best-of-3 aktivieren, Repo auf Teamseite verlinken | alle |

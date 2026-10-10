# 02 · Zielarchitektur

## Designziele (in dieser Priorität)

1. **Korrekt auf unbekannten Sites** (privater Datensatz): keine Site-Skripte, Missionen nach Informationsbedarf,
   robuste generische Browser-Schicht, deterministische Regeln.
2. **Immer eine gültige, signierte Abgabe** vor Ablauf (Deadline-Wächter, Teil-Abgabe statt nichts).
3. **Schnell & billig**: 4 Browser parallel, Qwen ohne Thinking für Navigation, kleine Observations, Prompt-Caching.
4. **Elegant & reviewbar**: klar getrennte Schichten, kleine Dateien, nachvollziehbare Logs.

## Schichten

```
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ main.py  →  contract.load_run()  →  Orchestrator.run(task)                                 │
├──────────────────────────────┬────────────────────────────────────────────────────────────┤
│ Orchestrator (Python)        │ Phasen, Deadline, Blackboard, Missions-Dispatch, Re-Dispatch │
├──────────────────────────────┼────────────────────────────────────────────────────────────┤
│ Explorer-Agent (LLM-Loop)    │ 1 pro Browser/Site; Tools (s. u.); berichtet Facts           │
│ Submit-Agent (LLM-Loop)      │ Konfigurator + TAN; Code verifiziert jedes Feld              │
├──────────────────────────────┼────────────────────────────────────────────────────────────┤
│ Browser-Layer (Python+JS)    │ observe (Serializer), actions, guards, documents             │
├──────────────────────────────┼────────────────────────────────────────────────────────────┤
│ Rules (reines Python)        │ parse → resolve → pricing → validate  ⇒ Answer + Trace        │
└──────────────────────────────┴────────────────────────────────────────────────────────────┘
```

## Phasen & Zeitbudget (Scored-Run = 20 min; Ziel ≤ 8 min)

| Phase | Was | Parallelität | Ziel |
| --- | --- | --- | --- |
| P0 Brief | LLM extrahiert `TaskContext` aus `brief` (Referenz, Stichtag+TZ, Deadline, Firma, USt-Id, Direktor-Mailbox-Localpart, Bieter-Domain-Hinweis, autorisierte Betreff/Absender-Paare, Abgabeanweisung). Regex-Gegencheck für Referenz/Datum. | 1 Call | < 15 s |
| P1 Login & Orientierung | Je Browser eine Site: Login, Cookie-Wall, Gates, Overlays (Guards). Explorer erstellt „Site-Map light“: was gibt es hier? | 4 | < 60 s |
| P2 Evidenz | Explorer arbeiten Missionen ab (s. u.). Abhängige Missionen warten auf Blackboard (z. B. ERP braucht Kataloggruppen/Merkmale; Mail braucht Keys). Fehlende Facts werden an andere Sites re-dispatcht. | 4 | < 4 min |
| P3 Auflösen & Rechnen | Resolver + Pricing deterministisch; Konsistenz-Checks; optional LLM-Audit der Evidenztabelle (1 Call, Thinking an). | – | < 30 s |
| P4 Abgabe | Submit-Agent im Vergabeportal; TAN erst ganz am Ende anfordern, neueste TAN lesen, `confirm` akzeptieren. | 1–2 | < 2 min |

**Deadline-Wächter**: bei `expires_at − 6 min` werden laufende Missionen abgebrochen, bei `− 4 min` startet P4 mit dem
aktuellen Stand (fehlende Werte = beste Schätzung bzw. leer bei optionalen Feldern). Keine Antwort = 0 Punkte.

## Missionen statt Site-Skripte

Eine Mission beschreibt **welche Information** gebraucht wird, mit Erkennungsmerkmalen und Qualitätskriterien — nicht
wo sie liegt. Jede Mission wird zuerst der Site gegeben, auf der sie am wahrscheinlichsten liegt (Heuristik aus
Site-Beschreibung/Startseite, vom LLM entschieden), bei „nicht gefunden“ den anderen Sites.

| Mission | Liefert (Fact-Typen) | Abhängigkeiten |
| --- | --- | --- |
| `tender_core` | `TenderInfo` (Referenz exakt, Titel, Auftraggeber, Frist + TZ), `Position[]` (Nr, Kurztext, Menge, Einheit, Anforderungsreferenz) | Referenz aus Brief |
| `clarifications` | `QAEntry[]` (Nr, Datum ISO, Frage, Antwort-Rohtext) + strukturiert: `QuantityChange`, `Withdrawal`, `TechAmendment`, `KeyAssignment` (Konditionsgruppe, Verpackungstarif, Terminschlüssel, Rüstpool, Bonusplan), `SetupCost` | Positionen |
| `specification` | `SpecRevision[]` (id, Datum, Status), `Requirement[]` der **gültigen** Revision (Ref, Bohrung, Hub, Druck, Befestigung, Zertifizierung, Kataloggruppe, nutzbarer-Weg-Flag + Endreserve-Key, Stück je Satz) | Referenz |
| `materials` | `MaterialCandidate[]` je Kataloggruppe: Nr, Merkmale, Status, Packinhalt, Umlagegewicht, Zuschlagsmasse, Preisindex-Key, Lieferzeiten (Lager/Produktion), freier Bestand, `PriceCondition[]` (gültig von/bis, Währung, FX-Key, Preiseinheit, Zahlenformat, Staffeln) | Kataloggruppen (+ Merkmale) |
| `approvals` | `MailRecord[]` (Betreff, Absender, Datum ISO, Referenz exakt, Ordner, Rohtext) für: Rabattfreigabe (Direktor), Konditionsregeln, Technikfreigabe, Verpackungstarife, Terminfreigabe, Devisenfixing, Rüstpools, Projektbonus, Preisindizes | Referenz, Keys |
| `submission` | Konfigurator-Zustand, TAN | Antwort fertig |

Das LLM interpretiert Freitext (Q&A-Antworten, Mail-Prosa, Klauseltexte) in **strukturierte Facts mit Zitat**
(`quote`), Python prüft und rechnet. Für Prosa-Regeln (z. B. „Rabatt nur auf Grundpreis“) mappt das LLM auf Enums
(`DiscountScope = base_only | base_and_surcharge | none`, `AllocationRule = floor_largest_remainder_asc_id | …`)
— mit Zitat, damit Fehler im Log sichtbar sind.

## Blackboard & Facts

```python
class Provenance(BaseModel):
    site: str; url: str; locator: str | None; quote: str      # Rohtext, aus dem der Wert stammt
    observed_at: float                                         # Laufzeit
class Fact(BaseModel):
    kind: str            # z. B. "quantity_change", "mail_record", "price_condition"
    key: str | None      # z. B. Positionsnummer, Material, Schlüssel
    value: dict          # typisiert je kind (pydantic-Subklassen in facts.py)
    record_date: datetime | None   # immer tz-aware UTC
    reference: str | None          # exakt wie auf der Seite (für Exact-Match)
    sender: str | None
    prov: Provenance
```

- Facts sind **append-only**; der Resolver wählt (neuester datierter ≤ Stichtag, exakte Referenz, autorisierter
  Absender, nicht zurückgezogen). Nie im Explorer „überschreiben“.
- Explorer können `ask_blackboard(kind, key)` aufrufen (z. B. ERP-Explorer: „welche Kataloggruppen gibt es?“) und
  warten bis zu N s.
- Fehlende Facts nach P2 → gezielter Re-Dispatch (Mission + was fehlt + was schon bekannt ist).

## Explorer-Agent (generischer LLM-Tool-Loop)

System-Prompt (site-agnostisch): Rolle, Glossar (`docs/09-glossary-de.md`), Anti-Fallen-Regeln, Mission, bekannte
Facts, Budget. Pro Schritt bekommt das Modell die **Observation** (Serializer-Text, gekürzt) + letzte Aktion + Ergebnis.

Tools (JSON-Schema; Refs stammen aus der aktuellen Observation):

| Tool | Zweck | Code-seitige Absicherung |
| --- | --- | --- |
| `observe(scope?)` | aktuelle Seite (alle Frames) als Text | Serializer, Diff zur letzten Observation |
| `click(ref)` / `open_link(ref)` | Klick bzw. Navigation zum `href` | Overlay-Guard vorher, Warten auf Netz-Idle, Popup-Fang |
| `fill(ref, text)` / `select(ref, value)` / `check(ref, on)` / `set_range(ref, value)` | Formulare | Verifikation durch Re-Read |
| `expand(ref)` / `hover(ref)` | `details`, Menüs, Tooltips | – |
| `read_document(ref|url)` | vom Modell gewähltes Dokument (iframe, CSV, TXT, ZIP, HTML) als Text | Download über Browser-Context (Cookies), ZIP entpacken |
| `paginate(ref_next, max_pages)` | vom Modell gewählte Ergebnisliste vollständig lesen | nur ab gewählter Liste (erlaubter Helper) |
| `search(ref_input, query)` | Suchfeld füllen + absenden | – |
| `report_facts([...])` | strukturierte Facts mit Zitat melden | pydantic-Validierung, Fehler zurück ans Modell |
| `ask_blackboard(kind, key?)` | bekannte Facts abfragen | – |
| `done(summary, missing[])` | Mission beenden | – |

Budget je Mission (Schritte/Zeit), Schleifen-Erkennung (gleiche Aktion+Observation 3× → Hinweis/Abbruch).
Navigation mit Qwen **ohne Thinking**; Extraktions-Schritte (`report_facts` nach langen Dokumenten) **mit Thinking**.

## Resolver & Pricing (deterministisch)

`rules/resolve.py`: Facts → `ResolvedPosition` je Position (finale Stückzahl, gültige Anforderung inkl. Nachträgen +
Endreserve, gewähltes Material, Kondition, Schlüssel) + Order-Ebene (Rabatt inkl. Bedingung). `rules/pricing.py`
rechnet exakt nach `docs/04-business-rules.md`. `rules/validate.py`: Schema, Arithmetik, Plausibilität (z. B. Preis
> 0, Menge > 0, jede Position genau einmal, Material aktiv & Merkmale exakt gleich). Jede Regelentscheidung wird mit
Begründung geloggt (`trace`), damit Test-Run-Punkte auf Ursachen zurückführbar sind.

## Submit-Agent

- LLM navigiert den Konfigurator (Site-agnostisch!), aber jede Eingabe kommt aus der fertigen Antwort; Code prüft nach
  jedem Schritt per Re-Read, dass die Werte stehen (Zahlenformat der Seite: deutsch `1.234,56`).
- Fallen: vorausgewählte falsche Optionen (Nebenangebot), „Position hinzufügen“ (Extra-Positionen kosten Punkte),
  „Position auslassen“, „Es gelten unsere AGB“ (nicht anhaken), Bruttosumme-Anzeige, versteckte Köder-Werte.
- TAN: erst auf dem letzten Schritt anfordern → Mail-Explorer (eigener Browser, bereits eingeloggt) holt **neueste**
  TAN-Mail für exakt diese Referenz, Zeitstempel nach Anforderung → eintragen → `page.once("dialog", accept)` →
  Abgeben → Quittung lesen & loggen.

## Modellstrategie

| Aufgabe | Modell | Hinweise |
| --- | --- | --- |
| Navigation (Tool-Calls) | Qwen, Thinking aus | kleine Observations, Prompt-Prefix stabil halten (Caching) |
| Extraktion/Interpretation (Prosa → Facts) | Qwen, Thinking an | strenges JSON-Schema, Zitate erzwingen |
| Ja/Nein-Checks (richtiges Dokument? aktuelle Revision?) | optional Clef `noul` | nur wenn messbar besser |
| Audit vor Abgabe | Qwen Thinking (optional Frontier-Modell per Flag `RFQ_AUDIT_MODEL`) | Diff zu Resolver nur loggen + gezielte Nachprüfung |

Alle Modell-IDs/Provider per Env-Flag umschaltbar; A/B über Test-Runs entscheiden (Qualität zuerst, dann Kosten×Zeit).

## Generalisierung für den privaten Datensatz

- Keine URLs/Pfade/Labels hartkodieren; Guards erkennen Muster (Cookie-Consent, Login-Formular, Fehlerseite mit
  „erneut versuchen“, Slider-Gate, Modale) über Rollen/Text-Heuristiken + LLM-Fallback.
- Informationen können auf anderen Sites liegen (z. B. Q&A in Mail, Rabatt im Portal) → Missionen sind Site-unabhängig,
  Re-Dispatch bei „nicht gefunden“.
- Andere Zahlen-/Datumsformate, andere Zeitzonen, andere Einheiten (Satz/Paar/Set) → `parse.py` + LLM-Normalisierung
  mit Locale-Angabe.
- Perturbations-Tests (`docs/06-testing.md`) simulieren „andere Software“.

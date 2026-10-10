# 04 · Geschäftsregeln & Rechenweg (Spezifikation für `rules/`)

Quelle: Brief (Regeln 1–8 + „Additional rules“), Challenge-Seite (Regeln 01–29), Mail-Prosa aus public-1, Formular-
Hinweise im Konfigurator. Die Regeln gelten laut Plattform **identisch für public und privat** → wir implementieren
sie deterministisch. Die *Werte* (Keys, Prozente, Prosa-Varianten) kommen zur Laufzeit von den Sites.

Punkte mit ⚠️ sind Interpretationen, die per Test-Run (Punkte je Feld) bestätigt werden müssen.

## Notation

`q_p` finale Stückzahl der Position p · `d` Order-Rabatt (Bruch, z. B. 0.05) · `r2(x)` = `x.quantize(Decimal("0.01"),
ROUND_HALF_UP)` · alle Werte `Decimal`, nie `float`. Stichtag `T` = Szenariodatum aus Brief (UTC).

## A. Auswahl der gültigen Records („Authority and time“)

Für jeden Schlüssel (Rabatt, Konditionsregeln, Technikfreigabe, Verpackungstarif, Terminfreigabe, Devisenfixing,
Rüstpool, Projektbonus, Preisindex):
1. Nur Records mit **exakt** der Tender-Referenz (String-Gleichheit nach Normalisierung von Zero-Width/Whitespace;
   `…-A` ≠ Referenz).
2. Nur **autorisierte Absender**: Localpart laut Brief + **Bieter-Mailbox-Domain** (aus Webmail-Identität bzw.
   Brief). Paare aus Brief: Technikfreigabe/technik, Verpackungstarife/logistik, Terminfreigabe/logistik,
   Devisenfixing/finanzen, Rüstpools/kalkulation, Projektbonus/finanzen, Konditionsregeln/kalkulation,
   Preisindizes/kalkulation, Rabatt/leitung (Direktor). ⚠️ Privat können Localparts anders heißen → immer aus dem
   **Brief des Runs** parsen, nicht hardcoden.
3. Nur Records mit Datum **≤ T** (zukunftsdatiert = ungültig). Entwürfe/„nicht versandt“/Vorschau zählen nie.
4. **Neuester** Record gewinnt (pro Schlüssel bzw. pro Eintrag „je Schlüssel“). Ein neuerer Record ersetzt den älteren
   auch dann, wenn seine Bedingung scheitert („a failed quantity condition does not revive it“).

## B. Menge (Regeln 1, 13, 17)

1. Start: Menge + Einheit aus LV/Positionsübersicht.
2. Q&A-Mengenänderungen je Position nach **Datum** (nicht Nummer) sortieren; zurückgezogene Antworten
   („zurückziehen“, „ersatzlos zurückgezogen“, Referenz per Nummer) entfernen; jüngste verbleibende gilt, sonst LV.
3. Einheit `Satz` (o. ä.): × „Stück je Satz“ aus der **maßgeblichen** Spezifikationsrevision → `q_p` in Stück.
4. `q_p` ist die Bid-Menge. Kartonrundung ändert `q_p` nie.

## C. Anforderung & Material (Regeln 2, 3, 14, 20)

1. Spezifikationsrevision mit Status „gültig/aktuell“ (marked current) wählen — nicht die zuerst angezeigte, nicht nach
   Datum raten.
2. Anforderung der Position (Referenz aus LV, z. B. `A-2`) aus dieser Revision lesen: Bohrung, Hub, Druck,
   Befestigung, Zertifizierung, Kataloggruppe, ggf. „nutzbarer Weg“ + Endreserve-Key, ggf. Stück je Satz.
3. Technische Nachträge aus Q&A, die **genau diese Revision und Anforderung** nennen: je Merkmal jüngster datierter
   Nachtrag ersetzt nur dieses Merkmal.
4. „Nutzbarer Weg“: `Hub_ERP = Hub_nach_Nachträgen + 2 × Endreserve(Key, Revision)` aus Technikfreigabe.
5. ERP: Kataloggruppe suchen (≤ 20 Treffer), Kandidaten mit **exakt** gleichen Bohrung, Hub, Druck, Befestigung,
   Zertifizierung („keine gefordert“ ≙ ohne Zertifizierung ⚠️) und Status **aktiv/freigegeben** (gesperrt zählt nicht).
6. Kein passendes aktives Material für **irgendeine** Position ⇒ `decision = no_bid`, `positions = []`,
   `total_net = 0`, Rabatt: bedingte Freigabe ⇒ 0 %. ⚠️ Mehrere passende aktive Materialien: sollte nicht vorkommen →
   loggen, das mit Bestand/gültiger Kondition wählen.

## D. Basispreis (Regeln 4, 22)

1. Konditionssatz des Materials, dessen Gültigkeit `T` enthält (beide Enden inklusive; Zeitzone der Anzeige beachten).
2. Staffel: größte „ab Menge“ ≤ `q_p` (**pro Position**, nicht pro Material-Summe).
3. Preis je Preiseinheit → je Stück: `price / price_unit_qty` (z. B. „100 ST“). Zahlenformat laut Tag!
4. Währung ≠ EUR: × Kurs „EUR je Währungseinheit“ aus Devisenfixing (FX-Key aus Kondition).
5. `base_p = r2(price_per_piece × fx)` — dieser gerundete EUR-Basispreis wird überall weiterverwendet
   (Rabattschwelle, Bonusbasis, Endpreis). ⚠️ Bei EUR ebenfalls `r2` (Preiseinheit 100 kann 3+ Nachkommastellen
   erzeugen).

## E. Rabatt (Regeln 5, 16)

1. Neueste Direktor-Mail (≤ T, exakte Referenz, `leitung@<bieterdomain>`), die einen Rabatt **genehmigt**.
   „Angebotsprüfung“ o. ä. ohne Genehmigung zählt nicht als Freigabe ⚠️ (zählt sie als „neueste“? Nein — nur
   Freigaben werden verglichen).
2. Bedingte Freigabe („sofern unrabattierter Nettoauftragswert mindestens X“): `V = r2(Σ q_p × base_p)` (ohne
   Zuschläge, Rüstkosten, Verpackung). `V ≥ X` ⇒ Prozentsatz, sonst **0 %** (keine ältere Freigabe reaktivieren).
3. `discount_percent` im Answer = dieser Order-Prozentsatz (auch wenn einzelne Positionen per Klausel nicht rabattiert
   werden). Bei `no_bid` mit bedingter Freigabe: 0.

## F. Konditionsgruppen (Regel 12)

Q&A kann Position → Konditionsgruppe zuordnen; Prosa in neuester `Konditionsregeln`-Mail (kalkulation@) je Gruppe:
- `base_only` (Default ohne Gruppe): Rabatt nur auf Basispreis.
- `base_and_surcharge`: Rabatt auf Basispreis + gewichtsabhängigen Materialzuschlag.
- `none` (Festpreis): kein Rabatt.
Rüstkosten (normal + Pool), Verpackung und Bonus werden **nie** rabattiert. Gruppe gilt je Position (auch wenn zwei
Positionen dasselbe Material haben).

## G. Zuschläge (Regeln 25, 28; Konfigurator-Hinweis)

- Materialzuschlag je Stück: `mass_p = Zuschlagsmasse_g / 1000 × Index_EUR_per_kg`. Index: neueste exakte
  `Preisindizes`-Mail (kalkulation@); bei mehreren Gültigkeitsfenstern das Fenster, das `T` enthält (inklusive).
  Preisindex-Key steht am Material (ERP). ⚠️ Umlagegewicht ≠ Zuschlagsmasse!
- Normale Rüstkosten `S_p` aus Q&A („einmalig … EUR, gleichmäßig auf die endgültige Stückzahl“), sonst 0.
- Rüstpool (Q&A nennt Pool-Mitglieder per Key): Poolbetrag `P` aus neuester `Rüstpools`-Mail. Gewicht
  `w_p = q_p × Umlagegewicht_p`. Verteilung in Cent nach Mail-Regel (public-1: abrunden, Restcents an größte
  Nachkommareste, Gleichstand → aufsteigende Positionskennung) → `A_p`.
- Report: `surcharge_per_unit_p = r2(mass_p + (S_p + A_p) / q_p)` — **vor Rabatt**, ohne Verpackung und Bonus.

## H. Verpackung (Regel 23)

Q&A ordnet Verpackungstarif-Key zu; Tarif (EUR je angefangenem Karton) aus neuester `Verpackungstarife`-Mail;
Packinhalt aus ERP. `cartons_p = ceil(q_p / packinhalt_p)` (je Position getrennt), `C_p = cartons_p × tarif`.
Report: `packaging_per_unit_p = r2(C_p / q_p)`. Nie rabattieren. Kein Tarif zugeordnet ⇒ 0.

## I. Projektbonus (Regel 26)

Q&A ordnet Bonusplan-Key zu (Mitglieder). Basis `G = Σ_{p∈M} q_p × base_p` (EUR, vor Rabatt, ohne Zuschläge/
Verpackung). Marginale Bänder aus `Projektbonus`-Mail: jeder Satz nur auf seinen Bandanteil. `B = r2(Σ Bandgutschriften)`.
Verteilung auf Mitglieder proportional zu `q_p × base_p` in Cent (abrunden, Restcents an größte Reste, Gleichstand
aufsteigende Positionskennung) → `B_p`. Report `rebate_total_p = B_p` (**ganze Position**, nicht je Stück);
Nichtmitglieder `0.00`.

## J. Endpreis & Summe (Regeln 7, 27)

```
disc_base_p = base_p × (1 − d)  wenn scope ∈ {base_only, base_and_surcharge}, sonst base_p
disc_mass_p = mass_p × (1 − d)  wenn scope == base_and_surcharge,          sonst mass_p
unit_price_p = r2( disc_base_p + disc_mass_p + (S_p + A_p + C_p − B_p) / q_p )   # ungerundete Komponenten, 1× runden
total_net    = r2( Σ q_p × unit_price_p )
```
Gerundete Report-Felder (Basis, Zuschlag, Verpackung) **nie** in die Endformel zurückspeisen.

## K. Lieferzeit (Regeln 6, 15, 24)

1. Gepoolter Bedarf je Material: `Q_m = Σ q_p` aller Positionen mit diesem Material.
2. Terminzusage (Q&A ordnet TZ-Key je Position zu; Details aus neuester `Terminfreigabe`-Mail): gilt, wenn Artikel =
   gewähltes Material **und** `Q_m ≤ Grenze` (inklusive) ⇒ zugesagte Wochen. Kann je Position verschieden ausfallen,
   auch bei gleichem Material.
3. Sonst Bestandsregel: `Q_m ≤ freier Bestand` (ohne Prüf-/Sperrbestand) ⇒ Lieferzeit ab Lager, sonst
   Produktionslieferzeit — für **alle** Positionen dieses Materials gleich (keine Teilallokation).

## L. Abgabe-Felder

| Answer-Feld | Konfigurator (public) | Format |
| --- | --- | --- |
| `position` | Überschrift „Position 01.04“ | exakt wie im LV |
| `material_number` | Kaskade + Katalognummer | – |
| `quantity` | Menge (Stk) | Ganzzahl |
| `unit_price` | Einzelpreis netto (EUR) | deutsch `1.234,56` |
| `base_unit_price` / `surcharge_per_unit` / `packaging_per_unit` / `rebate_total` | Preisaufbau-Felder | deutsch, `0,00` für bekannte Null |
| `lead_time_weeks` | Lieferzeit-Select | Wochen |
| `discount_percent` | Rabatt (%) | z. B. `5` |
| `total_net` | Nettosumme (EUR) | deutsch |

## M. Synthetischer Rechen-Testfall (für `tests/test_pricing.py`, keine Szenario-Werte)

Position X: `q=10`, Basis 100.00 EUR/ST (Preiseinheit 1), `d=10 %`, Scope `base_and_surcharge`, Zuschlagsmasse 500 g,
Index 2.00 EUR/kg ⇒ `mass=1.00`; Rüstkosten 25.00, Pool-Anteil 4.99; Packinhalt 4, Tarif 3.00 ⇒ 3 Kartons = 9.00;
Bonus 12.34.
- `surcharge_per_unit = r2(1.00 + (25.00+4.99)/10) = r2(3.999) = 4.00`
- `packaging_per_unit = r2(9.00/10) = 0.90`
- `unit_price = r2(90.00 + 0.90 + (25.00 + 4.99 + 9.00 − 12.34)/10) = r2(90.90 + 2.665) = r2(93.565) = 93.57`
- `total` (nur X) `= 935.70`.
Weitere Tests: Half-up bei `x.xx5`, Cent-Verteilung mit Gleichstand, marginale Bänder an Bandgrenzen, Schwelle exakt
gleich (inklusive), zukunftsdatierter Record, zurückgezogene Mengenänderung, Satz-Umrechnung, USD-Kondition mit
Preiseinheit 100 und en-US-Format.

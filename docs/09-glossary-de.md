# 09 · Glossar Deutsch → Bedeutung (für Runtime-Prompts geeignet)

Enthält **keine Szenario-Werte**, nur Begriffe. Darf als Ganzes in System-Prompts in `agent/rfq/llm/prompts/`.

## Vergabe / Ausschreibung
| Begriff | Bedeutung |
| --- | --- |
| Vergabeportal, Ausschreibung, Bekanntmachung, Verfahrensakte | Tender-Portal, Tender, Notice, Tender file |
| Aktenzeichen, Vergabenummer, Referenz | Tender reference (exakt übernehmen) |
| Auftraggeber, Vergabestelle | Buyer / contracting authority |
| Angebotsfrist | Bid deadline (Zeitzone beachten) |
| Leistungsverzeichnis (LV), Positionsübersicht, Position, Kurztext | Bill of quantities, line items |
| Menge, Einheit, Stk/ST/Stück, Satz | Quantity, unit, piece, set |
| Leistungsbeschreibung, Lastenheft, Spezifikation, Datenblatt | Statement of work / technical specification |
| Bieterfragen, Rückfrage, Antwort der Vergabestelle, Klarstellung | Bidder Q&A / clarifications |
| zurückziehen, ersatzlos zurückgezogen, Berichtigung | withdraw (an earlier answer) |
| Technischer Nachtrag | technical amendment (applies only to named revision/requirement/attribute) |
| Teilnahme bekunden, Interessenbekundung | expression of interest (unlocks documents/submission) |
| Unterlagen, Sammelakte, ZIP | documents, bundle |
| Angebotsabgabe, Hauptangebot, Nebenangebot, Verzichtserklärung | submission, main bid (= bid), alternative bid (NOT wanted), no-bid declaration (= no_bid) |
| Bieterdaten, Firmenname, USt-IdNr. | bidder data, company name, VAT id |
| Einzelpreis netto, Nettosumme, Bruttosumme inkl. USt | net unit price, net total, gross incl. VAT (never submit gross) |
| Vergabeunterlagen anerkannt | acknowledge tender documents (tick) |
| Es gelten unsere AGB | our own T&Cs apply (do NOT tick) |
| Position auslassen (Teilpunkte), Position hinzufügen | skip position (partial credit), add extra position (avoid) |
| Signatur-TAN, TAN anfordern, verbindlich abgeben | signing TAN, request TAN, submit binding bid |

## Spezifikation / Technik
| Begriff | Bedeutung |
| --- | --- |
| Revision, Revisionshistorie, Freigabestand, gültig / aktuell / ersetzt | revision, history, release status, current / superseded |
| Anforderung (A-…), Originalwortlaut | requirement, original wording |
| Bohrung (mm), Hub / Stellweg (mm), Betriebsdruck (bar), Befestigung, Zertifizierung, Prüfvermerk | bore, stroke, pressure, mounting, certification |
| Schwenkauge, Flansch, Fußbefestigung | clevis/eye, flange, foot mounting |
| nutzbarer Weg, Nennweg, Endreserve (je Ende) | usable stroke, nominal stroke, end reserve per end |
| Kataloggruppe | catalogue group (ERP search key) |
| Lieferumfang: 1 Satz = N Stück | pieces per set |
| Dokumentenüberlassung, Leseberechtigung, Belegnummer, Archivkopie, Vorschau | document release form, read permission, receipt number, archive copy (wrong), preview (wrong) |

## ERP
| Begriff | Bedeutung |
| --- | --- |
| Transaktion, MM03 / MMBE / VK13, F4-Suchhilfe | transaction codes: material display / stock overview / condition display, search help |
| Materialnummer, Bezeichnung, Basismengeneinheit | material number, description, base unit |
| Materialstatus 01 freigegeben / 03 gesperrt | active / blocked |
| Packinhalt (Stück/Karton) | pieces per carton |
| Umlagegewicht (nicht Zuschlagsmasse) | allocation weight (for setup pool) |
| Zuschlagsmasse | surcharge mass (for metal surcharge) |
| Preisindex, Materialteuerung | metal price index, material surcharge |
| Lieferzeit ab Lager / Produktionslieferzeit | stock lead time / production lead time |
| Physischer Bestand, frei verwendbar, Prüfbestand, gesperrt | physical stock, unrestricted (use this), inspection stock, blocked |
| Konditionsart, Konditionssatz, Gültig von/bis, Preiseinheit, Mengenstaffel, Staffel ab Menge | condition type/record, validity, price unit, quantity scale, scale from qty |
| Währung, Devisenschlüssel | currency, FX key |

## Mail / Freigaben
| Begriff | Bedeutung |
| --- | --- |
| Posteingang, Archiv, Ordner, Lesebereich, Original öffnen | inbox, archive, folder, reading pane, open original |
| Rabattfreigabe, genehmige … % Rabatt, sofern … mindestens | discount approval, conditional on min. order value |
| Angebotsprüfung, „keine Rabattfreigabe“ | review note, not an approval |
| Lokale Vorschau, nicht versandter Entwurf, Entwurfswert | local preview / unsent draft (ignore) |
| Konditionsregeln, Konditionsgruppe, Waren-Grundpreis, Festpreiskondition | clause rules, condition group, base goods price, fixed-price (no discount) |
| Technikfreigabe, Verpackungstarife (je angefangenem Karton), Terminfreigabe/Terminschlüssel/Zusage, Devisenfixing, Rüstkosten, Rüstpool, Projektbonus/Bonusplan, Preisindizes | technical release, packaging tariff (per started carton), delivery commitment, FX fixing, setup cost, shared setup pool, project bonus, metal indices |
| Restcents an größte Nachkommareste, aufsteigende Positionskennung | largest-remainder cent allocation, ties by ascending position id |
| marginale Staffel, Band | marginal bands |
| Stichtag, Szenariodatum | scenario date (cut-off) |

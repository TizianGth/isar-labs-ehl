"""Rules and pricing with synthetic data only (never values of a real scenario)."""
from datetime import datetime, timezone
from decimal import Decimal as D

from rfq.rules.parse import cert, number, when
from rfq.rules.resolve import Context, build, largest_remainder, marginal_bonus

T = datetime(2030, 3, 10, 9, 0, tzinfo=timezone.utc)
REF = "XT-2030-0001"


def test_numbers_with_locales():
    assert number("4.321,09 (de-DE)") == D("4321.09")
    assert number("512,345 (en-US)") == D("512345")
    assert number("12,345.67 (en-US)") == D("12345.67")
    assert number("7.85") == D("7.85")
    assert number("1,00") == D("1.00")
    assert number("1.234.567") == D("1234567")
    assert number("5 %") == D("5")
    assert number(None) is None


def test_dates_and_certificates():
    assert when("2030-03-10T08:00:00Z") == datetime(2030, 3, 10, 8, tzinfo=timezone.utc)
    assert when("10.03.2030, 09:00").day == 10
    assert cert("keine gefordert") == cert(None) == cert("Ohne Zertifizierung") == ""
    assert cert("EN 1234") == "EN1234"


def test_largest_remainder_ties_by_position():
    shares = largest_remainder(D("0.10"), {"02": D(1), "01": D(1), "03": D(1)})
    assert shares == {"01": D("0.04"), "02": D("0.03"), "03": D("0.03")}
    assert sum(shares.values()) == D("0.10")


def test_marginal_bonus_bands():
    bands = [{"from_eur": "0", "to_eur": "100", "rate_percent": "0"},
             {"from_eur": "100", "to_eur": "200", "rate_percent": "10"},
             {"from_eur": "200", "to_eur": None, "rate_percent": "20"}]
    assert marginal_bonus(D("250"), bands) == D("20.00")
    assert marginal_bonus(D("100"), bands) == D("0.00")


def _evidence(**overrides):
    mail = lambda topic, data, sender, at="2030-03-09T10:00:00Z", ref=REF: {"data": {
        "subject": f"{topic} {ref}", "sender": sender, "datetime_utc": at, "reference": ref,
        "is_draft_or_preview": False, "topic": topic, "data": data}}
    bb = {
        "positions": [{"data": {"positions": [
            {"position": "1.1", "quantity": "8", "unit": "Stk", "requirement_ref": "R-A"},
            {"position": "1.2", "quantity": "2", "unit": "Satz", "requirement_ref": "R-B"}]}}],
        "clarifications": [{"data": {"entries": [
            {"number": "11", "datetime_utc": "2030-03-01T10:00:00Z", "facts": [{"type": "quantity_change", "position": "1.1", "quantity": "9", "unit": "Stk"}]},
            {"number": "5", "datetime_utc": "2030-03-02T10:00:00Z", "facts": [{"type": "quantity_change", "position": "1.1", "quantity": "10", "unit": "Stk"}]},
            {"number": "12", "datetime_utc": "2030-03-03T10:00:00Z", "facts": [{"type": "quantity_change", "position": "1.1", "quantity": "99", "unit": "Stk"}]},
            {"number": "13", "datetime_utc": "2030-03-04T10:00:00Z", "facts": [{"type": "withdrawal", "withdrawn_number": "12"}]},
            {"number": "14", "datetime_utc": "2030-03-04T10:00:00Z", "facts": [
                {"type": "condition_group", "position": "1.1", "group": "G-1"},
                {"type": "setup_cost", "position": "1.1", "amount_eur": "25.00"},
                {"type": "assignment", "position": "1.1", "packaging_key": "P-1", "delivery_key": None, "setup_pool_key": None, "bonus_plan_key": None}]},
        ]}}],
        "spec_revisions": [{"data": {"revisions": [{"revision": "V1", "status_text": "ersetzt", "is_current": False},
                                                   {"revision": "V2", "status_text": "gültig", "is_current": True}]}}],
        "requirements": [
            {"data": {"revision": "V1", "requirements": [{"ref": "R-A", "bore_mm": "1", "stroke_mm": "1", "pressure_bar": "1", "mounting": "X"}]}},
            {"data": {"revision": "V2", "requirements": [
                {"ref": "R-A", "bore_mm": "40", "stroke_mm": "100", "pressure_bar": "200", "mounting": "Flansch", "certification": None, "catalog_group": "G1"},
                {"ref": "R-B", "bore_mm": "50", "stroke_mm": "200", "pressure_bar": "200", "mounting": "Flansch", "certification": None, "catalog_group": "G2", "pieces_per_set": 3}]}}],
        "materials": [{"data": {"materials": [
            {"material_number": "M-1", "catalog_group": "G1", "bore_mm": "40", "stroke_mm": "100", "pressure_bar": "200", "mounting": "Flansch",
             "certification": "none", "status_meaning": "active", "pieces_per_carton": "4", "surcharge_mass_g": "500", "price_index_key": "IX",
             "lead_time_stock_weeks": "2", "lead_time_production_weeks": "8", "unrestricted_stock": "50",
             "conditions": [{"valid_from": "2030-01-01", "valid_to": "2030-12-31", "currency": "EUR", "price_unit_qty": "1", "number_format": "de-DE",
                             "scales": [{"from_qty": "1", "price": "120,00"}, {"from_qty": "10", "price": "100,00"}]}]},
            {"material_number": "M-0", "catalog_group": "G1", "bore_mm": "40", "stroke_mm": "100", "pressure_bar": "200", "mounting": "Flansch",
             "certification": "none", "status_meaning": "blocked"},
            {"material_number": "M-2", "catalog_group": "G2", "bore_mm": "50", "stroke_mm": "200", "pressure_bar": "200", "mounting": "Flansch",
             "certification": None, "status_meaning": "active", "pieces_per_carton": "10", "lead_time_stock_weeks": "1",
             "lead_time_production_weeks": "9", "unrestricted_stock": "1",
             "conditions": [{"valid_from": "2030-01-01", "valid_to": "2030-12-31", "currency": "USD", "fx_key": "F-1", "price_unit_qty": "100",
                             "number_format": "en-US", "scales": [{"from_qty": "1", "price": "10,000"}]}]}]}}],
        "mail_list": [{"data": {"mailbox_address": "sales@bidder.example"}}],
        "mail_message": [
            mail("discount", {"is_approval": True, "percent": "10", "min_order_value_eur": "1.00"}, "boss@bidder.example"),
            mail("discount", {"is_approval": True, "percent": "50"}, "boss@bidder.example", ref=REF + "-A"),
            mail("discount", {"is_approval": True, "percent": "60"}, "boss@other.example"),
            mail("discount", {"is_approval": True, "percent": "70"}, "boss@bidder.example", at="2030-03-11T10:00:00Z"),
            mail("clause_rules", {"groups": {"G-1": "base_and_surcharge"}}, "calc@bidder.example"),
            mail("packaging", {"tariffs": {"P-1": {"eur_per_carton": "3.00"}}}, "log@bidder.example"),
            mail("fx", {"rates": {"F-1": {"currency": "USD", "eur_per_unit": "0.5"}}}, "fin@bidder.example"),
            mail("metal_index", {"indices": {"IX": [{"eur_per_kg": "9.00", "valid_from": "2030-01-01", "valid_to": "2030-03-09"},
                                                    {"eur_per_kg": "2.00", "valid_from": "2030-03-10", "valid_to": "2030-04-30"}]}}, "calc@bidder.example"),
        ],
    }
    bb.update(overrides)
    return bb


CTX = Context(tender_reference=REF, scenario=T, director_localpart="boss",
              authorized={"clause_rules": "calc", "packaging": "log", "fx": "fin", "metal_index": "calc"})


def test_full_bid():
    result = build(CTX, _evidence())
    answer = result.answer
    assert answer["decision"] == "bid" and answer["discount_percent"] == 10.0
    p1, p2 = answer["positions"]
    # 1.1: q=10 (withdrawn 99 ignored), M-1 active (M-0 blocked), scale 10 -> 100.00, index window 2.00 -> mass 1.00,
    # scope base+surcharge, setup 25.00, packaging ceil(10/4)=3 cartons * 3.00 = 9.00
    assert (p1["material_number"], p1["quantity"], p1["base_unit_price"]) == ("M-1", 10, 100.0)
    assert p1["surcharge_per_unit"] == 3.5 and p1["packaging_per_unit"] == 0.9
    assert p1["unit_price"] == 94.3        # 90 + 0.9 + (25 + 9)/10 = 94.30
    assert p1["lead_time_weeks"] == 2
    # 1.2: 2 Satz x 3 = 6 pieces, USD 10000 per 100 -> 100 USD * 0.5 = 50.00 EUR, discount on base only
    assert (p2["material_number"], p2["quantity"], p2["base_unit_price"], p2["unit_price"]) == ("M-2", 6, 50.0, 45.0)
    assert p2["lead_time_weeks"] == 9      # stock 1 < 6
    assert answer["total_net"] == round(10 * 94.3 + 6 * 45.0, 2)


def test_conditional_discount_fails_below_threshold():
    bb = _evidence()
    bb["mail_message"][0]["data"]["data"]["min_order_value_eur"] = "999999"
    assert build(CTX, bb).answer["discount_percent"] == 0.0


def test_no_bid_when_only_blocked_material():
    bb = _evidence()
    bb["materials"][0]["data"]["materials"][0]["status_meaning"] = "blocked"
    answer = build(CTX, bb).answer
    assert answer["decision"] == "no_bid" and answer["positions"] == [] and answer["total_net"] == 0

"""B03: the review page must match the bid field by field (synthetic data only)."""
from rfq.agent.review import validate_review

EXPECTED = {"tender_reference": "XT-1", "decision": "bid", "discount_percent": 5.0, "total_net": 1234.5,
            "positions": [{"position": "1.1", "material_number": "M-1", "quantity": 10, "unit_price": 100.0,
                           "base_unit_price": 105.0, "surcharge_per_unit": 1.5, "packaging_per_unit": 0.0,
                           "rebate_total": 0.0, "lead_time_weeks": 3},
                          {"position": "1.2", "material_number": "M-2", "quantity": 2, "unit_price": 117.25,
                           "base_unit_price": 120.0, "surcharge_per_unit": 0.0, "packaging_per_unit": 0.5,
                           "rebate_total": 12.34, "lead_time_weeks": 9}]}


def _page(**changes):
    page = {"tender_reference": "XT-1", "offer_type": "main", "discount_percent": "5 %", "total_net": "1.234,50 EUR",
            "positions": [{"position": "1.1", "material_number": "M-1", "quantity": "10", "unit_price": "100,00 EUR",
                           "base_unit_price": "105,00", "surcharge_per_unit": "1,50", "packaging_per_unit": "0,00",
                           "rebate_total": "0,00", "lead_time_weeks": "3 Wochen"},
                          {"position": "1.2", "material_number": "M-2", "quantity": "2", "unit_price": "117,25 EUR",
                           "base_unit_price": "120,00", "surcharge_per_unit": "0,00", "packaging_per_unit": "0,50",
                           "rebate_total": "12,34", "lead_time_weeks": "9 Wochen"}],
            "omitted_positions": [], "documents_acknowledged": True, "own_terms_declared": False}
    page.update(changes)
    return page


def test_matching_review_passes():
    result = validate_review(EXPECTED, _page())
    assert not result["blocking"] and not result["mismatches"] and not result["missing"]


def test_wrong_material_with_right_total_is_caught():
    page = _page()
    page["positions"][1]["material_number"] = "M-9"
    result = validate_review(EXPECTED, page)
    assert result["blocking"] and any("1.2 material_number" in m for m in result["mismatches"])


def test_alternative_offer_gross_total_extra_and_missing_positions_block():
    assert validate_review(EXPECTED, _page(offer_type="alternative"))["blocking"]
    assert validate_review(EXPECTED, _page(total_net="1.469,06"))["blocking"]
    page = _page()
    page["positions"].append(dict(page["positions"][0], position="1.3"))
    assert validate_review(EXPECTED, page)["blocking"]
    page = _page()
    page["positions"].pop()
    result = validate_review(EXPECTED, page)
    assert result["blocking"] and "position 1.2" in result["missing"]


def test_verified_wrong_optional_field_and_own_terms_block():
    page = _page()
    page["positions"][0]["packaging_per_unit"] = "0,10"
    result = validate_review(EXPECTED, page)
    assert result["mismatches"] and result["blocking"]
    assert validate_review(EXPECTED, _page(own_terms_declared=True))["blocking"]


def test_optional_unreadable_or_ambiguous_values_warn_without_false_mismatches():
    for raw in (None, "unavailable", "1,234", "1.234", "3 / 4"):
        page = _page()
        page["positions"][0]["packaging_per_unit"] = raw
        result = validate_review(EXPECTED, page)
        assert result["missing"] and not result["mismatches"] and not result["blocking"]
    page = _page()
    page["positions"][0]["quantity"] = "1,234"
    result = validate_review(EXPECTED, page)
    assert result["blocking"] and any("ambiguous" in m for m in result["missing"])


def test_english_and_swiss_money_readback_do_not_create_false_blocks():
    for raw in ("1,234.50", "1'234.50 (de-CH)", "1’234.50"):
        assert not validate_review(EXPECTED, _page(total_net=raw))["blocking"]
    page = _page()
    page["positions"][0]["packaging_per_unit"] = "1,234 (en-US)"
    assert validate_review(EXPECTED, page)["blocking"]


def test_non_participation_with_position_rows_blocks():
    expected = {"tender_reference": "XT-1", "decision": "no_bid"}
    assert validate_review(expected, {"tender_reference": "XT-1", "offer_type": "no_bid", "positions": []})["blocking"] is False
    assert validate_review(expected, {"tender_reference": "XT-1", "offer_type": "no_bid", "positions": [{"position": "1"}]})["blocking"]


def test_number_formats_on_the_review_page():
    from decimal import Decimal
    from rfq.agent.review import _num
    assert _num("4.321,09 EUR") == Decimal("4321.09")
    assert _num("1234.50") == Decimal("1234.50")          # decimal point from a normalising extraction
    assert _num(1234.5) == Decimal("1234.5")              # JSON number
    assert _num("1,234.50 (en-US)") == Decimal("1234.50")
    assert _num("1.234") == Decimal("1234")               # German thousands, not 1.234
    assert _num("5 %") == Decimal("5")
    page = _page(total_net="1234.50")
    assert not validate_review(EXPECTED, page)["blocking"]
    page = _page(total_net="1234.60")                     # a genuinely wrong amount is still caught
    assert validate_review(EXPECTED, page)["blocking"]

"""Missing facts must never become proof of an offer or of no_bid."""
from copy import deepcopy

from rfq.rules.parse import certification_known, merge_certification
from rfq.rules.resolve import Context, build, index_rate
from test_rules import CTX, T, _evidence


def test_unknown_certificate_is_not_an_explicit_none():
    assert not certification_known({"certification": None})
    assert not certification_known({"certification": None, "certification_known": True})
    assert certification_known({"certification": "none"})
    assert not certification_known({"certification": "none", "certification_known": False})
    assert certification_known({"certification": None}, requirement=True)  # old schema
    assert not certification_known({"certification": None, "certification_known": False}, requirement=True)


def test_unknown_candidate_cert_never_proves_no_bid():
    bb = _evidence()
    bb["materials"][0]["data"]["materials"][0]["certification"] = None
    ctx = Context(**{**CTX.__dict__, "searched_groups": {"G1", "G2"}})
    result = build(ctx, bb)
    assert result.answer["decision"] == "bid"
    assert "1.1" in result.skipped
    assert all(p["position"] != "1.1" for p in result.answer["positions"])


def test_unseen_requirement_certificate_is_incomplete():
    bb = _evidence()
    bb["requirements"][1]["data"]["requirements"][0]["certification_known"] = False
    result = build(Context(**{**CTX.__dict__, "searched_groups": {"G1", "G2"}}), bb)
    assert result.answer["decision"] != "no_bid"
    assert "1.1" in result.skipped


def test_missing_revision_is_not_assigned_current():
    bb = _evidence()
    bb["requirements"][1]["data"].pop("revision")
    result = build(CTX, bb)
    assert result.answer["positions"] == []
    assert set(result.skipped) == {"1.1", "1.2"}
    assert any("proven current revision" in t for t in result.trace)


def test_untrusted_revision_claim_is_ignored_but_validated_envelope_allowed():
    bb = _evidence()
    record = bb["requirements"][1]
    record["data"].pop("revision")
    record["data"]["validated_current_revision"] = "V2"
    assert not build(CTX, bb).answer["positions"]
    record["validated_current_revision"] = "V2"
    assert len(build(CTX, bb).answer["positions"]) == 2
    record["data"]["revision"] = "V1"  # explicit contradiction wins over envelope
    assert not build(CTX, bb).answer["positions"]


def test_missing_fx_never_treats_foreign_currency_as_eur():
    bb = _evidence()
    bb["mail_message"] = [r for r in bb["mail_message"] if r["data"]["topic"] != "fx"]
    result = build(CTX, bb)
    assert "1.2" in result.skipped
    assert all(p["position"] != "1.2" for p in result.answer["positions"])


def test_partial_price_page_does_not_erase_certificate():
    bb = _evidence()
    bb["materials"].append({"data": {"materials": [
        {"material_number": "M-1", "certification": None, "certification_known": False}]}})
    assert len(build(CTX, bb).answer["positions"]) == 2
    target = {"certification": "none", "certification_known": True}
    merge_certification(target, {"certification": None, "certification_known": False})
    assert certification_known(target)
    merge_certification(target, {"certification": "EN 9876"})
    assert target["certification"] == "EN 9876" and target["certification_known"] is True


def test_conflicting_valid_prices_never_depend_on_completion_order():
    bb = _evidence()
    conditions = bb["materials"][0]["data"]["materials"][0]["conditions"]
    other = deepcopy(conditions[0])
    other["valid_from"] = "2030-02-01"
    other["scales"][0]["price"] = "111,00"
    conditions.append(other)
    forward = build(CTX, bb)
    conditions.reverse()
    backward = build(CTX, bb)
    assert forward.answer == backward.answer
    assert "1.1" in forward.skipped


def test_partial_requirement_page_preserves_observed_dimensions_and_cert():
    bb = _evidence()
    bb["requirements"].append({"data": {"revision": "V2", "requirements": [
        {"ref": "R-A", "bore_mm": None, "certification": None, "certification_known": False}]}})
    assert len(build(CTX, bb).answer["positions"]) == 2


def test_index_windows_never_fall_back_to_a_future_or_expired_row():
    future = {"eur_per_kg": "9", "valid_from": "2030-04-01", "valid_to": "2030-05-01"}
    expired = {"eur_per_kg": "8", "valid_from": "2030-01-01", "valid_to": "2030-03-09"}
    assert index_rate([future], T) is None
    assert index_rate([expired], T) is None
    assert index_rate([future, expired], T) is None
    assert index_rate([{"eur_per_kg": "2", "valid_from": "unreadable"}], T) is None
    assert index_rate({"eur_per_kg": "2"}, T) == 2
    assert index_rate([{"eur_per_kg": "2", "valid_from": "2030-03-10", "valid_to": "2030-03-10"}], T) == 2


def test_conflicting_valid_index_rates_do_not_depend_on_arrival_order():
    entries = [{"eur_per_kg": "2"}, {"eur_per_kg": "3", "valid_from": "2030-03-01"}]
    assert index_rate(entries, T) is None
    assert index_rate(list(reversed(entries)), T) is None
    assert index_rate([{"eur_per_kg": "2"}, {"eur_per_kg": "2.00"}], T) == 2


def test_missing_current_index_never_becomes_zero_surcharge():
    bb = _evidence()
    for item in bb["mail_message"]:
        if item["data"].get("topic") == "metal_index":
            item["data"]["data"]["indices"]["IX"] = [{"eur_per_kg": "9", "valid_from": "2030-04-01"}]
    result = build(CTX, bb)
    assert "1.1" in result.skipped
    assert all(p["position"] != "1.1" for p in result.answer["positions"])
    assert any("metal index" in message and "not proven valid" in message for message in result.trace)

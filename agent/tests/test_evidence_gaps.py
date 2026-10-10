"""B07: evidence gaps must never look like certainty (synthetic data only)."""
from rfq.rules.parse import revision_is_current
from rfq.rules.resolve import Context, build
from tests.test_rules import CTX, _evidence


def test_revision_status_words():
    assert revision_is_current({"status_text": "gültig"})
    assert revision_is_current({"status_text": "aktuell (freigegeben)"})
    assert not revision_is_current({"status_text": "ungültig"})
    assert not revision_is_current({"status_text": "nicht gültig"})
    assert not revision_is_current({"status_text": "invalid"})
    assert not revision_is_current({"status_text": "ersetzt"})
    assert not revision_is_current({"status_text": "Entwurf, gültig ab Freigabe"})
    assert not revision_is_current({"status_text": "gültig", "is_current": False})
    assert revision_is_current({"status_text": None, "is_current": True})
    assert not revision_is_current({"status_text": None})


def test_negative_status_is_not_chosen_as_current():
    bb = _evidence()
    bb["spec_revisions"] = [{"data": {"revisions": [{"revision": "V1", "status_text": "ungültig"},
                                                    {"revision": "V2", "status_text": "gültig"}]}}]
    result = build(CTX, bb)
    assert any("current specification revision: V2" in line for line in result.trace)


def test_incomplete_search_never_proves_no_bid():
    bb = _evidence()
    bb["materials"][0]["data"]["materials"][0]["status_meaning"] = "blocked"   # only the blocked look-alike fits
    result = build(CTX, bb)                                                      # no group proven searched
    assert result.answer["decision"] == "bid"
    assert "1.1" in result.skipped
    assert any("not proven complete" in line for line in result.trace)


def test_unknown_status_stays_uncertain():
    bb = _evidence()
    for m in bb["materials"][0]["data"]["materials"]:
        m["status_meaning"] = None
    result = build(CTX, bb)
    assert result.answer["decision"] == "bid"
    assert any("UNCERTAIN" in line for line in result.trace)


def test_proven_complete_blocked_search_gives_no_bid():
    bb = _evidence()
    bb["materials"][0]["data"]["materials"][0]["status_meaning"] = "blocked"
    ctx = Context(**{**CTX.__dict__, "searched_groups": {"G1"}})
    assert build(ctx, bb).answer["decision"] == "no_bid"

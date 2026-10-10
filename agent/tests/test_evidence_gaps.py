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


def test_pieces_per_carton_is_only_demanded_when_a_packaging_tariff_can_apply():
    from tests.test_deadlines import _orchestrator
    material = {"material_number": "M-9", "catalog_group": "G-9", "status_meaning": "active", "bore_mm": "40",
                "stroke_mm": "100", "pressure_bar": "200", "mounting": "Flansch", "unrestricted_stock": "5",
                "lead_time_stock_weeks": "2", "lead_time_production_weeks": "8",
                "conditions": [{"valid_from": "2030-01-01", "currency": "EUR", "scales": [{"from_qty": "1", "price": "9"}]}]}
    entries = lambda *facts: {"entries": [{"number": "1", "facts": list(facts)}]}

    o = _orchestrator()                                   # Q&A not read yet: we cannot know, so keep demanding it
    o.bb.add("materials", {"materials": [material]}, "u")
    text, complete = o.erp_progress("G-9")
    assert not complete and "pieces_per_carton" in text

    o.bb.add("clarifications", entries({"type": "assignment", "position": "1", "packaging_key": None}), "u",
             via="collection")                            # Q&A read completely, no tariff anywhere
    text, complete = o.erp_progress("G-9")
    assert complete and "pieces_per_carton" not in text

    o.bb.add("clarifications", entries({"type": "assignment", "position": "1", "packaging_key": "P-9"}), "u",
             via="collection")                            # a tariff is assigned: the carton size is needed
    text, complete = o.erp_progress("G-9")
    assert not complete and "pieces_per_carton" in text


def test_an_empty_extraction_says_so_and_points_to_the_entries(monkeypatch):
    """Regression (sandbox run_f33ba429…): extracting revisions from a document register returned an empty summary,
    so the model extracted the register again and again until the loop guard aborted the specification mission."""
    import asyncio
    from rfq import llm
    from tests.test_deadlines import _orchestrator
    replies = iter([{"document_reference": None, "revisions": []},
                    {"revisions": [{"revision": "V2", "status_text": "gültig"}]}])

    async def fake_ask_json(system, prompt, **kwargs):
        return next(replies)
    monkeypatch.setattr(llm, "ask_json", fake_ask_json)
    o = _orchestrator()
    first = asyncio.run(o.extract("spec_revisions", "URL: u\nRegister: Lastenheft, Leistungsbeschreibung", "u/register"))
    again = asyncio.run(o.extract("spec_revisions", "URL: u\nRegister: Lastenheft, Leistungsbeschreibung", "u/register"))
    found = asyncio.run(o.extract("spec_revisions", "URL: u\nRevision V2 gültig", "u/doc"))
    assert "NOTHING of kind 'spec_revisions'" in first and "open the entry" in first
    assert again.startswith("already extracted earlier") and "NOTHING" in again
    assert "NOTHING" not in found and "V2" in found

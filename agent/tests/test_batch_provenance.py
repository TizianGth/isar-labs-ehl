"""Synthetic regressions for exact source attribution and complete batch inputs."""
import asyncio
import time
from types import SimpleNamespace

from rfq import llm
from rfq.agent.orchestrator import Orchestrator
from test_pricing_evidence import mail_fixture, assign, add_mail


def test_matching_material_does_not_hide_unread_price_records():
    o = Orchestrator({"brief": "Synthetic", "expires_at": (time.time() + 1200) * 1000}, {})
    req = {"bore_mm": "40", "stroke_mm": "70", "pressure_bar": "120", "mounting": "hinge"}
    material = {"material_number": "SYN-MAT", "catalog_group": "SYN-GROUP", **req,
                "status_meaning": "active", "unrestricted_stock": "10", "lead_time_stock_weeks": "2",
                "lead_time_production_weeks": "4", "pieces_per_carton": "5", "condition_records_total": "2",
                "conditions": [{"valid_from": "2032-01-01", "currency": "EUR", "price_unit_qty": "1",
                                "scales": [{"from_qty": "1", "price": "20"}]}]}
    o.bb.add("materials", {"materials": [material]}, "https://fixture.example/material")
    text, complete = o.erp_progress("SYN-GROUP", [req])
    assert not complete and "all 2 condition records" in text
    o.bb.add("materials", {"materials": [{"material_number": "SYN-MAT", "conditions": [
        {"valid_from": "2032-02-01", "currency": "EUR", "price_unit_qty": "1",
         "scales": [{"from_qty": "1", "price": "18"}]}]}]}, "https://fixture.example/conditions")
    assert o.erp_progress("SYN-GROUP", [req])[1]


def test_cached_identical_text_keeps_distinct_source_provenance(monkeypatch):
    async def scenario():
        o = Orchestrator({"brief": "Synthetic", "expires_at": (time.time() + 1200) * 1000}, {})
        calls = []
        async def single(kind, spec, text, url, via):
            calls.append((url, via))
            o.bb.add(kind, {"materials": []}, url, via)
            return "complete"
        monkeypatch.setattr(o, "_extract_new", single)
        one, two = "https://fixture.example/one", "https://fixture.example/two"
        await o.extract("materials", "identical page", one)
        await o.extract("materials", "identical page", two)
        await o.extract("materials", "identical page", one)
        assert calls == [(one, "page"), (two, "page")]
        assert {i["url"] for i in o.bb.get("materials")} == {one, two}
    asyncio.run(scenario())


def test_changed_document_heading_is_not_removed_from_the_cache_key(monkeypatch):
    async def scenario():
        o = Orchestrator({"brief": "Synthetic", "expires_at": (time.time() + 1200) * 1000}, {})
        calls = []
        async def single(kind, spec, text, url, via):
            calls.append(text)
            return "complete"
        monkeypatch.setattr(o, "_extract_new", single)
        source = "https://fixture.example/document"
        for text in ("Previous heading\nsame body", "Current heading\nsame body"):
            await o.extract("materials", text, source)
        assert len(calls) == 2
    asyncio.run(scenario())


def test_partial_batch_does_not_match_a_different_prefix_url(monkeypatch):
    async def scenario():
        o = Orchestrator({"brief": "Synthetic", "expires_at": (time.time() + 1200) * 1000}, {})
        o.brief = {"tender_reference": "RFQ-SYNTH"}
        first, second = "https://fixture.example/document/1", "https://fixture.example/document/10"
        async def batch(*args, **kwargs):
            return {"results": [{"url": second, "data": {"materials": [{"material_number": "SYN-SECOND"}]}}]}
        calls = []
        async def single(kind, spec, text, url, via):
            calls.append(url)
            o.bb.add(kind, {"materials": [{"material_number": "SYN-FIRST"}]}, url, via)
            return "first extracted separately"
        monkeypatch.setattr(llm, "ask_json", batch)
        monkeypatch.setattr(o, "_extract_new", single)
        await o.extract_many("materials", [(first, "first document"), (second, "second document")])
        assert calls == [first]
        evidence = {item["url"]: item["data"]["materials"][0]["material_number"] for item in o.bb.get("materials")}
        assert evidence == {first: "SYN-FIRST", second: "SYN-SECOND"}
    asyncio.run(scenario())


def test_long_document_uses_existing_full_extraction_instead_of_truncation(monkeypatch):
    async def scenario():
        o = Orchestrator({"brief": "Synthetic", "expires_at": (time.time() + 1200) * 1000}, {})
        o.brief = {"tender_reference": "RFQ-SYNTH"}
        first, second = "https://fixture.example/long", "https://fixture.example/short"
        batch_calls, read = [], {}
        async def batch(*args, **kwargs):
            batch_calls.append(1)
            return {"results": [{"url": url, "data": {"materials": []}} for url in (first, second)]}
        async def single(kind, spec, text, url, via):
            read[url] = text
            return "complete"
        monkeypatch.setattr(llm, "ask_json", batch)
        monkeypatch.setattr(o, "_extract_new", single)
        long = "x" * 26000 + "LAST-SECTION-FACT"
        await o.extract_many("materials", [(first, long), (second, "short")])
        assert not batch_calls
        assert read[first] == long and read[second] == "short"
    asyncio.run(scenario())


def test_late_required_bonus_is_repaired_without_cached_empty_payload(monkeypatch):
    async def scenario():
        o = mail_fixture("project_bonus", "plans", {})
        assign(o, "bonus_plan_key")
        calls = []
        o.evidence_end = time.time() + 120
        o._cache = {"mail_message:empty": "empty", "materials:keep": "material"}
        site = SimpleNamespace(fresh_start=lambda: calls.append("fresh"))
        monkeypatch.setattr(o, "site_for", lambda role: site)
        async def repair(actual_site, mission, attempts):
            assert actual_site is site and attempts == 1
            assert "mail_message:empty" not in o._cache and "materials:keep" in o._cache
            calls.append("repair")
            add_mail(o, "project_bonus", {"plans": {"SYN-KEY": {
                "bands": [{"from_eur": "0", "to_eur": None, "rate_percent": "5"}]}}})
        monkeypatch.setattr(o, "run_mission", repair)
        await o.repair_pricing_mail(SimpleNamespace(name="mail"))
        assert calls == ["fresh", "repair"] and not o.pricing_mail_gaps()
    asyncio.run(scenario())


def test_late_repair_does_not_consume_the_submission_reserve(monkeypatch):
    async def scenario():
        o = mail_fixture("project_bonus", "plans", {})
        assign(o, "bonus_plan_key")
        o.evidence_end = time.time() + 30
        async def forbidden(*args, **kwargs):
            raise AssertionError("research started inside submission reserve")
        monkeypatch.setattr(o, "run_mission", forbidden)
        await o.repair_pricing_mail(SimpleNamespace(name="mail"))
        assert o.pricing_mail_gaps()
    asyncio.run(scenario())

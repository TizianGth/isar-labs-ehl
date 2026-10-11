"""Synthetic scheduling, immutable evidence and conditional completeness regressions; no model/network calls."""
import asyncio
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from rfq import llm
from rfq.agent import orchestrator as module
from rfq.agent.extraction_jobs import ExtractionJobs, Snapshot
from rfq.agent.explorer import Collection, Explorer, Mission
from rfq.agent.orchestrator import Orchestrator


def orch(monkeypatch, asynchronous=False):
    monkeypatch.setenv("RFQ_ASYNC_EXTRACT", "1" if asynchronous else "0")
    sites = {s: SimpleNamespace(site_id=s, url=f"https://{s}.example") for s in ("alpha", "beta", "gamma", "delta")}
    o = Orchestrator({"expires_at": (time.time() + 7200) * 1000, "brief": "Synthetic RFQ-SYNTH",
                      "access": [{"siteId": s, "url": v.url} for s, v in sites.items()]}, sites)
    o.brief = {"tender_reference": "RFQ-SYNTH", "scenario_datetime_utc": "2032-04-20T08:00:00Z",
               "sites": dict(zip(("mail", "erp", "tender", "specification"), sites))}
    return o


def test_role_repair_uses_inventory_without_credentials(monkeypatch):
    async def scenario():
        o = orch(monkeypatch)
        o.run["access"][0]["credentials"] = {"password": "never-send-this"}
        calls = []

        async def fake(system, user, **kwargs):
            calls.append((user, kwargs))
            return ({"tender_reference": "RFQ-SYNTH", "sites": {"tender": "missing"}}
                    if len(calls) == 1 else {"sites": {"tender": "gamma", "specification": "delta", "erp": "beta", "mail": "alpha"}})

        monkeypatch.setattr(llm, "ask_json", fake)
        await o.read_brief()
        assert o.site_for("tender") is o.sites["gamma"]
        assert calls[1][1]["tag"] == "brief:roles"
        assert "never-send-this" not in calls[1][0]
        assert calls[0][1]["deadline"] == o.evidence_end
    asyncio.run(scenario())


def test_invalid_roles_never_fall_back_to_inventory_order(monkeypatch):
    o = orch(monkeypatch)
    o.brief["sites"] = {"tender": "unknown"}
    with pytest.raises(ValueError, match="mapping"):
        o.site_for("tender")
    assert not o.valid_roles(dict.fromkeys(("tender", "specification", "erp", "mail"), "alpha"))


def test_role_repair_is_bounded_to_one_attempt(monkeypatch):
    async def scenario():
        o = orch(monkeypatch)
        calls = []
        async def fake(*args, **kwargs):
            calls.append(kwargs["tag"])
            return {"sites": {}}
        monkeypatch.setattr(llm, "ask_json", fake)
        with pytest.raises(ValueError, match="one targeted repair"):
            await o.read_brief()
        assert calls == ["brief", "brief:roles"]
    asyncio.run(scenario())


def test_brief_and_ready_site_do_not_wait_for_unrelated_startup(monkeypatch):
    async def scenario():
        o = orch(monkeypatch)
        entered, blocked = asyncio.Event(), asyncio.Event()
        async def slow():
            entered.set()
            await blocked.wait()
        o.readiness = {"alpha": asyncio.create_task(slow()), "gamma": asyncio.create_task(asyncio.sleep(0))}
        async def fake(*args, **kwargs):
            assert entered.is_set()
            return dict(o.brief)
        monkeypatch.setattr(llm, "ask_json", fake)
        await entered.wait()
        await o.read_brief()
        await asyncio.wait_for(o.wait_site(o.sites["gamma"]), .1)
        assert not o.readiness["alpha"].done()
        own = asyncio.create_task(o.wait_site(o.sites["alpha"]))
        await asyncio.sleep(0)
        assert not own.done()
        blocked.set()
        await own
        await o.close()
    asyncio.run(scenario())


def test_snapshot_immutability_dedup_and_source_identity():
    async def scenario():
        calls, gate = [], asyncio.Event()
        async def extract(kind, text, source, via):
            calls.append((kind, text, source, via))
            await gate.wait()
            return "facts"
        jobs = ExtractionJobs(extract)
        snapshot = Snapshot("positions", "selected page", "https://fixture.example/view", "page")
        with pytest.raises(FrozenInstanceError):
            snapshot.text = "later navigation"
        first = jobs.enqueue("mission-one", snapshot)
        second = jobs.enqueue("mission-two", snapshot)
        assert first == second and len(jobs.jobs) == 1
        jobs.enqueue("mission-two", Snapshot(snapshot.kind, snapshot.text, snapshot.source + "-other", snapshot.via))
        await asyncio.sleep(0)
        assert len(calls) == 2 and jobs.pending()
        gate.set()
        await jobs.drain(time.time() + 1)
        assert not jobs.pending() and all(j.status == "complete" for j in jobs.jobs)
    asyncio.run(scenario())


def test_new_requirement_null_needs_explicit_absence_proof(monkeypatch):
    async def scenario():
        o = orch(monkeypatch)
        replies = iter([
            {"revision": "SYN-REV", "requirements": [{"ref": "SYN-REQ", "certification": None}]},
            {"revision": "SYN-REV", "requirements": [{"ref": "SYN-REQ", "certification": None, "certification_known": True}]},
            {"revision": "SYN-REV", "requirements": [{"ref": "SYN-REQ", "certification": "none"}]},
        ])
        async def fake(*args, **kwargs): return next(replies)
        monkeypatch.setattr(llm, "ask_json", fake)
        for page in ("unknown", "explicit absence", "literal none"):
            await o.extract("requirements", page, "https://fixture.example/" + page.replace(" ", "-"))
        records = [i["data"]["requirements"][0] for i in o.bb.get("requirements")]
        assert records[0]["certification_known"] is False
        assert not module.parse.certification_known(records[0], requirement=True)
        assert module.parse.certification_known(records[1], requirement=True)
        assert module.parse.certification_known(records[2], requirement=True)
    asyncio.run(scenario())


def test_late_material_index_dependency_is_reconciled_from_valid_authorized_mail(monkeypatch):
    o = orch(monkeypatch)
    o.brief["authorized_records"] = [{"topic": "metal_index", "sender_localpart": "calc"}]
    o.bb.add("spec_revisions", {"revisions": [{"revision": "SYN-REV", "is_current": True}]}, "https://fixture.example/revisions")
    o.bb.add("requirements", {"revision": "SYN-REV", "requirements": [
        {"ref": "SYN-REQ", "catalog_group": "SYN-GROUP", "certification": "none"}]}, "https://fixture.example/spec")
    o.bb.add("positions", {"positions": [{"position": "SYN-POS", "requirement_ref": "SYN-REQ", "quantity": "2"}]}, "https://fixture.example/tender")
    o.bb.add("materials", {"materials": [{"material_number": "SYN-MAT", "catalog_group": "SYN-GROUP",
              "status_meaning": "active", "surcharge_mass_g": "500", "price_index_key": "SYN-INDEX"}]}, "https://fixture.example/material")
    def record(sender, rate, at="2032-04-19T08:00:00Z"):
        return {"reference": "RFQ-SYNTH", "sender": sender, "datetime_utc": at, "topic": "metal_index",
                "data": {"indices": {"SYN-INDEX": [rate]}}}
    o.bb.add("mail_message", record("calc@synthetic.example", {"eur_per_kg": "4", "valid_from": "2032-05-01"}), "https://fixture.example/future-rate")
    assert o.index_mail_gaps()
    o.bb.add("mail_message", record("wrong@synthetic.example", {"eur_per_kg": "9"}), "https://fixture.example/decoy-rate")
    assert o.index_mail_gaps()
    o.bb.add("mail_message", record("calc@synthetic.example", {"eur_per_kg": "3", "valid_from": "2032-04-01"},
                                  at="2032-04-20T07:00:00Z"), "https://fixture.example/current-rate")
    assert not o.index_mail_gaps()


def test_gap_repair_can_reread_same_selected_source_without_invalidating_other_kinds(monkeypatch):
    async def scenario():
        o = orch(monkeypatch, True)
        calls = []
        async def fake(*args, **kwargs):
            calls.append(kwargs["tag"])
            if kwargs["tag"] == "extract:mail_message":
                return {"subject": "selected index record", "topic": "metal_index", "data": {"indices": {}}}
            return {"positions": [{"position": "SYN-POS", "quantity": "2"}]}
        monkeypatch.setattr(llm, "ask_json", fake)
        message = Snapshot("mail_message", "selected original text", "https://fixture.example/message", "page")
        positions = Snapshot("positions", "selected positions", "https://fixture.example/list", "page")
        o.jobs.enqueue("mail", message)
        o.jobs.enqueue("tender", positions)
        await o.jobs.drain(time.time() + 1)
        o.invalidate_extractions("mail_message")
        o.jobs.enqueue("mail", message)
        o.jobs.enqueue("tender", positions)
        await o.jobs.drain(time.time() + 1)
        assert calls.count("extract:mail_message") == 2
        assert calls.count("extract:positions") == 1
        assert len(o.bb.get("mail_message")) == 2
        await o.close()
    asyncio.run(scenario())


def test_failed_and_cancelled_jobs_are_retryable():
    async def scenario():
        calls = 0
        async def extract(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary parser failure")
            if calls == 2:
                await asyncio.Event().wait()
            return "facts"
        jobs = ExtractionJobs(extract)
        s = Snapshot("requirements", "selected text", "https://fixture.example/doc", "collection")
        jobs.enqueue("spec", s)
        await jobs.drain(time.time() + 1)
        assert jobs.jobs[-1].status == "failed"
        jobs.enqueue("spec", s)
        await asyncio.sleep(0)
        await jobs.drain(time.time())
        assert jobs.jobs[-1].status == "cancelled"
        jobs.enqueue("spec", s)
        await jobs.drain(time.time() + 1)
        assert calls == 3 and jobs.jobs[-1].status == "complete"
        jobs.enqueue("spec", s)
        assert len(jobs.jobs) == 3
    asyncio.run(scenario())


def test_snapshot_status_preserves_unresolved_sources_and_omits_superseded_failures():
    async def scenario():
        attempts = 0
        async def extract(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ValueError("read failed")
            return "many completed facts that must not be repeated on every model round"
        jobs = ExtractionJobs(extract)
        snapshot = Snapshot("requirements", "selected document", "https://fixture.example/selected", "page")
        jobs.enqueue("spec", snapshot)
        assert "queued" in jobs.status("spec") and "j1" not in jobs.status("spec")
        await jobs.drain(time.time() + 1)
        assert "failed" in jobs.status("spec")
        jobs.enqueue("spec", snapshot)
        assert "failed" not in jobs.status("spec") and "queued" in jobs.status("spec")
        await jobs.drain(time.time() + 1)
        status = jobs.status("spec")
        assert "1 selected snapshots completed" in status
        assert "many completed facts" not in status and "failed" not in status
        assert jobs.status("unrelated mission") == ""
        assert jobs.jobs[-1].result.startswith("many completed facts")
    asyncio.run(scenario())


def test_cancel_before_execution_is_not_left_queued():
    async def scenario():
        async def extract(*args, **kwargs):
            raise AssertionError("cancelled queued job must never execute")
        jobs = ExtractionJobs(extract)
        jobs.enqueue("spec", Snapshot("requirements", "text", "https://fixture.example/doc", "page"))
        jobs.jobs[0].task.cancel()
        await jobs.close()
        assert jobs.jobs[0].status == "cancelled"
    asyncio.run(scenario())


def test_cancelled_owner_wait_does_not_discard_shared_evidence():
    async def scenario():
        gate = asyncio.Event()
        async def extract(*args, **kwargs):
            await gate.wait()
            return "facts"
        jobs = ExtractionJobs(extract)
        s = Snapshot("requirements", "text", "https://fixture.example/doc", "page")
        jobs.enqueue("one", s)
        jobs.enqueue("two", s)
        owner = asyncio.create_task(jobs.drain(time.time() + 60, "one"))
        await asyncio.sleep(0)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert jobs.pending("two")
        gate.set()
        await jobs.drain(time.time() + 1, "two")
        assert jobs.jobs[0].status == "complete"
    asyncio.run(scenario())


def test_finish_waits_for_snapshot_and_independent_navigation_continues(monkeypatch):
    async def scenario():
        o = orch(monkeypatch, True)
        gate, navigated = asyncio.Event(), asyncio.Event()
        async def fake_json(*args, **kwargs):
            await gate.wait()
            return {"positions": [{"position": "S-ONE", "quantity": "3"}]}
        monkeypatch.setattr(llm, "ask_json", fake_json)
        site = o.sites["gamma"]
        site.page = SimpleNamespace(url="https://fixture.example/start")
        site.collections = {}
        async def observe(): return "[e1] selected detail"
        async def full_text(): return "URL: snapshot\nposition S-ONE quantity 3"
        async def act(*args):
            navigated.set()
            assert o.jobs.pending()
            site.page.url = "https://fixture.example/later"
            gate.set()
            return "opened independent view"
        site.observe, site.full_text, site.act = observe, full_text, act
        actions = iter([("extract", {"kind": "positions"}), ("click", {"ref": "e1"}), ("finish", {"summary": "done"})])
        async def chat(*args, **kwargs): return {"tool_calls": [next(actions)]}
        monkeypatch.setattr(llm, "chat", chat)
        mission = Mission("synthetic", "collect selected evidence", {"positions": "positions"}, deadline=time.time() + 90)
        e = await o.run_mission(site, mission, attempts=1)
        assert e.status == "finished" and navigated.is_set()
        assert o.bb.get("positions")[0]["url"] == "https://fixture.example/start"
        assert not o.jobs.pending()
        await o.close()
    asyncio.run(scenario())


def test_calculation_rejects_pending_snapshots(monkeypatch):
    async def scenario():
        o = orch(monkeypatch, True)
        o.jobs.enqueue("spec", Snapshot("requirements", "text", "https://fixture.example/doc", "page"))
        with pytest.raises(RuntimeError, match="pending"):
            o.resolve()
        await o.close()
    asyncio.run(scenario())


def test_unknown_explicit_collection_never_reads_an_unrelated_last_collection():
    saved = Collection("c1", [("https://fixture.example/list", "unrelated facts")], [])
    e = Explorer(SimpleNamespace(collections={"c1": saved}), Mission("test", "collect", {}), None)
    assert e._collection("c1") is saved
    assert e._collection(None) is saved
    assert e._collection("j1") is None
    assert e._collection("c-missing") is None


def test_wait_tool_settles_sources_without_claiming_missing_facts_complete(monkeypatch):
    async def scenario():
        settled = False
        async def drain():
            nonlocal settled
            settled = True
        async def observe(): return "selected source"
        mission = Mission("test", "collect", {}, deadline=time.time() + 60,
                          progress=lambda: ("[ ] required revision still missing", False))
        site = SimpleNamespace(observe=observe, site_id="synthetic", url="https://fixture.example/source")
        e = Explorer(site, mission, None,
                     await_extractions=drain, extraction_status=lambda: "snapshot requirements failed")
        result = await e._call("wait_for_extractions", {})
        assert settled and "Continue with missing facts" in result and "failed" in result
        assert e.status == "running"
        async def chat(*args, **kwargs):
            names = {t["function"]["name"] for t in kwargs["tools"]}
            assert "wait_for_extractions" in names
            return {"tool_calls": [("finish", {"summary": "source unavailable", "missing": ["revision"]})]}
        monkeypatch.setattr(llm, "chat", chat)
        await e.run()
        assert e.status == "incomplete"
        ordinary = Explorer(site, mission, None)
        assert (await ordinary._call("wait_for_extractions", {})).startswith("ERROR")
    asyncio.run(scenario())


def test_separate_background_batches_can_wait_on_the_same_unchanged_page(monkeypatch):
    async def scenario():
        settled = 0
        async def drain():
            nonlocal settled
            settled += 1
        async def observe(): return "unchanged source list"
        actions = iter([("wait_for_extractions", {})] * 3 + [("finish", {"summary": "done"})])
        async def chat(*args, **kwargs): return {"tool_calls": [next(actions)]}
        monkeypatch.setattr(llm, "chat", chat)
        e = Explorer(SimpleNamespace(observe=observe, site_id="synthetic", url="https://fixture.example/list"),
                     Mission("test", "settle selected batches", {}, deadline=time.time() + 60), None,
                     await_extractions=drain)
        await e.run()
        assert sum("wait_for_extractions" in s and "ERROR" not in s for s in e.history) == 3
        assert settled >= 3 and e.status == "finished"
    asyncio.run(scenario())


def test_phase_interruption_settles_late_snapshot_before_calculation(monkeypatch):
    async def scenario():
        o = orch(monkeypatch, True)
        async def no_brief(): pass
        async def fake_json(*args, **kwargs):
            await asyncio.sleep(.02)
            return {"positions": [{"position": "S-ONE", "quantity": "3"}]}
        async def stopped_phase():
            o.jobs.enqueue("tender", Snapshot("positions", "selected position", "https://fixture.example/view", "page"))
            raise TimeoutError("navigation phase ended while extraction is in flight")
        async def fake_submit(current, result):
            assert not current.jobs.pending()
            assert current.bb.get("positions")[0]["data"]["positions"][0]["quantity"] == "3"
        monkeypatch.setattr(llm, "ask_json", fake_json)
        monkeypatch.setattr(o, "read_brief", no_brief)
        monkeypatch.setattr(o, "gather_evidence", stopped_phase)
        monkeypatch.setattr(module, "submit_bid", fake_submit)
        await o.run_all()
        assert o.jobs.jobs[0].status == "complete"
        await o.close()
    asyncio.run(scenario())


def test_async_cache_preserves_distinct_source_provenance(monkeypatch):
    async def scenario():
        o = orch(monkeypatch, True)
        async def fake(*args, **kwargs): return {"positions": [{"position": "S-ONE"}]}
        monkeypatch.setattr(llm, "ask_json", fake)
        for source in ("https://fixture.example/one", "https://fixture.example/two"):
            o.jobs.enqueue("tender", Snapshot("positions", "URL: same\nidentical rows", source, "page"))
        await o.jobs.drain(time.time() + 1)
        assert {item["url"] for item in o.bb.get("positions")} == {"https://fixture.example/one", "https://fixture.example/two"}
        await o.close()
    asyncio.run(scenario())


def test_chunk_failure_cancels_and_awaits_sibling_requests(monkeypatch):
    async def scenario():
        o = orch(monkeypatch, True)
        monkeypatch.setattr(module, "CHUNK_CHARS", 30)
        running, stopped = [], []
        async def fake(system, user, **kwargs):
            running.append(user)
            try:
                if "part 1 of" in user:
                    await asyncio.sleep(.01)
                    raise RuntimeError("first chunk failed")
                await asyncio.Event().wait()
            finally:
                stopped.append(user)
        monkeypatch.setattr(llm, "ask_json", fake)
        o.jobs.enqueue("spec", Snapshot("requirements", "\n".join(["one row with enough text"] * 8), "https://fixture.example/doc", "page"))
        await o.jobs.drain(time.time() + 1)
        assert len(running) > 1 and len(stopped) == len(running)
        assert o.jobs.jobs[0].status == "failed" and not o._inflight
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        await o.close()
    asyncio.run(scenario())


def add_spec(o, **overrides):
    r = {"ref": "REQ-SYNTH", "bore_mm": "40", "stroke_mm": "70", "pressure_bar": "120", "mounting": "hinge",
         "certification": None, "catalog_group": "SYNTH-GROUP", **overrides}
    o.bb.add("spec_revisions", {"revisions": [{"revision": "REV-CURRENT", "is_current": True}]}, "https://fixture.example/history")
    o.bb.add("positions", {"positions": [{"position": "S-ONE", "requirement_ref": r["ref"], "quantity": "6", "unit": "Stück"}]}, "https://fixture.example/tender")
    o.bb.add("requirements", {"revision": "REV-CURRENT", "requirements": [r]}, "https://fixture.example/current")
    return r


def add_material(o, **overrides):
    m = {"material_number": "SYNTH-MAT", "catalog_group": "SYNTH-GROUP", "bore_mm": "40", "stroke_mm": "70",
         "pressure_bar": "120", "mounting": "hinge", "certification": "none", "status_meaning": "active",
         "unrestricted_stock": "9", "lead_time_stock_weeks": "2", "conditions": [{"record": "synthetic-record",
         "valid_from": "2032-01-01", "currency": "EUR", "price_unit_qty": "1", "scales": [{"from_qty": "1", "price": "20"}]}], **overrides}
    o.bb.add("materials", {"materials": [m]}, "https://fixture.example/material")
    return m


def test_revision_provenance_and_technical_fields_are_required(monkeypatch):
    o = orch(monkeypatch)
    add_spec(o)
    assert o.spec_progress()[1]
    o.bb.get("requirements")[0]["data"].pop("revision")
    o.bb.get("requirements")[0]["via"] = "collection"
    assert not o.spec_progress()[1]


def test_requirements_do_not_start_groups_before_current_revision_is_known(monkeypatch):
    o = orch(monkeypatch)
    add_spec(o)
    o.bb.items.pop("spec_revisions")
    assert o.requirements() == {} and o.erp_groups() == {}
    o.bb.get("requirements")[0]["validated_current_revision"] = "REV-CURRENT"
    assert not o.spec_progress()[1]  # envelope cannot replace proof of the current revision
    o.bb.get("requirements")[0]["data"]["requirements"][0].pop("bore_mm")
    assert not o.spec_progress()[1]


def test_valid_late_amendment_supplies_effective_requirement(monkeypatch):
    o = orch(monkeypatch)
    add_spec(o, bore_mm=None)
    assert not o.spec_progress()[1]
    o.bb.add("clarifications", {"entries": [{"number": "1", "datetime_utc": "2032-04-19T08:00:00Z", "facts": [
        {"type": "tech_amendment", "revision": "REV-CURRENT", "requirement": "REQ-SYNTH", "attribute": "bore", "value": "40"}]}]}, "https://fixture.example/qa")
    assert o.spec_progress()[1]
    o.bb.add("clarifications", {"entries": [{"number": "2", "datetime_utc": "2032-04-19T09:00:00Z", "facts": [
        {"type": "withdrawal", "withdrawn_number": "1"}]}]}, "https://fixture.example/qa-later")
    assert not o.spec_progress()[1]


def test_late_amendment_restarts_finished_erp_group(monkeypatch):
    async def scenario():
        o = orch(monkeypatch)
        add_spec(o)
        o.done["specification"].set()
        o.done["tender"].set()
        calls = []
        async def mission(site, task):
            calls.append(task.goal)
            if len(calls) == 1:
                o.bb.add("clarifications", {"entries": [{"number": "1", "facts": [{"type": "tech_amendment",
                    "revision": "REV-CURRENT", "requirement": "REQ-SYNTH", "attribute": "bore", "value": "45"}]}]}, "https://fixture.example/qa")
            o.status[task.name] = "complete"
        monkeypatch.setattr(o, "run_mission", mission)
        monkeypatch.setattr(o, "_tab", lambda base: asyncio.sleep(0, result=base))
        await asyncio.wait_for(o.erp_phase(), 5)
        assert len(calls) == 2 and "bore 40" in calls[0] and "bore 45" in calls[1]
    asyncio.run(scenario())


def test_late_valid_delivery_change_restarts_finished_erp_group(monkeypatch):
    async def scenario():
        o = orch(monkeypatch)
        r = add_spec(o)
        m = add_material(o, unrestricted_stock=None, lead_time_stock_weeks=None)
        o.brief["authorized_records"] = [{"topic": "delivery", "sender_localpart": "logistics"}]
        o.bb.add("clarifications", {"entries": [{"number": "1", "facts": [{"type": "assignment", "position": "S-ONE", "delivery_key": "SYNTH-DELIVERY"}]}]}, "https://fixture.example/qa")
        def mail(at, cap, **changes):
            return {"reference": "RFQ-SYNTH", "sender": "logistics@fixture.example", "datetime_utc": at, "topic": "delivery",
                    "data": {"commitments": {"SYNTH-DELIVERY": {"material": "SYNTH-MAT", "max_total_qty": cap, "weeks": "3"}}}, **changes}
        o.bb.add("mail_message", mail("2032-04-18T08:00:00Z", "6"), "https://fixture.example/mail-first")
        o.done["specification"].set()
        o.done["tender"].set()
        calls = []
        async def mission(site, task):
            text, complete = task.progress()
            calls.append(complete)
            if len(calls) == 1:
                assert complete, text
                o.bb.add("mail_message", mail("2032-04-19T08:00:00Z", "1"), "https://fixture.example/mail-later")
            else:
                assert not complete and "unrestricted_stock" in text
                m.update(unrestricted_stock="9", lead_time_stock_weeks="2")
            o.status[task.name] = "complete"
        monkeypatch.setattr(o, "run_mission", mission)
        monkeypatch.setattr(o, "_tab", lambda base: asyncio.sleep(0, result=base))
        await asyncio.wait_for(o.erp_phase(), 5)
        assert calls == [True, False]
        old = o.delivery_commitments()
        o.bb.add("mail_message", mail("2032-04-19T09:00:00Z", "6", reference="RFQ-OTHER"), "https://fixture.example/foreign")
        o.bb.add("mail_message", mail("2032-04-21T09:00:00Z", "6"), "https://fixture.example/future")
        o.bb.add("mail_message", mail("2032-04-19T09:00:00Z", "6", sender="outsider@fixture.example"), "https://fixture.example/unauthorized")
        assert o.delivery_commitments() == old
    asyncio.run(scenario())


def test_unused_pricing_fields_do_not_force_extra_pages(monkeypatch):
    o = orch(monkeypatch)
    r = add_spec(o)
    add_material(o)
    text, complete = o.erp_progress("SYNTH-GROUP", [r])
    assert complete, text
    assert "pieces_per_carton" not in text and "lead_time_production" not in text


def test_completion_barrier_repairs_late_erp_dependencies(monkeypatch):
    async def scenario():
        o = orch(monkeypatch)
        add_spec(o)
        material = add_material(o)
        material["unrestricted_stock"] = None
        o.erp_complete.add("SYNTH-GROUP")
        calls = []
        async def run(site, mission, attempts):
            calls.append(mission.name)
            assert "SYNTH-GROUP" not in o.erp_complete
            material["unrestricted_stock"] = "100"
        monkeypatch.setattr(o, "run_mission", run)
        await o.reconcile_evidence(o.mission("specification", "Read current requirements", ["requirements"], 10,
                                           progress=o.spec_progress))
        assert calls == ["erp:SYNTH-GROUP"]
        assert "SYNTH-GROUP" in o.erp_complete
    asyncio.run(scenario())


def test_unknown_certification_and_price_basis_are_incomplete(monkeypatch):
    o = orch(monkeypatch)
    r = add_spec(o)
    m = add_material(o, certification=None)
    assert not o.erp_progress("SYNTH-GROUP", [r])[1]
    m["certification"] = "none"
    m["conditions"][0].pop("price_unit_qty")
    assert "price_unit_qty" in o.erp_progress("SYNTH-GROUP", [r])[0]


def test_partial_detail_does_not_erase_proven_material_certification(monkeypatch):
    o = orch(monkeypatch)
    r = add_spec(o)
    add_material(o)
    o.bb.add("materials", {"materials": [{"material_number": "SYNTH-MAT", "certification": None, "certification_known": False,
                                           "unrestricted_stock": "9"}]}, "https://fixture.example/stock-only")
    assert o.materials()["SYNTH-MAT"]["certification"] == "none"
    assert o.erp_progress("SYNTH-GROUP", [r])[1]


def test_unreadable_required_numbers_do_not_count_as_complete(monkeypatch):
    o = orch(monkeypatch)
    r = add_spec(o)
    m = add_material(o)
    m["conditions"][0]["price_unit_qty"] = "0"
    assert not o.erp_progress("SYNTH-GROUP", [r])[1]
    m["conditions"][0]["price_unit_qty"] = "1"
    m["unrestricted_stock"] = "not readable"
    assert "unrestricted_stock" in o.erp_progress("SYNTH-GROUP", [r])[0]


def test_shared_material_demand_selects_production_branch(monkeypatch):
    o = orch(monkeypatch)
    r = add_spec(o)
    o.bb.get("positions")[0]["data"]["positions"].append({"position": "S-TWO", "requirement_ref": r["ref"], "quantity": "6", "unit": "Stück"})
    add_material(o)
    text, complete = o.erp_progress("SYNTH-GROUP", [r])
    assert not complete and "lead_time_production_weeks" in text


def test_assignments_require_only_their_weight_and_packaging(monkeypatch):
    o = orch(monkeypatch)
    r = add_spec(o)
    add_material(o)
    o.bb.add("clarifications", {"entries": [{"number": "1", "facts": [{"type": "assignment", "position": "S-ONE",
                         "setup_pool_key": "SYNTH-POOL", "packaging_key": "SYNTH-PACK"}]}]}, "https://fixture.example/qa")
    text, complete = o.erp_progress("SYNTH-GROUP", [r])
    assert not complete and "allocation_weight_g" in text and "pieces_per_carton" in text


def test_feature_flag_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("RFQ_ASYNC_EXTRACT", raising=False)
    o = Orchestrator({"expires_at": (time.time() + 7200) * 1000}, {})
    assert o.jobs is None

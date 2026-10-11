"""Invented dependency regressions: mail topics alone are not pricing evidence."""
import asyncio
import time

import pytest

from rfq.agent.orchestrator import Blackboard, Orchestrator
from test_speed_scheduling import orch, add_spec, add_material


def mail_fixture(topic, field, records):
    o = Orchestrator.__new__(Orchestrator)
    o.bb = Blackboard()
    o.brief = {"tender_reference": "RFQ-SYNTH", "scenario_datetime_utc": "2032-04-20T08:00:00Z",
               "director_localpart": "director", "authorized_records": [
                   {"topic": topic, "sender_localpart": "finance", "subject_keyword": topic}]}
    o.erp_complete = set()
    o.bb.add("mail_list", {"folder": "inbox", "page": 1, "pages_total": 1,
                           "mailbox_address": "sales@fixture.example", "messages": []}, "https://fixture.example/list")
    add_mail(o, "discount", {"is_approval": True, "percent": "0"}, sender="director@fixture.example")
    add_mail(o, topic, {field: records})
    return o


def add_mail(o, topic, data, **overrides):
    m = {"topic": topic, "reference": "RFQ-SYNTH", "datetime_utc": "2032-04-19T08:00:00Z",
         "sender": "finance@fixture.example", "is_draft_or_preview": False, "data": data}
    m.update(overrides)
    o.bb.add("mail_message", m, f"https://fixture.example/message/{len(o.bb.get('mail_message'))}")


def assign(o, field, key="SYN-KEY", position="SYN-POS", **entry):
    o.bb.add("clarifications", {"entries": [{"number": "1", "datetime_utc": "2032-04-19T07:00:00Z",
              "facts": [{"type": "assignment", "position": position, field: key}], **entry}]},
             "https://fixture.example/questions")


@pytest.mark.parametrize("topic,field,assignment", [
    ("project_bonus", "plans", "bonus_plan_key"),
    ("setup_pool", "pools", "setup_pool_key"),
    ("packaging", "tariffs", "packaging_key"),
])
def test_empty_topic_is_not_complete(topic, field, assignment):
    o = mail_fixture(topic, field, {})
    assign(o, assignment)
    text, complete = o.mail_progress()
    assert not complete, text
    assert "SYN-KEY" in text


@pytest.mark.parametrize("record", [{}, {"bands": []}, {"bands": [{"from_eur": "0", "to_eur": None}]},
    {"bands": [{"from_eur": "0", "to_eur": "unreadable", "rate_percent": "5"}]},
    {"bands": [{"from_eur": False, "to_eur": None, "rate_percent": "5"}]},
    {"bands": [{"from_eur": "0", "to_eur": "20", "rate_percent": "5"},
               {"from_eur": "10", "to_eur": None, "rate_percent": "2"}]}])
def test_unreadable_bonus_is_incomplete(record):
    o = mail_fixture("project_bonus", "plans", {"SYN-KEY": record})
    assign(o, "bonus_plan_key")
    assert not o.mail_progress()[1]


@pytest.mark.parametrize("topic,field,assignment,record", [
    ("project_bonus", "plans", "bonus_plan_key", {"bands": [{"from_eur": "0", "to_eur": None, "rate_percent": "0"}]}),
    ("setup_pool", "pools", "setup_pool_key", {"amount_eur": "0"}),
    ("packaging", "tariffs", "packaging_key", {"eur_per_carton": "0"}),
])
def test_explicit_zero_is_complete(topic, field, assignment, record):
    o = mail_fixture(topic, field, {"SYN-KEY": record})
    assign(o, assignment)
    assert o.mail_progress()[1], o.mail_progress()[0]


def test_unassigned_bonus_does_not_force_research():
    o = mail_fixture("project_bonus", "plans", {})
    assert o.mail_progress()[1]


@pytest.mark.parametrize("overrides", [
    {"reference": "RFQ-OTHER"}, {"datetime_utc": "2032-04-21T08:00:00Z"},
    {"sender": "outsider@fixture.example"}, {"is_draft_or_preview": True},
])
def test_invalid_mail_cannot_fill_missing_key(overrides):
    o = mail_fixture("setup_pool", "pools", {})
    assign(o, "setup_pool_key")
    add_mail(o, "setup_pool", {"pools": {"SYN-KEY": {"amount_eur": "2"}}}, **overrides)
    assert not o.mail_progress()[1]


def test_latest_key_record_must_be_readable():
    o = mail_fixture("setup_pool", "pools", {"SYN-KEY": {"amount_eur": "2"}})
    assign(o, "setup_pool_key")
    assert o.mail_progress()[1]
    add_mail(o, "setup_pool", {"pools": {"SYN-KEY": {}}}, datetime_utc="2032-04-19T09:00:00Z")
    assert not o.mail_progress()[1]


def test_empty_newer_payload_cannot_hide_a_key_update():
    o = mail_fixture("setup_pool", "pools", {"SYN-KEY": {"amount_eur": "2"}})
    assign(o, "setup_pool_key")
    add_mail(o, "setup_pool", {"pools": {}}, datetime_utc="2032-04-19T09:00:00Z")
    text, complete = o.mail_progress()
    assert not complete and "newer eligible message" in text
    # Re-reading an older message does not resolve the unknown newer update.
    add_mail(o, "setup_pool", {"pools": {"SYN-KEY": {"amount_eur": "2"}}})
    assert not o.mail_progress()[1]
    add_mail(o, "setup_pool", {"pools": {"SYN-KEY": {"amount_eur": "3"}}},
             datetime_utc="2032-04-19T09:00:00Z")
    assert o.mail_progress()[1]


def test_old_empty_payload_is_superseded_by_a_newer_proven_key():
    o = mail_fixture("setup_pool", "pools", {})
    assign(o, "setup_pool_key")
    add_mail(o, "setup_pool", {"pools": {"SYN-KEY": {"amount_eur": "2"}}},
             datetime_utc="2032-04-19T09:00:00Z")
    assert o.mail_progress()[1]


def test_bonus_validation_does_not_invent_a_percentage_cap():
    o = mail_fixture("project_bonus", "plans", {"SYN-KEY": {
        "bands": [{"from_eur": "0", "to_eur": None, "rate_percent": "125"}]}})
    assign(o, "bonus_plan_key")
    assert o.mail_progress()[1]


def test_withdrawn_or_future_assignment_does_not_require_a_plan():
    o = mail_fixture("project_bonus", "plans", {})
    assign(o, "bonus_plan_key", datetime_utc="2032-04-21T08:00:00Z")
    assert o.mail_progress()[1]
    assign(o, "bonus_plan_key")
    o.bb.add("clarifications", {"entries": [{"number": "2", "datetime_utc": "2032-04-19T09:00:00Z",
              "facts": [{"type": "withdrawal", "withdrawn_number": "1"}]}]}, "https://fixture.example/withdrawal")
    assert o.mail_progress()[1]


def test_late_bonus_repair_invalidates_empty_extraction(monkeypatch):
    async def scenario():
        o = orch(monkeypatch, asynchronous=True)
        add_spec(o)
        add_material(o)
        o.brief["authorized_records"] = [{"topic": "project_bonus", "sender_localpart": "finance"}]
        assign(o, "bonus_plan_key", position="S-ONE")
        add_mail(o, "project_bonus", {"plans": {}})
        o._cache["mail_message:empty"] = "empty"
        calls = []
        monkeypatch.setattr(o.sites["alpha"], "fresh_start", lambda: calls.append("fresh"), raising=False)
        async def run(site, mission, attempts):
            assert mission.name == "mail"
            assert "mail_message:empty" not in o._cache
            calls.append(mission.name)
            add_mail(o, "project_bonus", {"plans": {"SYN-KEY": {
                "bands": [{"from_eur": "0", "to_eur": None, "rate_percent": "5"}]}}})
        monkeypatch.setattr(o, "run_mission", run)
        await o.reconcile_evidence(o.mission("specification", "Read requirements", ["requirements"], 10,
                                           progress=o.spec_progress),
                                   o.mission("mail", "Read financial approvals", ["mail_message"], 10,
                                             progress=o.mail_progress))
        assert calls == ["fresh", "mail"]
        assert not o.pricing_mail_gaps()
        await o.close()
    asyncio.run(scenario())


def test_repair_keeps_submission_time_reserve(monkeypatch):
    async def scenario():
        o = orch(monkeypatch)
        add_spec(o)
        add_material(o)
        assign(o, "bonus_plan_key", position="S-ONE")
        o.evidence_end = time.time() + 30
        async def forbidden(*args, **kwargs):
            pytest.fail("no new research inside the submission reserve")
        monkeypatch.setattr(o, "run_mission", forbidden)
        await o.reconcile_evidence(o.mission("specification", "Read requirements", ["requirements"], 10,
                                           progress=o.spec_progress),
                                   o.mission("mail", "Read approvals", ["mail_message"], 10,
                                             progress=o.mail_progress))
        assert o.pricing_mail_gaps()
    asyncio.run(scenario())

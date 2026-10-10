"""G-0: every shipped optimization can be switched off again without a code change (synthetic data only)."""
import time

from rfq import flags
from rfq.agent import submit
from rfq.agent.orchestrator import Orchestrator


def test_switch_values_are_read_from_the_environment(monkeypatch):
    for value, expected in (("1", True), ("on", True), ("0", False), ("off", False)):
        monkeypatch.setenv("RFQ_SOME_SWITCH", value)
        assert flags._on("RFQ_SOME_SWITCH", not expected) is expected
    monkeypatch.delenv("RFQ_SOME_SWITCH")
    assert flags._on("RFQ_SOME_SWITCH", True) is True


def test_steps_tool_and_its_guidance_follow_the_switch(monkeypatch):
    names = lambda: [t["function"]["name"] for t in submit.submit_tools()]
    monkeypatch.setattr(flags, "STEPS_TOOL", True)
    assert "steps" in names() and "`steps`" in submit.STEPS_GUIDANCE
    monkeypatch.setattr(flags, "STEPS_TOOL", False)
    assert "steps" not in names() and "steps" not in submit.SINGLE_GUIDANCE


def test_background_extraction_follows_the_switch(monkeypatch):
    run = {"expires_at": (time.time() + 7200) * 1000, "brief": ""}
    o = Orchestrator(run, {"erp": object()}, started=time.time())
    o.brief = {"tender_reference": "XT-1", "sites": {"erp": "erp"}}
    monkeypatch.setattr(flags, "BACKGROUND_EXTRACTION", False)
    assert o.erp_mission("G-1", [{"ref": "A-1"}]).background_extraction is False
    monkeypatch.setattr(flags, "BACKGROUND_EXTRACTION", True)
    assert o.erp_mission("G-1", [{"ref": "A-1"}]).background_extraction is True


def test_split_brief_starts_with_the_core_and_keeps_it(monkeypatch):
    """P-2: the missions can start after a small first call; the whole brief arrives later, its first-half values
    never overwrite the core (reference, scenario date, websites)."""
    import asyncio
    from rfq import llm
    from rfq.agent.prompts import BRIEF_CORE_SYSTEM
    core = {"tender_reference": "XT-1", "scenario_datetime_utc": "2030-01-01T00:00:00Z",
            "sites": {"tender": "t", "specification": "s", "erp": "e", "mail": "m"}}
    full = {"tender_reference": "XT-1-A", "director_localpart": "boss", "bidder_company": "B",
            "authorized_records": [{"topic": "packaging", "subject_keyword": "Pack", "sender_localpart": "log"}]}

    async def ask_json(system, user, **kwargs):
        if system == BRIEF_CORE_SYSTEM:
            return dict(core)
        await asyncio.sleep(0.5)
        return dict(full)
    monkeypatch.setattr(llm, "ask_json", ask_json)

    async def scenario(split: bool):
        monkeypatch.setattr(flags, "BRIEF_SPLIT", split)
        run = {"expires_at": (time.time() + 7200) * 1000, "brief": "Bid on tender (XT-1) by 2030."}
        o = Orchestrator(run, {}, started=time.time())
        t0 = time.monotonic()
        await o.read_brief()
        first = time.monotonic() - t0, dict(o.brief)
        await o.full_brief()
        return first, o.brief
    (seconds, first), merged = asyncio.run(scenario(True))
    assert seconds < 0.3 and first["tender_reference"] == "XT-1" and "director_localpart" not in first
    assert merged["tender_reference"] == "XT-1" and merged["director_localpart"] == "boss" and merged["sites"] == core["sites"]
    (seconds, first), merged = asyncio.run(scenario(False))
    assert seconds >= 0.5 and first["director_localpart"] == "boss" and first["tender_reference"] == "XT-1"


def test_trimmed_schema_only_drops_the_material_description(monkeypatch):
    """X-4: the mail body stays (dropping it flipped is_draft_or_preview in offline replays)."""
    from rfq.agent import prompts
    monkeypatch.setattr(flags, "SCHEMA_TRIM", True)
    trimmed = prompts.kinds()
    assert '"description"' not in trimmed["materials"]["schema"] and '"description"' in prompts.KINDS["materials"]["schema"]
    assert trimmed["mail_message"] == prompts.KINDS["mail_message"]
    monkeypatch.setattr(flags, "SCHEMA_TRIM", False)
    assert prompts.kinds() is prompts.KINDS

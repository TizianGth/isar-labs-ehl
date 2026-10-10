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

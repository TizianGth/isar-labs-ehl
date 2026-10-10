"""An incomplete mission is tried briefly on the other websites: other software splits information differently."""
import asyncio
import time

from rfq.agent.orchestrator import Orchestrator


class _Site:
    def __init__(self, site_id: str):
        self.site_id, self.spawned, self.closed = site_id, 0, 0
        self.page = self

    async def spawn(self):
        self.spawned += 1
        return self

    async def close(self):                 # stands in for the spawned tab's page.close()
        self.closed += 1


def _orchestrator(seconds_left: float = 7200):
    sites = {sid: _Site(sid) for sid in ("tp", "sp", "erp", "mail")}
    o = Orchestrator({"expires_at": (time.time() + 7200) * 1000, "brief": ""}, sites, started=time.time())
    o.evidence_end = time.time() + seconds_left
    o.brief = {"tender_reference": "XT-1", "sites": {"tender": "tp", "specification": "sp", "erp": "erp", "mail": "mail"}}
    return o, sites


def _spec_evidence(o):
    o.bb.add("spec_revisions", {"revisions": [{"revision": "V2", "status_text": "gültig"}]}, "u")
    o.bb.add("requirements", {"revision": "V2", "requirements": [{"ref": "A-1", "catalog_group": "G-1"}]}, "u",
             via="collection")


def _run(o, role, monkeypatch, home_finds=False, finds_on=None):
    calls = []

    async def home(self, site, mission):
        calls.append(("home", site.site_id))
        if home_finds:
            _spec_evidence(self)
        self.status[mission.name] = "complete" if home_finds else "incomplete"

    async def elsewhere(self, site, mission):
        calls.append(("elsewhere", site.site_id, mission.max_steps, "NOT FOUND COMPLETELY" in mission.context))
        if site.site_id == finds_on:
            _spec_evidence(self)

    monkeypatch.setattr(Orchestrator, "run_mission", home)
    monkeypatch.setattr(Orchestrator, "explore_once", elsewhere)
    mission = o.mission("specification", "find it", ["spec_revisions", "requirements"], 30, progress=o.spec_progress)
    asyncio.run(o.run_role(role, mission))
    return calls


def test_complete_mission_stays_home(monkeypatch):
    o, _ = _orchestrator()
    assert _run(o, "specification", monkeypatch, home_finds=True) == [("home", "sp")]


def test_incomplete_mission_tries_the_other_sites_until_complete(monkeypatch):
    o, sites = _orchestrator()
    calls = _run(o, "specification", monkeypatch, finds_on="mail")
    assert calls == [("home", "sp"), ("elsewhere", "tp", 12, True), ("elsewhere", "mail", 12, True)]
    assert o.status["specification"] == "complete"
    assert sites["tp"].spawned == 1 and sites["tp"].closed == 1 and sites["erp"].spawned == 0


def test_no_redispatch_when_evidence_time_is_short(monkeypatch):
    o, _ = _orchestrator(seconds_left=60)
    assert _run(o, "specification", monkeypatch) == [("home", "sp")]


def test_mail_and_erp_missions_are_not_redispatched(monkeypatch):
    o, _ = _orchestrator()
    assert _run(o, "mail", monkeypatch) == [("home", "mail")]

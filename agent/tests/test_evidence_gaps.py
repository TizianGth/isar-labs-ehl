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


class _GapSite:
    def __init__(self, site_id, page=""):
        self.site_id, self.url, self.last_observation, self.tabs, self.closed = site_id, f"http://{site_id}.test", page, [], 0

    async def wait_started(self):
        pass

    async def spawn(self):
        tab = _GapSite(self.site_id, self.last_observation)
        tab.parent = self
        self.tabs.append(tab)
        return tab

    async def close(self):
        self.parent.closed += 1


def _gap_run(monkeypatch, choice: dict, switch: bool = True):
    """The specification is not on the supplier portal (information split differently) but in the tender portal."""
    import asyncio
    import time
    from rfq import flags, llm
    from rfq.agent.orchestrator import Orchestrator
    sites = {"tender": _GapSite("tender", "TITLE: Vergabeportal Dokumente Lastenheft"), "supplier": _GapSite("supplier"),
             "mail": _GapSite("mail"), "erp": _GapSite("erp")}
    asked, missions = [], []

    async def ask_json(system, user, **kwargs):
        asked.append(user)
        return choice

    async def fake_mission(self, site, mission, attempts=2):
        missions.append((mission.name, site.site_id, site in sites.values()))
        if mission.name == "specification+gap":           # the requirements are found on the other website
            self.bb.add("spec_revisions", {"revisions": [{"revision": "R2", "status_text": "gültig"}]}, "u1")
            self.bb.add("requirements", {"revision": "R2", "requirements": [{"ref": "A-1", "catalog_group": "G-1"}]},
                        "u2", "collection")

    async def no_erp(self):
        return []
    monkeypatch.setattr(llm, "ask_json", ask_json)
    monkeypatch.setattr(flags, "GAP_RECOVERY", switch)
    monkeypatch.setattr(Orchestrator, "run_mission", fake_mission)
    monkeypatch.setattr(Orchestrator, "erp_phase", no_erp)
    monkeypatch.setattr(Orchestrator, "tender_progress", lambda self: ("ok", True))
    monkeypatch.setattr(Orchestrator, "mail_progress", lambda self: ("ok", True))

    async def scenario():
        run = {"expires_at": (time.time() + 7200) * 1000, "brief": ""}
        o = Orchestrator(run, sites, started=time.time())
        o.brief = {"tender_reference": "XT-1", "authorized_records": [],
                   "sites": {"tender": "tender", "specification": "supplier", "mail": "mail", "erp": "erp"}}
        await o.gather_evidence()
        return o
    return asyncio.run(scenario()), sites, asked, missions


def test_missing_specification_is_looked_for_on_the_website_the_model_picks(monkeypatch):
    """G-2: the specification mission ends incomplete; the model picks the tender portal from the start pages, a
    short mission there finds the requirements, and only then the specification counts as ended."""
    o, sites, asked, missions = _gap_run(monkeypatch, {"site": "tender", "reason": "documents are listed there"})
    assert ("specification+gap", "tender", False) in missions          # in a new tab of the chosen website
    assert "Lastenheft" in asked[0] and "supplier" not in asked[0].split("CANDIDATES:")[1]
    assert o.spec_progress()[1] and o.done["specification"].is_set()
    assert sites["tender"].closed == 1 and len(missions) == 4        # 3 missions + 1 recovery, nothing else


def test_no_recovery_when_the_model_sees_no_candidate_or_the_switch_is_off(monkeypatch):
    o, sites, asked, missions = _gap_run(monkeypatch, {"site": "none", "reason": "nowhere"})
    assert asked and not any(name.endswith("+gap") for name, _, _ in missions)
    o, sites, asked, missions = _gap_run(monkeypatch, {"site": "tender"}, switch=False)
    assert not asked and not any(name.endswith("+gap") for name, _, _ in missions)


def test_positions_without_requirement_ids_are_matched_by_the_model(monkeypatch):
    """#4: a bill of quantities that does not name requirement ids: one model call matches the positions, so the
    rules price them instead of skipping them; positions that name their requirement need no call."""
    import asyncio
    import time
    from rfq import llm
    from rfq.agent.orchestrator import Orchestrator
    asked = []

    async def ask_json(system, user, **kwargs):
        asked.append(user)
        return {"mapping": {"1": "R-2", "2": "nonsense"}}
    monkeypatch.setattr(llm, "ask_json", ask_json)
    run = {"expires_at": (time.time() + 7200) * 1000, "brief": ""}
    o = Orchestrator(run, {}, started=time.time())
    o.bb.add("positions", {"positions": [{"position": "1", "short_text": "Zylinder 63/500", "quantity": "8", "unit": "Stk"},
                                         {"position": "2", "short_text": "Ventil", "quantity": "2", "unit": "Stk"}]}, "u")
    o.bb.add("requirements", {"revision": "V1", "requirements": [{"ref": "R-1", "bore_mm": "40"},
                                                                 {"ref": "R-2", "bore_mm": "63", "stroke_mm": "500"}]}, "u2")
    asyncio.run(o.map_positions())
    by_id = {p["position"]: p for p in o.positions()}
    assert by_id["1"]["requirement_ref"] == "R-2" and by_id["1"]["quantity"] == "8"
    assert not by_id["2"].get("requirement_ref") and "Zylinder 63/500" in asked[0]
    asked.clear()
    asyncio.run(o.map_positions())                                    # position 2 is still unmapped: asked again
    o2 = Orchestrator(run, {}, started=time.time())
    o2.bb.add("positions", {"positions": [{"position": "1", "quantity": "8", "requirement_ref": "R-1"}]}, "u")
    o2.bb.add("requirements", {"revision": "V1", "requirements": [{"ref": "R-1"}]}, "u2")
    asked.clear()
    asyncio.run(o2.map_positions())
    assert asked == []                                                 # nothing to match: no model call

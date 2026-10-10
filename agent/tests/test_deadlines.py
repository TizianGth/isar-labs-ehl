"""B08 + B02: deadlines hold through slots, hung calls and retries; ended missions wake the ERP phase."""
import asyncio
import time

import pytest

from rfq import llm
from rfq.agent import orchestrator as orch_mod
from rfq.agent.orchestrator import Orchestrator


class _HangingCompletions:
    async def create(self, **kwargs):
        await asyncio.sleep(kwargs.get("timeout", 60))      # behaves like a request that never answers in time
        raise TimeoutError("hung")


class _HangingClient:
    chat = type("C", (), {"completions": _HangingCompletions()})()


def test_hung_model_call_respects_the_deadline(monkeypatch):
    monkeypatch.setattr(llm, "client", lambda: _HangingClient())
    started = time.time()
    with pytest.raises(llm.DeadlineExceeded):
        asyncio.run(llm.chat([{"role": "user", "content": "x"}], deadline=time.time() + 6))
    assert time.time() - started < 9


def test_busy_slots_respect_the_deadline(monkeypatch):
    async def scenario():
        slots = asyncio.Semaphore(1)
        monkeypatch.setattr(llm, "_slots", slots)
        await slots.acquire()                                  # another call holds the only slot
        await llm.chat([{"role": "user", "content": "x"}], deadline=time.time() + 4)
    started = time.time()
    with pytest.raises(llm.DeadlineExceeded):
        asyncio.run(scenario())
    assert time.time() - started < 6


def _orchestrator() -> Orchestrator:
    run = {"expires_at": (time.time() + 7200) * 1000, "brief": ""}
    o = Orchestrator(run, {"erp": object()}, started=time.time())
    o.brief = {"tender_reference": "XT-1", "sites": {"erp": "erp"}}
    return o


def test_budget_is_capped_whatever_the_run_allows():
    o = _orchestrator()
    assert o.end - time.time() <= orch_mod.RUN_BUDGET + 1


def test_failed_specification_wakes_erp_without_waiting():
    async def scenario():
        o = _orchestrator()
        o.done["specification"].set()      # e.g. mission_loop_abort: ended without requirements
        o.done["tender"].set()
        return await asyncio.wait_for(o.erp_phase(), timeout=5)
    assert asyncio.run(scenario()) == []


def test_duplicate_catalogue_groups_start_one_mission(monkeypatch):
    calls = []

    async def fake_run_mission(self, site, mission):
        calls.append(mission.name)
        self.status[mission.name] = "complete"
        return None

    monkeypatch.setattr(Orchestrator, "run_mission", fake_run_mission)

    async def scenario():
        o = _orchestrator()
        o.bb.add("positions", {"positions": [{"position": "1", "requirement_ref": "A-1"},
                                             {"position": "2", "requirement_ref": "A-2"}]}, "u")
        o.bb.add("spec_revisions", {"revisions": [{"revision": "V2", "status_text": "gültig"}]}, "u")
        o.bb.add("requirements", {"revision": "V2", "requirements": [{"ref": "A-1", "catalog_group": "G-7"},
                                                                     {"ref": "A-2", "catalog_group": "g-7 "}]}, "u")
        o.done["specification"].set()
        o.done["tender"].set()
        await asyncio.wait_for(o.erp_phase(), timeout=5)
        return o
    o = asyncio.run(scenario())
    assert calls == ["erp:G-7"] and o.erp_complete == {"G-7"}


def test_empty_extraction_is_reported_as_wrong_page():
    from rfq.agent.orchestrator import _summary
    assert "NOTHING of kind 'spec_revisions'" in _summary("spec_revisions", {"revisions": []})
    assert "NOTHING" not in _summary("spec_revisions", {"revisions": [{"revision": "V1", "status_text": "gültig"}]})


def test_erp_missions_end_with_the_phase_and_never_share_a_tab(monkeypatch):
    """W02: cancelling the phase cancels its missions; without an own tab, missions wait instead of sharing."""
    running, finished = [], []

    async def slow_mission(self, site, mission):
        running.append((id(site), mission.name))
        try:
            await asyncio.sleep(30)
        finally:
            finished.append(mission.name)

    async def no_tab(self, base):
        return base

    monkeypatch.setattr(Orchestrator, "run_mission", slow_mission)
    monkeypatch.setattr(Orchestrator, "_tab", no_tab)

    async def scenario():
        o = _orchestrator()
        o.bb.add("positions", {"positions": [{"position": "1", "requirement_ref": "A-1"},
                                             {"position": "2", "requirement_ref": "A-2"}]}, "u")
        o.bb.add("spec_revisions", {"revisions": [{"revision": "V2", "status_text": "gültig"}]}, "u")
        o.bb.add("requirements", {"revision": "V2", "requirements": [{"ref": "A-1", "catalog_group": "G-1"},
                                                                     {"ref": "A-2", "catalog_group": "G-2"}]}, "u")
        o.done["specification"].set()
        o.done["tender"].set()
        phase = asyncio.create_task(o.erp_phase())
        await asyncio.sleep(1.5)
        phase.cancel()
        try:
            await phase
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.1)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    leftover = asyncio.run(scenario())
    assert len(running) == 1                 # the second group waited for the base tab instead of sharing it
    assert finished == ["erp:G-1"]           # and the running mission was cancelled with the phase
    assert not leftover


def test_decoy_mails_do_not_satisfy_a_topic():
    """W03: a topic counts as found only with a record the rules would use."""
    o = _orchestrator()
    o.brief.update({"scenario_datetime_utc": "2030-03-10T09:00:00Z", "director_localpart": "boss",
                    "authorized_records": [{"topic": "packaging", "subject_keyword": "Pack", "sender_localpart": "log"}]})
    o.bb.add("mail_list", {"mailbox_address": "sales@bidder.example", "folder": "inbox", "messages": []}, "u")
    mail = lambda **kw: {"topic": "packaging", "reference": "XT-1", "sender": "log@bidder.example",
                         "datetime_utc": "2030-03-09T10:00:00Z", "is_draft_or_preview": False, **kw}
    for i, decoy in enumerate([mail(sender="log@other.example"), mail(datetime_utc="2030-03-11T10:00:00Z"),
                               mail(is_draft_or_preview=True), mail(reference="XT-1-A"), mail(sender="boss@bidder.example")]):
        o.bb.add("mail_message", decoy, f"u{i}")
    o.bb.add("mail_message", mail(datetime_utc=None), "u-undated")
    text, _ = o.mail_progress()
    assert "packaging ('Pack')" in text                     # still missing: only decoys so far
    assert "packaging: after the scenario date, draft/preview, no readable date, not from the authorized sender" in text
    o.bb.add("mail_message", mail(sender="Lo Gistik <LOG@bidder.example>"), "u-valid")   # display name form
    text, _ = o.mail_progress()
    assert "packaging ('Pack')" not in text and "NOT usable" not in text


def test_concurrent_identical_extractions_share_one_call(monkeypatch):
    from rfq import llm as llm_module
    calls = []

    async def fake_ask_json(system, user, **kwargs):
        calls.append(1)
        await asyncio.sleep(0.2)
        return {"positions": [{"position": "1", "quantity": "2"}]}

    monkeypatch.setattr(llm_module, "ask_json", fake_ask_json)

    async def scenario():
        o = _orchestrator()
        page = "URL: x\nPos 1: 2 Stk"
        return await asyncio.gather(o.extract("positions", page, "u1"), o.extract("positions", page, "u2")), o
    (first, second), o = asyncio.run(scenario())
    assert len(calls) == 1 and "already extracted" in second and len(o.bb.get("positions")) == 1


def test_long_documents_are_read_in_parts_not_cut(monkeypatch):
    from rfq import llm as llm_module
    from rfq.agent import orchestrator as orch

    async def fake_ask_json(system, user, **kwargs):
        found = [{"position": m} for m in ("A", "B") if f"Position {m} hier" in user]
        return {"positions": found}

    monkeypatch.setattr(llm_module, "ask_json", fake_ask_json)
    monkeypatch.setattr(orch, "CHUNK_CHARS", 1000)
    text = "URL: x\nPosition A hier\n" + "\n".join("füllzeile " * 8 for _ in range(300)) + "\nPosition B hier"

    async def scenario():
        o = _orchestrator()
        summary = await o.extract("positions", text, "u")
        return summary, o.bb.get("positions")[0]["data"]
    summary, data = asyncio.run(scenario())
    assert [p["position"] for p in data["positions"]] == ["A", "B"]     # the end of the document is not lost


def test_site_memory_shares_page_types_not_values():
    from types import SimpleNamespace
    o = _orchestrator()
    site = SimpleNamespace(url="http://erp.test", hung={"http://erp.test/slow?id=9": 2})
    o.bb.add("materials", {"materials": [{"material_number": "M-1"}]}, "http://erp.test/cond?material=M-1&view=scales")
    o.bb.add("materials", {"materials": [{"material_number": "M-2"}]}, "http://erp.test/cond?material=M-2&view=scales")
    o.bb.add("materials", {"materials": []}, "http://erp.test/empty")                     # empty pages teach nothing
    o.bb.add("positions", {"positions": [{"position": "1"}]}, "http://other.test/lv")        # other website
    memory = o.site_memory(site)
    assert "/cond?material=*&view=scales" in memory and "M-1" not in memory
    assert "/empty" not in memory and "other.test" not in memory and "/lv" not in memory
    assert "/slow" not in memory          # one page that hung must not ban its whole page type


def test_a_cancelled_extraction_does_not_cancel_the_one_waiting_for_it(monkeypatch):
    from rfq import llm as llm_module
    calls = []

    async def fake_ask_json(system, user, **kwargs):
        calls.append(1)
        await asyncio.sleep(0.3)
        return {"positions": [{"position": "1"}]}

    monkeypatch.setattr(llm_module, "ask_json", fake_ask_json)

    async def scenario():
        o = _orchestrator()
        page = "URL: x\nPos 1"
        first = asyncio.create_task(o.extract("positions", page, "u1"))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(o.extract("positions", page, "u2"))
        await asyncio.sleep(0.05)
        first.cancel()                                  # e.g. its mission ended
        return await second
    result = asyncio.run(scenario())
    assert "1" in result and len(calls) == 2           # the waiter extracted the page itself


def test_every_page_of_a_paginated_qa_list_is_required():
    """Run 6c71…: Q&A pages were missing, the tender mission still counted as complete, and a missed amendment
    turned the whole bid into no_bid."""
    o = _orchestrator()
    o.bb.add("positions", {"positions": [{"position": "1", "quantity": "2", "unit": "Stk", "requirement_ref": "A-1"}]}, "u")
    for page in (1, 2, 3):
        o.bb.add("clarifications", {"page": page, "pages_total": 6, "entries": [{"number": str(page), "facts": []}]},
                 f"u{page}", "collection")
    text, complete = o.tender_progress()
    assert not complete and "NOT read yet: [4, 5, 6] of 6" in text
    for page in (4, 5, 6):
        o.bb.add("clarifications", {"page": str(page), "pages_total": "6", "entries": []}, f"u{page}", "collection")
    assert o.tender_progress()[1]


def test_qa_without_page_numbers_keeps_the_old_rule():
    o = _orchestrator()
    o.bb.add("positions", {"positions": [{"position": "1", "requirement_ref": "A-1"}]}, "u")
    o.bb.add("clarifications", {"entries": [{"number": "7", "facts": []}]}, "u1", "collection")
    assert o.tender_progress()[1]


def test_no_bid_is_checked_again_with_fresh_qa_before_it_is_accepted(monkeypatch):
    from types import SimpleNamespace
    from rfq.rules.resolve import Result
    o = _orchestrator()
    site = SimpleNamespace(collections={"c1": object()}, fresh=[])
    site.fresh_start = lambda: site.fresh.append(1)
    monkeypatch.setattr(Orchestrator, "site_for", lambda self, role: site)
    o._cache = {"clarificationsabc": "old", "materialsdef": "keep"}
    missions = []

    async def fake_mission(self, s, mission, attempts=2):
        missions.append((mission.name, attempts))
        self.bb.add("clarifications", {"page": 1, "pages_total": 1, "entries": [{"number": "9", "facts": [
            {"type": "tech_amendment", "revision": "R1", "requirement": "A-1", "attribute": "stroke", "value": "480"}]}]},
            "u-again", "collection")

    def fake_resolve(self):
        amended = any(f.get("type") == "tech_amendment" for i in self.bb.get("clarifications")
                      for e in i["data"]["entries"] for f in e["facts"])
        return Result({"decision": "bid" if amended else "no_bid"}, ["no active matching material for ['1']: no_bid"], [], {})

    monkeypatch.setattr(Orchestrator, "run_mission", fake_mission)
    monkeypatch.setattr(Orchestrator, "resolve", fake_resolve)
    checked = asyncio.run(o.recheck_no_bid(o.resolve()))
    assert checked.answer["decision"] == "bid" and missions == [("recheck", 1)]
    assert site.fresh and site.collections == {} and o._cache == {"materialsdef": "keep"}
    assert o.evidence_digest()["amendments"] == [["R1", "A-1", "stroke", "480"]]


def test_a_slow_website_blocks_only_its_own_missions(monkeypatch):
    """N3: websites load in the background; waiting for one never waits for another, and a failed start is no error."""
    from rfq.browser.site import Site

    async def scenario():
        fast, slow, broken = (Site(n, f"http://{n}.test", {}, None) for n in ("fast", "slow", "broken"))
        delays = {"fast": 0.05, "slow": 2.0}

        async def start(self):
            if self.site_id == "broken":
                raise RuntimeError("unreachable")
            await asyncio.sleep(delays[self.site_id])
        monkeypatch.setattr(Site, "start", start)
        for site in (fast, slow, broken):
            site.begin_start(timeout=5)
        began = time.time()
        await fast.wait_started()
        fast_wait = time.time() - began
        await broken.wait_started()
        await slow.wait_started()
        return fast_wait, time.time() - began
    fast_wait, all_wait = asyncio.run(scenario())
    assert fast_wait < 0.5 and all_wait >= 1.9


class _FakeSite:
    """Just enough of a Site for the explorer loop (no browser)."""
    site_id, url = "erp", "http://erp.test"

    def __init__(self):
        self.page = type("P", (), {"url": "http://erp.test/list"})()
        self.clicks = []

    async def wait_started(self):
        pass

    async def observe(self, limit=0):
        return f"URL: {self.page.url}?view={len(self.clicks)}\n[e1] link \"Detail\" -> /detail"

    async def act(self, name, args):
        self.clicks.append((name, time.time()))
        await asyncio.sleep(0.25)          # a page load
        return "clicked"

    def absolute(self, url):
        return url if url.startswith("http") else "http://erp.test" + url

    def was_seen(self, url):
        return True

    async def read_url(self, url):
        return f"URL: {url}\npage text"


def _scripted(monkeypatch, calls: list, prompts: list):
    async def chat(messages, **kwargs):
        prompts.append(messages[-1]["content"])
        return {"content": "", "tool_calls": [calls.pop(0)]}
    monkeypatch.setattr(llm, "chat", chat)


def test_background_extraction_lets_the_mission_navigate_meanwhile(monkeypatch):
    """Evidence speed-up: extract_urls returns at once; the model clicks on while the page is extracted, sees the
    result in a later step, and the mission completes only once the extraction has landed."""
    from rfq.agent.explorer import Explorer, Mission
    done, prompts = {"facts": False, "at": None}, []

    async def extractor(kind, text, url, via="page"):
        await asyncio.sleep(0.6)
        done.update(facts=True, at=time.time())
        return "M-1: price"

    _scripted(monkeypatch, [("extract_urls", {"urls": ["/detail"], "kind": "materials"}),
                            ("click", {"ref": "e1"}), ("click", {"ref": "e1"}), ("click", {"ref": "e1"})], prompts)
    site = _FakeSite()
    mission = Mission("erp:G", "goal", {}, max_steps=6, deadline=time.time() + 60,
                      progress=lambda: ("facts" if done["facts"] else "missing", done["facts"]),
                      background_extraction=True)
    explorer = asyncio.run(Explorer(site, mission, extractor).run())
    assert site.clicks and site.clicks[0][1] < done["at"]            # navigated before the extraction finished
    assert "STILL EXTRACTING IN THE BACKGROUND" in prompts[1]
    assert explorer.status == "complete" and not explorer.pending
    assert any("(background)" in h and "M-1: price" in h for h in explorer.history)


def test_finish_waits_for_background_results_and_is_postponed_if_work_remains(monkeypatch):
    from rfq.agent.explorer import Explorer, Mission
    state, prompts = {"pages": 0}, []

    async def extractor(kind, text, url, via="page"):
        await asyncio.sleep(0.3)
        state["pages"] += 1
        return "found another material"

    _scripted(monkeypatch, [("extract_urls", {"urls": ["/a"], "kind": "materials"}),
                            ("finish", {"summary": "done"}),
                            ("extract_urls", {"urls": ["/b"], "kind": "materials"}),
                            ("finish", {"summary": "done"})], prompts)
    mission = Mission("erp:G", "goal", {}, max_steps=6, deadline=time.time() + 60,
                      progress=lambda: (f"pages {state['pages']}/2", state["pages"] >= 2),
                      background_extraction=True)
    explorer = asyncio.run(Explorer(_FakeSite(), mission, extractor).run())
    assert any("postponed" in h for h in explorer.history)       # first finish: results came, checklist incomplete
    assert state["pages"] == 2 and explorer.status in ("complete", "finished") and not explorer.pending


def test_a_slow_new_erp_tab_does_not_hold_up_the_other_groups(monkeypatch):
    started = {}
    calls = {"n": 0}

    async def tab(self, base):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(3)          # this group's tab takes long to load
        return object()

    async def mission_run(self, site, mission):
        started[mission.name] = time.time()
        self.status[mission.name] = "complete"

    monkeypatch.setattr(Orchestrator, "_tab", tab)
    monkeypatch.setattr(Orchestrator, "run_mission", mission_run)

    async def scenario():
        o = _orchestrator()
        o.bb.add("positions", {"positions": [{"position": str(i), "requirement_ref": f"A-{i}"} for i in (1, 2, 3)]}, "u")
        o.bb.add("spec_revisions", {"revisions": [{"revision": "V2", "status_text": "gültig"}]}, "u")
        o.bb.add("requirements", {"revision": "V2", "requirements": [
            {"ref": f"A-{i}", "catalog_group": f"G-{i}"} for i in (1, 2, 3)]}, "u")
        o.done["specification"].set()
        o.done["tender"].set()
        began = time.time()
        await o.erp_phase()
        return {k: v - began for k, v in started.items()}
    times = asyncio.run(scenario())
    assert len(times) == 3 and sorted(times.values())[1] < 1.5     # base + the fast tab start without waiting


class _RoleSite:
    def __init__(self, site_id, fail_spawn=False):
        self.site_id, self.url, self.fail_spawn, self.tabs = site_id, f"http://{site_id}.test", fail_spawn, []

    async def wait_started(self):
        pass

    async def spawn(self):
        if self.fail_spawn:
            raise RuntimeError("no new tab")
        tab = _RoleSite(self.site_id)
        self.tabs.append(tab)
        return tab


def _shared_site_run(monkeypatch, fail_spawn: bool):
    portal = _RoleSite("portal", fail_spawn)
    sites = {"portal": portal, "erp": _RoleSite("erp"), "mail": _RoleSite("mail")}
    runs = []

    async def fake_mission(self, site, mission, attempts=2):
        runs.append((mission.name, site, time.time()))
        await asyncio.sleep(0.3)
        runs.append((mission.name + ":end", site, time.time()))

    async def no_erp(self):
        return []
    from rfq import flags
    monkeypatch.setattr(flags, "GAP_RECOVERY", False)            # this test is about tabs, not about recovery
    monkeypatch.setattr(Orchestrator, "run_mission", fake_mission)
    monkeypatch.setattr(Orchestrator, "erp_phase", no_erp)

    async def scenario():
        run = {"expires_at": (time.time() + 7200) * 1000, "brief": ""}
        o = Orchestrator(run, sites, started=time.time())
        o.brief = {"tender_reference": "XT-1", "authorized_records": [],
                   "sites": {"tender": "portal", "specification": "portal", "mail": "mail", "erp": "erp"}}
        await o.gather_evidence()
        return o
    o = asyncio.run(scenario())
    at = {name: (site, t) for name, site, t in runs}
    return portal, o, at


def test_two_roles_on_one_website_get_their_own_tabs(monkeypatch):
    """G-1: information split differently: the specification lives on the tender website. Each mission gets its
    own tab and both run at the same time."""
    portal, o, at = _shared_site_run(monkeypatch, fail_spawn=False)
    assert at["tender"][0] is portal and at["specification"][0] is portal.tabs[0]
    assert at["specification"][1] < at["tender:end"][1]               # in parallel
    assert o.role_site("specification") is portal.tabs[0] and o.role_site("tender") is portal


def test_without_a_new_tab_the_missions_take_turns(monkeypatch):
    portal, o, at = _shared_site_run(monkeypatch, fail_spawn=True)
    assert at["tender"][0] is portal and at["specification"][0] is portal
    first, second = sorted(("tender", "specification"), key=lambda n: at[n][1])
    assert at[second][1] >= at[first + ":end"][1]                      # never two missions on one tab at once

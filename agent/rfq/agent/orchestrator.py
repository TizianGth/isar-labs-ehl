"""Orchestrator: brief -> parallel evidence missions (one browser per website) -> rules -> submission.

Phases and deadlines follow docs/02-architecture.md. Every mission gets a progress checklist computed from the
blackboard, so the model always knows what is still missing and missions end as soon as their evidence is complete.
Mission ends are events: nothing waits for a mission that already failed; an incomplete mission gets at most one
recovery attempt with its failure context. Our own time budget applies whatever the run allows (test runs give 2 h,
scored runs 20 min). Every phase degrades gracefully, and the submission phase always runs.
"""
import asyncio
import hashlib
import json
import os
import re
import time
from urllib.parse import parse_qsl, urlparse

from .. import flags, llm, metrics
from ..browser.site import Site
from ..log import log
from ..rules import parse
from ..rules.resolve import Context, Result, build
from .explorer import Explorer, Mission
from .prompts import BRIEF_SYSTEM, EXTRACT_SYSTEM, GAP_GOAL, KINDS, SITE_CHOICE_SYSTEM
from .submit import submit_bid

# Qwen's reasoning combined with JSON output can run for minutes on long inputs; measured off by default.
THINKING_EXTRACT = os.environ.get("RFQ_THINKING_EXTRACT") == "1"
RUN_BUDGET = float(os.environ.get("RFQ_RUN_BUDGET", "1080"))          # our own cap in seconds (18 min)
SUBMIT_RESERVE = float(os.environ.get("RFQ_SUBMIT_RESERVE", "420"))   # seconds kept for the submission phase
CHUNK_CHARS, MAX_CHUNKS = 60000, 4      # as before up to 60k chars in one call; longer documents in parts (≤ 240k)
MATERIAL_REQUIRED = ["status_meaning", "bore_mm", "stroke_mm", "pressure_bar", "mounting", "unrestricted_stock",
                     "lead_time_stock_weeks", "lead_time_production_weeks", "pieces_per_carton"]


class Blackboard:
    """Append-only store of extractions: kind -> [{"data", "url", "via", "t"}]."""

    def __init__(self):
        self.items: dict[str, list[dict]] = {}

    def add(self, kind: str, data: dict, url: str, via: str = "page") -> None:
        self.items.setdefault(kind, []).append({"data": data, "url": url, "via": via, "t": time.time()})

    def get(self, kind: str) -> list[dict]:
        return self.items.get(kind, [])

    def dump(self) -> dict:
        return {k: [{"url": i["url"], "via": i.get("via", "page"), "data": i["data"]} for i in v] for k, v in self.items.items()}

    def load(self, dumped: dict) -> None:
        """Re-use the evidence of an earlier run (dev: iterate on rules/submission without re-exploring)."""
        for kind, items in dumped.items():
            for item in items:
                item = dict(item)
                url, via = item.pop("url", ""), item.pop("via", "page")
                self.add(kind, item["data"] if set(item) == {"data"} else item, url, via)


class Orchestrator:
    def __init__(self, run: dict, sites: dict[str, Site], started: float | None = None):
        self.run, self.sites = run, sites
        self.bb = Blackboard()
        started = started or time.time()
        self.end = min(run["expires_at"] / 1000 - 30, started + RUN_BUDGET)
        self.evidence_end = self.end - SUBMIT_RESERVE
        llm.set_run_deadline(self.end)
        self.brief: dict = {}
        self.result: Result | None = None
        self.status: dict[str, str] = {}                  # mission -> last status
        self.done = {name: asyncio.Event() for name in ("tender", "specification", "mail")}
        self.erp_complete: set[str] = set()               # catalogue groups whose ERP mission completed its checklist
        self._cache: dict[str, str] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self.role_sites: dict[str, Site] = {}             # role -> the tab its missions use (assign_role_sites)
        self._turns: dict[int, asyncio.Lock] = {}         # one mission at a time per tab
        log("budget", seconds=round(self.end - started), evidence_seconds=round(self.evidence_end - started))

    # ---------------------------------------------------------------- extraction helper (model-selected pages only)
    async def extract(self, kind: str, text: str, url: str, via: str = "page") -> str:
        spec = KINDS.get(kind)
        if spec is None:
            return f"ERROR: unknown kind {kind}; use one of {list(KINDS)}"
        key = kind + hashlib.sha1(re.sub(r"\[(?:f\d+)?e\d+\]", "", text.split("\n", 1)[-1]).encode()).hexdigest()
        if key in self._cache:
            return "already extracted earlier: " + self._cache[key]
        if key in self._inflight:                    # the same page is being extracted right now: share its result
            shared = await asyncio.shield(self._inflight[key])
            if shared is not None:
                return "already extracted earlier: " + shared
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            summary = await self._extract_new(kind, spec, text, url, via)
            if summary.startswith("extraction returned nothing"):
                return summary
            self._cache[key] = summary               # empty/failed results are not cached as facts
            return summary
        finally:   # waiters get the summary, or None (failed/cancelled here): then they extract themselves
            if self._inflight.get(key) is future:
                del self._inflight[key]
            future.set_result(self._cache.get(key))

    async def _extract_new(self, kind: str, spec: dict, text: str, url: str, via: str) -> str:
        # Long documents are read in parts (not silently cut): each part is extracted, list fields are merged.
        parts = _chunks(text, CHUNK_CHARS)
        skipped = 0
        if len(parts) > MAX_CHUNKS:              # keep the beginning and the end (closing sections matter too)
            skipped = len(parts) - MAX_CHUNKS
            parts = parts[:MAX_CHUNKS - 1] + parts[-1:]
            log("extract_parts_skipped", kind=kind, url=url[:160], skipped=skipped)
        results = await asyncio.gather(*(llm.ask_json(EXTRACT_SYSTEM, extraction_prompt(
            kind, spec, part, url, self.brief.get("tender_reference"), self.brief.get("scenario_datetime_utc"),
            i, len(parts)), thinking=THINKING_EXTRACT, tag=f"extract:{kind}") for i, part in enumerate(parts)))
        data = _merge_parts(kind, [r for r in results if r])
        if not data:
            return "extraction returned nothing"
        self.bb.add(kind, data, url, via)
        metrics.mark(f"first:{kind}")
        metrics.add(f"extractions:{kind}")
        log("extracted", kind=kind, url=url[:160], via=via, parts=len(parts), data=data)
        note = f" (very long document: {skipped} middle parts were not read)" if skipped else ""
        return _summary(kind, data) + note

    # ---------------------------------------------------------------- brief
    async def read_brief(self) -> None:
        brief = self.run["brief"]
        self.brief = await llm.ask_json(BRIEF_SYSTEM, brief, thinking=False, tag="brief")
        ref = parse.clean(self.brief.get("tender_reference"))
        if not ref or ref not in brief:   # the reference must be copied verbatim from the brief
            candidates = re.findall(r"\(([A-Z]{1,5}-[\w-]+)\)", brief)
            log("brief_reference_fallback", model=ref, candidates=candidates)
            if candidates:
                self.brief["tender_reference"] = candidates[0]
        metrics.mark("brief_done")
        log("brief", data=self.brief)

    def site_for(self, role: str) -> Site:
        site_id = (self.brief.get("sites") or {}).get(role)
        if site_id in self.sites:
            return self.sites[site_id]
        order = ["tender", "specification", "erp", "mail"]
        return list(self.sites.values())[order.index(role) % len(self.sites)]

    def role_site(self, role: str) -> Site:
        """The tab a role works on: its own tab when it shares the website with another role."""
        return self.role_sites.get(role) or self.site_for(role)

    def turn(self, site) -> asyncio.Lock:
        """A tab serves one mission at a time."""
        return self._turns.setdefault(id(site), asyncio.Lock())

    async def assign_role_sites(self) -> None:
        """Information may sit on other websites than usual, so two roles can map to the same website. The later
        role gets its own tab of that website (same session); if no tab can be opened, both missions use the
        website's tab one after the other (see `turn`)."""
        first: dict[int, str] = {}
        for role in ("tender", "specification", "mail", "erp"):
            site = self.site_for(role)
            if flags.TAB_ISOLATION and id(site) in first:
                try:
                    await site.wait_started()
                    self.role_sites[role] = await site.spawn()
                    log("role_tab", role=role, site=site.site_id, shared_with=first[id(site)])
                    continue
                except Exception as error:
                    log("role_tab_failed", role=role, site=site.site_id, error=f"{type(error).__name__}: {error}"[:200])
            first.setdefault(id(site), role)
            self.role_sites[role] = site

    def mission(self, name: str, goal: str, kinds: list[str], steps: int, progress=None, context: str = "") -> Mission:
        return Mission(name=name, goal=goal, kinds={k: KINDS[k]["describe"] for k in kinds}, max_steps=steps,
                       deadline=self.evidence_end, context=context, progress=progress)

    def site_memory(self, site) -> str:
        """What this run has learned about a website (no fixed knowledge): the kinds of pages where facts of each kind
        were found. Shared by all missions on that site, at no model cost."""
        origin = urlparse(site.url).netloc
        found: dict[str, set[str]] = {}
        for kind, items in self.bb.items.items():
            for item in items:
                url = urlparse(str(item["url"]).split("#")[0])
                if url.netloc == origin and not _empty(kind, item["data"]):
                    found.setdefault(kind, set()).add(_page_pattern(url))
        lines = [f"- {kind}: {', '.join(sorted(patterns)[:6])}" for kind, patterns in sorted(found.items())]
        return ("LEARNED IN THIS RUN ABOUT THIS WEBSITE (page types where facts were found; '*' = any value):\n"
                + "\n".join(lines)) if lines else ""

    def _remembering(self, site, mission: Mission) -> Mission:
        """The mission's checklist also shows the site memory (updated every step)."""
        progress = mission.progress
        if progress is None or getattr(progress, "with_memory", False):
            return mission

        def with_memory():
            text, complete = progress()
            memory = self.site_memory(site)
            return (f"{text}\n\n{memory}" if memory else text), complete

        with_memory.with_memory = True
        with_memory.inner = progress          # the checklist without this website's memory (for other websites)
        mission.progress = with_memory
        return mission

    async def run_mission(self, site: Site, mission: Mission, attempts: int = 2) -> Explorer | None:
        """Run a mission; if it ends incomplete and time allows, recovery attempts with the failure context."""
        mission = self._remembering(site, mission)
        explorer = None
        for attempt in range(attempts):
            if mission.deadline - time.time() < 60:
                break
            explorer = await Explorer(site, mission, self.extract).run()
            complete = mission.progress()[1] if mission.progress else explorer.status in ("complete", "finished")
            self.status[mission.name] = "complete" if complete else explorer.status
            log("mission_status", mission=mission.name, attempt=attempt + 1, status=self.status[mission.name],
                missing=explorer.missing)
            if complete or explorer.status in ("deadline",):
                break
            checklist = mission.progress()[0] if mission.progress else ""
            mission = Mission(**{**mission.__dict__, "max_steps": max(12, mission.max_steps // 2),
                                 "context": mission.context + "\n\nPREVIOUS ATTEMPT ENDED WITHOUT COMPLETING THE MISSION "
                                 f"(status {explorer.status}; missing: {', '.join(explorer.missing) or '-'}).\n"
                                 f"Still missing per checklist:\n{checklist}\nIts last steps:\n"
                                 + "\n".join(explorer.history[-8:]) + "\nDo NOT repeat what failed; take a different route."})
        return explorer

    # ---------------------------------------------------------------- progress checklists (from the blackboard)
    def positions(self) -> list[dict]:
        found: dict[str, dict] = {}
        for item in self.bb.get("positions"):
            for p in item["data"].get("positions") or []:
                if p.get("position"):
                    found[parse.clean(p["position"])] = p
        return list(found.values())

    def tender_progress(self) -> tuple[str, bool]:
        positions = self.positions()
        qa = self.bb.get("clarifications")
        entries = {parse.norm_id(e.get("number")) for i in qa for e in i["data"].get("entries") or [] if e.get("number")}
        collected = any(i.get("via") == "collection" for i in qa)
        unread, total = self.qa_pages_unread()
        lines = [
            f"[{'x' if positions else ' '}] positions: " + (", ".join(f"{p['position']} {p.get('quantity')} {p.get('unit')} "
                                                                       f"-> {p.get('requirement_ref')}" for p in positions) or "not found yet"),
            f"[{'x' if entries and collected and not unread else ' '}] Q&A entries: {len(entries)}" + (
                "" if collected or not entries else " (only single pages extracted: read ALL pages with read_all_pages)")
            + (f"; Q&A list pages NOT read yet: {unread} of {total} (open them and extract kind 'clarifications')"
               if unread else ""),
        ]
        return "\n".join(lines), bool(positions and entries and collected and not unread)

    def qa_pages_unread(self) -> tuple[list[int], int]:
        """Pages of a paginated Q&A list that were not extracted (only when the pages state their numbers)."""
        qa = self.bb.get("clarifications")
        total = max((parse.integer(i["data"].get("pages_total")) or 0 for i in qa), default=0)
        read = {parse.integer(i["data"].get("page")) for i in qa} - {None}
        if not total or not read or total > 50:
            return [], total
        return sorted(set(range(1, total + 1)) - read), total

    def current_revision(self) -> str | None:
        current = None
        for item in self.bb.get("spec_revisions"):
            for r in item["data"].get("revisions") or []:
                if parse.revision_is_current(r):
                    current = parse.norm_id(r.get("revision"))
        return current

    def requirements(self) -> dict[str, dict]:
        current, found = self.current_revision(), {}
        for item in self.bb.get("requirements"):
            rev = parse.norm_id(item["data"].get("revision"))
            if current and rev and rev != current:
                continue
            for r in item["data"].get("requirements") or []:
                if r.get("ref"):
                    found[parse.norm_id(r["ref"])] = {**r, "revision": rev}
        return found

    def needed_requirements(self) -> dict[str, dict]:
        reqs = self.requirements()
        needed = {parse.norm_id(p.get("requirement_ref")) for p in self.positions() if p.get("requirement_ref")}
        return {k: v for k, v in reqs.items() if k in needed} if needed else reqs

    def spec_progress(self) -> tuple[str, bool]:
        current, reqs = self.current_revision(), self.requirements()
        needed = {parse.norm_id(p.get("requirement_ref")) for p in self.positions() if p.get("requirement_ref")}
        missing = sorted(needed - set(reqs))
        lines = [f"[{'x' if current else ' '}] revision marked current: {current or 'unknown (extract the revision list with statuses)'}",
                 f"[{'x' if reqs else ' '}] requirements of the current revision: {sorted(reqs) or 'none yet'}"]
        if needed:
            lines.append(f"[{'x' if not missing else ' '}] requirements referenced by the tender positions: "
                         f"{sorted(needed)}" + (f" — missing {missing}" if missing else ""))
        collected = any(i.get("via") == "collection" for i in self.bb.get("requirements"))
        complete = bool(current and reqs and (not missing if needed else collected))
        return "\n".join(lines), complete

    def materials(self) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        for item in self.bb.get("materials"):
            for m in item["data"].get("materials") or []:
                num = parse.norm_id(m.get("material_number"))
                if not num:
                    continue
                target = merged.setdefault(num, {"material_number": m["material_number"], "conditions": [], "records": None})
                for k, v in m.items():
                    if k == "conditions":
                        target["conditions"] += [c for c in v or [] if c.get("scales")]
                    elif k == "condition_records_total" and v:
                        target["records"] = parse.integer(v)
                    elif v not in (None, "", []):
                        target[k] = v
        return merged

    def erp_progress(self, group: str, reqs: list[dict] | None = None) -> tuple[str, bool]:
        mats = [m for m in self.materials().values() if parse.norm_id(m.get("catalog_group")) == parse.norm_id(group)]
        if not mats:
            return f"[ ] no materials of group {group} found yet (search for the group key)", False
        lines, complete = [], True
        for m in mats:
            mismatch = _mismatch(m, reqs or [])
            if mismatch:
                lines.append(f"[x] {m['material_number']}: does not fit the requirement ({mismatch}); no more data needed")
                continue
            missing = [f for f in MATERIAL_REQUIRED if m.get(f) in (None, "", [])]
            if m.get("status_meaning") != "blocked":
                records = len({json.dumps(c.get("valid_from")) + json.dumps(c.get("currency")) for c in m["conditions"]})
                if not records:
                    missing.append("price condition records WITH quantity scales (open the scales view via its link)")
                elif m.get("records") and records < m["records"]:
                    missing.append(f"scales of all {m['records']} condition records (have {records})")
            else:
                missing = [f for f in missing if f in ("bore_mm", "stroke_mm", "pressure_bar", "mounting")]
            complete &= not missing
            lines.append(f"[{'x' if not missing else ' '}] {m['material_number']} ({m.get('status_meaning') or 'status ?'})"
                         + (f": missing {', '.join(missing)}" if missing else ": complete"))
        return "\n".join(lines), complete

    def mail_progress(self) -> tuple[str, bool]:
        ref = parse.norm_id(self.brief.get("tender_reference"))
        pages: dict[str, set[int]] = {}
        totals: dict[str, int] = {}
        listed = {}
        for item in self.bb.get("mail_list"):
            folder = parse.clean(item["data"].get("folder")).lower() or "?"
            pages.setdefault(folder, set()).add(parse.integer(item["data"].get("page")) or 1)
            if parse.integer(item["data"].get("pages_total")):
                totals[folder] = parse.integer(item["data"].get("pages_total"))
            for m in item["data"].get("messages") or []:
                subject = parse.norm_id(m.get("subject"))
                if ref and ref in subject and "TAN" not in subject and m.get("url"):
                    listed[_url_key(m["url"])] = parse.clean(m.get("subject"))
        done = {_url_key(i["url"]) for i in self.bb.get("mail_message")}
        found, unusable = set(), {}
        for i in self.bb.get("mail_message"):
            topic, problem = i["data"].get("topic"), self._mail_problem(i["data"], ref)
            if topic and problem is None:
                found.add(topic)
            elif topic and problem != "other reference":
                unusable.setdefault(topic, set()).add(problem)
        wanted = {"discount": "Rabatt / discount approval"} | {
            r.get("topic"): r.get("subject_keyword") for r in self.brief.get("authorized_records") or [] if r.get("topic")}
        absent = {topic: keyword for topic, keyword in wanted.items() if topic not in found}
        missing = [f"{subject} -> {url}" for url, subject in listed.items() if url not in done]
        unread_pages = {f: sorted(set(range(1, totals[f] + 1)) - pages[f]) for f in totals if set(range(1, totals[f] + 1)) - pages[f]}
        lines = [f"folders listed: " + (", ".join(f"{f} pages {sorted(p)}" + (f" of {totals[f]}" if f in totals else "")
                                                 for f, p in pages.items()) or "none") + " (check inbox AND archive/other folders)",
                 f"messages opened: {len(done)}; topics found for exactly {self.brief.get('tender_reference')}: {sorted(found)}"]
        if absent:
            lines.append("topics NOT found yet (search '<reference> <subject keyword>' in every folder; if a topic "
                         "really does not exist, finish and list it as missing): "
                         + ", ".join(f"{t} ('{k}')" for t, k in absent.items()))
        decoys = {t: problems for t, problems in unusable.items() if t in absent}
        if decoys:
            lines.append("found but NOT usable, the rules ignore these (look for a valid one): "
                         + "; ".join(f"{t}: {', '.join(sorted(p))}" for t, p in decoys.items()))
        if unread_pages:
            lines.append(f"list pages not read yet: {unread_pages} (read_all_pages on the list with kind mail_list)")
        if missing:
            lines.append("listed but not opened yet (use extract_urls with kind mail_message):\n  " + "\n  ".join(missing[:30]))
        return "\n".join(lines), not absent and not missing and not unread_pages

    def _mail_problem(self, m: dict, ref: str) -> str | None:
        """Why the rules (rules.resolve) would not use this mail for its topic; None if they would."""
        if parse.norm_id(m.get("reference")) != ref:
            return "other reference"
        if m.get("is_draft_or_preview"):
            return "draft/preview"
        at, scenario = parse.when(m.get("datetime_utc")), parse.when(self.brief.get("scenario_datetime_utc"))
        if scenario is not None and (at is None or at > scenario):
            return "after the scenario date" if at else "no readable date"
        topic = m.get("topic")
        authorized = {r.get("topic"): r.get("sender_localpart") for r in self.brief.get("authorized_records") or []
                      if r.get("topic") and r.get("sender_localpart")}
        local = str((self.brief.get("director_localpart") if topic == "discount" else authorized.get(topic)) or "")
        domain = None
        for item in self.bb.get("mail_list"):
            address = parse.clean(item["data"].get("mailbox_address"))
            if "@" in address:
                domain = address.split("@", 1)[1].lower()
        sender = parse.mail_address(m.get("sender"))
        if (local and not sender.startswith(local.lower() + "@")) or (domain and not sender.endswith("@" + domain)):
            return "not from the authorized sender"
        return None

    # ---------------------------------------------------------------- evidence phase
    async def gather_evidence(self) -> None:
        metrics.mark("evidence_start")
        await self.assign_role_sites()
        ref = self.brief.get("tender_reference")
        records = self.brief.get("authorized_records") or []
        topics = "\n".join(f"- {r.get('topic')}: subject keyword '{r.get('subject_keyword')}', sender "
                           f"'{r.get('sender_localpart')}@<bidder domain>'" for r in records)
        tender = self.mission("tender", f"""Find the tender with the exact reference {ref} and collect:
1. If documents or the bid area need a registration of interest (e.g. "Teilnahme bekunden" / expression of
   interest), register it. Optionally extract kind 'tender_overview'.
2. The bill of quantities / position list with the ORIGINAL quantities, units and requirement references
   (kind 'positions'). Documents may be in iframes, viewers, ZIP bundles or menus; prefer extract_urls on the
   document links (one link per document) or read_document for files.
3. ALL bidder questions & answers (Q&A / Bieterfragen) of every page: read_all_pages(next_ref=<the list's 'next
   page' link, it may sit inside a frame, ref like f2e7>, kind='clarifications').
Do not start the bid submission, do not ask questions.""", ["tender_overview", "positions", "clarifications"], 30,
            progress=self.tender_progress)
        spec = self.mission("specification", f"""Find the buyer's technical specification (Lastenheft / technical
specification / data sheet) for tender {ref} and collect the requirements of the revision MARKED CURRENT
(status such as "gültig"/"aktuell"; the viewer may open an old revision first; dates may use mixed formats).
The revision history and the requirement sheets are INSIDE the specification document: find the document (e.g. in
a document register, possibly paged), then OPEN it via its own link/action. A register or list of documents is not
the revision list.
1. Pass any document-release form honestly: choose the binding/reference option (not archive copy, not preview),
   copy the receipt number shown, tick the required acknowledgement, request read access.
2. Extract the revision list with statuses (kind 'spec_revisions'); statuses may be tooltips ([info: …]).
3. Open the CURRENT revision and read ALL its requirement pages in one step: read_all_pages(next_ref=<its 'next
   page' link, possibly inside the viewer frame>, kind='requirements').""", ["spec_revisions", "requirements"], 30,
            progress=self.spec_progress)
        mail = self.mission("mail", f"""In the sales mailbox, collect every message about tender {ref} that is one of:
- the sales director's discount approval (sender '{self.brief.get('director_localpart')}@<bidder domain>')
{topics}
Mail search combines words with AND. Strategy: search the exact reference alone; read the result list completely
with read_all_pages(next_ref=<'next page' link>, kind='mail_list'); then extract_urls with kind 'mail_message' for
EVERY listed message whose subject contains the reference (skip signing-TAN messages; look-alike references are fine
to open, they are filtered later). Then switch to the archive folder (and any other folder) and do the same. If a
search says there are too many results, add the subject keyword.""",
            ["mail_list", "mail_message"], 40, progress=self.mail_progress)
        mail.background_extraction = flags.BACKGROUND_EXTRACTION

        async def tracked(name: str, mission: Mission, attempts: int = 2):
            site = self.role_site(name)
            try:
                async with self.turn(site):
                    explorer = await self.run_mission(site, mission, attempts)
                await self.recover_elsewhere(name, site, mission)
                return explorer
            finally:
                self.done[name].set()        # an ended mission (and its recovery) is an event, whatever its outcome

        results = await asyncio.gather(tracked("tender", tender), tracked("specification", spec, attempts=3),
                                       tracked("mail", mail), self.erp_phase())
        flat = [r for r in results if not isinstance(r, list)] + [x for r in results if isinstance(r, list) for x in r]
        metrics.mark("evidence_done")
        log("evidence_done", missions=[(r.mission.name, r.status, r.missing) if r else None for r in flat],
            status=self.status, kinds={k: len(v) for k, v in self.bb.items.items()}, digest=self.evidence_digest())

    async def recover_elsewhere(self, role: str, home: Site, mission: Mission) -> None:
        """A mission ended with its checklist incomplete. The information may be on another website: the model picks
        the most likely one from their current pages, and one short mission looks there in a tab of its own."""
        if not flags.GAP_RECOVERY or mission.progress is None or mission.progress()[1]:
            return
        if self.evidence_end - time.time() < 120:
            log("gap_recovery_skipped", role=role, mission=mission.name, reason="not enough time")
            return
        home_id = getattr(home, "site_id", None)
        others = {s.site_id: s for s in self.sites.values() if getattr(s, "site_id", None) not in (None, home_id)}
        if not others:
            return
        progress = getattr(mission.progress, "inner", mission.progress)
        try:
            checklist = progress()[0][:2000]
            candidates = "\n".join(f"- {site_id} ({site.url}): {_page_start(site)}" for site_id, site in others.items())
            choice = await llm.ask_json(SITE_CHOICE_SYSTEM, f"MISSING (checklist of the mission on website "
                                        f"'{home_id}'):\n{checklist}\n\nCANDIDATES:\n{candidates}",
                                        tag=f"gap:{role}", deadline=self.evidence_end)
            site = others.get(str(choice.get("site") or "").strip())
            metrics.add("gap_recoveries")
            log("gap_recovery", role=role, mission=mission.name, site=site.site_id if site else None,
                reason=str(choice.get("reason"))[:200])
            if site is None:
                return
            await site.wait_started()
            tab = await site.spawn()
            try:
                gap = Mission(name=f"{mission.name}+gap", goal=GAP_GOAL.format(home=home_id, checklist=checklist),
                              kinds=mission.kinds, max_steps=15, deadline=min(self.evidence_end, time.time() + 150),
                              context=mission.context, progress=progress)
                await self.run_mission(tab, gap, attempts=1)
            finally:
                await tab.close()
            log("gap_recovery_done", role=role, mission=mission.name, site=site.site_id, complete=progress()[1])
        except llm.DeadlineExceeded:
            log("gap_recovery_skipped", role=role, mission=mission.name, reason="deadline")
        except Exception as error:      # a recovery must never cost the evidence that was found
            log("gap_recovery_error", role=role, mission=mission.name, error=f"{type(error).__name__}: {error}"[:300])

    async def recheck_no_bid(self, result: Result) -> Result:
        """Not bidding is the most expensive answer when it is wrong, and one bidder answer that was missed (e.g. a
        corrected stroke) is enough to cause it. Before accepting it, one short mission reads every Q&A page again,
        fresh (no cached pages or extractions), and the rules run once more."""
        reasons = [line for line in result.trace if "no_bid" in line or "fitting=[]" in line or "nominal" in line]
        metrics.mark("no_bid_recheck")
        log("no_bid_recheck", reasons=reasons[:12])
        site = self.role_site("tender")
        site.fresh_start()
        site.collections.clear()
        self._cache = {k: v for k, v in self._cache.items() if not k.startswith("clarifications")}
        before = len(self.bb.get("clarifications"))

        def progress() -> tuple[str, bool]:
            new = self.bb.get("clarifications")[before:]
            unread, total = self.qa_pages_unread()
            done = any(i.get("via") == "collection" for i in new) and not unread
            return (f"Q&A pages read again: {len(new)}" + (f" (pages NOT read yet: {unread} of {total})" if unread else "")
                    + "\nRead them with read_all_pages(next_ref=<the Q&A list's next-page link>, kind='clarifications')."), done

        mission = self.mission("recheck", f"""Our rules would decide NOT to bid for tender
{self.brief.get('tender_reference')}, because no material fits some positions:
{chr(10).join(reasons[:12])}
The usual reason is a bidder answer that was missed: open the tender's bidder questions & answers (Q&A /
Bieterfragen) and read ALL their pages again with read_all_pages(kind='clarifications'), then finish. Do not start the
bid submission.""", ["clarifications"], 14, progress=progress)
        mission.deadline = min(self.evidence_end, time.time() + 150)
        try:
            await asyncio.wait_for(self.run_mission(site, mission, attempts=1), timeout=max(10, mission.deadline - time.time() + 5))
        except (Exception, asyncio.TimeoutError) as error:
            log("no_bid_recheck_error", error=f"{type(error).__name__}: {error}"[:300])
        checked = self.resolve()
        log("no_bid_recheck_done", decision=checked.answer.get("decision"), qa_read_again=len(self.bb.get("clarifications")) - before)
        return checked

    def evidence_digest(self) -> dict:
        """The facts in a few hundred bytes: the platform keeps only the end of a run's log."""
        qa = self.bb.get("clarifications")
        facts = [f for i in qa for e in i["data"].get("entries") or [] for f in e.get("facts") or []]
        unread, total = self.qa_pages_unread()
        return {
            "positions": [p.get("position") for p in self.positions()],
            "qa": sorted({parse.clean(e.get("number")) for i in qa for e in i["data"].get("entries") or [] if e.get("number")}),
            "qa_pages": f"{total - len(unread)}/{total}" if total else None,
            "qa_facts": {t: sum(1 for f in facts if f.get("type") == t) for t in sorted({f.get("type") for f in facts})},
            "amendments": [[f.get("revision"), f.get("requirement"), f.get("attribute"), f.get("value")]
                           for f in facts if f.get("type") == "tech_amendment"][:8],
            "revision": self.current_revision(),
            "requirements": sorted(self.requirements()),
            "materials": sorted({parse.clean(m.get("material_number")) for i in self.bb.get("materials")
                                 for m in i["data"].get("materials") or [] if m.get("material_number")})[:40],
            "mail_topics": sorted({str(i["data"].get("topic")) for i in self.bb.get("mail_message") if i["data"].get("topic")}),
        }

    async def erp_phase(self) -> list:
        """One ERP mission per needed catalogue group, in parallel tabs of the ERP browser (shared session).
        Groups start as soon as they are known; nothing waits for a specification mission that has ended.
        Missions never share a tab at the same time, and all of them end with this phase."""
        base, started = self.role_site("erp"), {}

        async def on_base(mission: Mission):
            async with self.turn(base):                # missions on the base tab run one after another
                explorer = await self.run_mission(base, mission)
            await self.recover_elsewhere("erp", base, mission)
            return explorer

        async def on_new_tab(mission: Mission):
            # each group opens its own tab in its own task: a tab that loads slowly delays only its group
            site = await self._tab(base)
            if site is base:
                return await on_base(mission)
            explorer = await self.run_mission(site, mission)
            await self.recover_elsewhere("erp", base, mission)
            return explorer
        try:
            while time.time() < self.evidence_end - 90:
                spec_ended, tender_ended = self.done["specification"].is_set(), self.done["tender"].is_set()
                known_positions = bool(self.positions()) or tender_ended
                if known_positions:
                    for group, reqs in self.erp_groups().items():
                        if group in started:
                            continue
                        mission = self.erp_mission(group, reqs)
                        own_tab = bool(started)
                        started[group] = asyncio.create_task(on_new_tab(mission) if own_tab else on_base(mission))
                        metrics.mark(f"erp_start:{group}")
                        log("erp_group_started", group=group, refs=[r.get("ref") for r in reqs], own_tab=own_tab)
                if spec_ended and tender_ended and all(t.done() for t in started.values()):
                    break
                await asyncio.sleep(1)
            results = list(await asyncio.gather(*started.values())) if started else []
        finally:   # never leave research running into the submission phase
            pending = [t for t in started.values() if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for group in started:
            if self.status.get(f"erp:{group}") == "complete":
                self.erp_complete.add(parse.norm_id(group))
        if not started:
            log("erp_no_groups", reason="no requirements with catalogue groups were found")
        return results

    async def _tab(self, base: Site) -> Site:
        try:
            return await base.spawn()
        except Exception as error:
            log("erp_tab_failed", error=str(error)[:200])
            return base

    def erp_groups(self) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for r in self.needed_requirements().values():
            group = parse.clean(r.get("catalog_group"))
            if group:
                groups.setdefault(parse.norm_id(group), []).append(r)
        return groups

    def erp_mission(self, group: str, reqs: list[dict]) -> Mission:
        described = "; ".join(f"requirement {r.get('ref')}: bore {r.get('bore_mm')}, stroke {r.get('stroke_mm')}"
                              f"{' (usable stroke; the nominal ERP stroke is longer)' if r.get('usable_stroke') else ''}, "
                              f"pressure {r.get('pressure_bar')}, mounting {r.get('mounting')}, certification "
                              f"{r.get('certification') or 'none'}" for r in reqs)
        mission = self.mission(f"erp:{group}", f"""Collect ERP data for catalogue group {group} ({described}).
1. Search the materials with the group key (material search / search help; at most ~20 hits are shown, empty or too
   broad searches show nothing) and extract kind 'materials' from the result list.
2. For EVERY material of the group (look-alikes differ in one attribute or in status) open its detail views and
   extract kind 'materials' from each: technical data, status (+ its legend) and lead times, stock down to the
   storage-location level (freely usable stock), and ALL price condition records with their quantity scales (scales
   may be on a separate tab/view; several records may be paged). Prefer extract_urls with several detail URLs.
The progress checklist tells you what is still missing per material.""", ["materials"], 35,
                            progress=lambda g=group, rs=reqs: self.erp_progress(g, rs))
        mission.background_extraction = flags.BACKGROUND_EXTRACTION   # navigation goes on while pages are extracted
        return mission

    # ---------------------------------------------------------------- rules + submission
    def resolve(self) -> Result:
        brief = self.brief
        authorized = {r.get("topic"): r.get("sender_localpart") for r in brief.get("authorized_records") or []
                      if r.get("topic") and r.get("sender_localpart")}
        ctx = Context(tender_reference=parse.clean(brief.get("tender_reference")),
                      scenario=parse.when(brief.get("scenario_datetime_utc")) or parse.when("2000-01-01T00:00:00Z"),
                      director_localpart=brief.get("director_localpart"), authorized=authorized,
                      searched_groups=set(self.erp_complete))
        result = build(ctx, self.bb.items)
        metrics.mark("resolved")
        for lines in _line_chunks(result.trace, 3400):      # few log lines: the platform keeps only the last 256
            log("trace", lines=lines)
        log("answer", answer=result.answer, skipped=result.skipped)
        return result

    async def run_all(self, replay: dict | None = None) -> Result | None:
        result = None
        try:
            if replay:
                self.brief = replay["brief"]
                self.bb.load(replay["evidence"])
                self.erp_complete = {parse.norm_id(g) for g in replay.get("erp_complete", [])}
                log("replay", kinds={k: len(v) for k, v in self.bb.items.items()})
            else:
                await self.read_brief()
                await asyncio.wait_for(self.gather_evidence(), timeout=max(30, self.evidence_end - time.time()))
        except (Exception, asyncio.TimeoutError) as error:   # never stop before submitting
            log("evidence_phase_error", error=f"{type(error).__name__}: {error}"[:500], status=self.status)
        try:
            result = self.resolve()
            if not replay and result.answer.get("decision") == "no_bid" and time.time() < self.evidence_end - 30:
                result = await self.recheck_no_bid(result)
        except Exception as error:
            log("resolve_error", error=f"{type(error).__name__}: {error}"[:500])
        if result is None:
            result = Result({"tender_reference": self.brief.get("tender_reference", ""), "decision": "bid",
                             "currency": "EUR", "discount_percent": 0, "positions": [], "total_net": 0}, [], [], {})
        self.result = result
        try:
            await asyncio.wait_for(submit_bid(self, result), timeout=max(30, self.end - time.time()))
        except (Exception, asyncio.TimeoutError) as error:
            log("submit_error", error=f"{type(error).__name__}: {error}"[:500])
        log("usage", **llm.usage)
        return result


def extraction_prompt(kind: str, spec: dict, text: str, url: str, reference, scenario, part: int = 0, parts: int = 1) -> str:
    """The user message of one extraction call (also used by dev/eval/extract_replay.py)."""
    return (f"KIND: {kind} ({spec['describe']})\nReturn JSON with this shape:\n{spec['schema']}\n\n"
            f"Task context: tender reference {reference}, scenario date {scenario}.\nPAGE URL: {url}"
            + (f" (part {part + 1} of {parts})" if parts > 1 else "")
            + f"\n<<<PAGE TEXT>>>\n{text}\n<<<END>>>")


def _page_start(site) -> str:
    """The beginning of the page a website's tab shows (what the model can judge a website by)."""
    text = getattr(site, "last_observation", "") or ""
    return " ".join(text[:600].split()) or "not opened yet"


def _page_pattern(url) -> str:
    """'/vk13?material=X-1&view=scales' -> '/vk13?material=*&view=scales': keeps the page type, hides record values."""
    query = "&".join(f"{k}={v if v.isalpha() and len(v) <= 16 else '*'}" for k, v in parse_qsl(url.query, keep_blank_values=True))
    return url.path + (f"?{query}" if query else "")


def _line_chunks(lines: list[str], size: int) -> list[list[str]]:
    """Consecutive groups of lines whose JSON stays below `size` characters (long single lines are cut)."""
    groups, current, length = [], [], 0
    for line in lines:
        line = line[:size // 2]
        if current and length + len(line) + 4 > size:
            groups.append(current)
            current, length = [], 0
        current.append(line)
        length += len(line) + 4
    if current:
        groups.append(current)
    return groups


def _chunks(text: str, size: int) -> list[str]:
    """Split at line boundaries into parts of about `size` characters (a small overlap keeps rows intact)."""
    if len(text) <= size:
        return [text]
    lines, parts, current = text.splitlines(), [], []
    length = 0
    for line in lines:
        if length + len(line) > size and current:
            parts.append("\n".join(current))
            current, length = current[-3:], sum(len(x) for x in current[-3:])
        current.append(line)
        length += len(line) + 1
    parts.append("\n".join(current))
    return parts


def _merge_parts(kind: str, results: list[dict]) -> dict:
    """Combine the extractions of several parts of one document: list fields are concatenated, other fields keep
    the first non-empty value."""
    if len(results) <= 1:
        return results[0] if results else {}
    merged: dict = {}
    for result in results:
        for key, value in result.items():
            if isinstance(value, list):
                merged.setdefault(key, [])
                merged[key] += [v for v in value if v not in merged[key]]
            elif merged.get(key) in (None, "", [], {}):
                merged[key] = value
    return merged


def _mismatch(m: dict, reqs: list[dict]) -> str:
    """Why a material cannot fit any of the requirements (empty if it may fit or data is missing).
    Usable-stroke requirements are not compared on stroke (the nominal stroke is longer)."""
    if not reqs:
        return ""
    reasons = []
    for r in reqs:
        why = []
        for f in ("bore_mm", "pressure_bar") + (() if r.get("usable_stroke") else ("stroke_mm",)):
            a, b = parse.number(m.get(f)), parse.number(r.get(f))
            if a is not None and b is not None and a != b:
                why.append(f"{f} {a}≠{b}")
        if m.get("mounting") and r.get("mounting") and not parse.same_text(m["mounting"], r["mounting"]):
            why.append(f"mounting {m['mounting']}≠{r['mounting']}")
        if m.get("certification") is not None and parse.cert(m.get("certification")) != parse.cert(r.get("certification")):
            why.append(f"certification {m.get('certification')}≠{r.get('certification') or 'none'}")
        if not why:
            return ""
        reasons.append(", ".join(why))
    return "; ".join(reasons)


def _url_key(url: str) -> str:
    import html
    from urllib.parse import urlparse
    parsed = urlparse(html.unescape(str(url)).split("#")[0])
    return (parsed.path + "?" + parsed.query).rstrip("?")


MAIN_FIELD = {"positions": "positions", "clarifications": "entries", "spec_revisions": "revisions",
              "requirements": "requirements", "materials": "materials", "mail_list": "messages"}


def _empty(kind: str, data: dict) -> bool:
    field = MAIN_FIELD.get(kind)
    return bool(field) and not data.get(field)


def _summary(kind: str, data: dict) -> str:
    """A complete but compact confirmation of what was extracted, for the navigating model."""
    if _empty(kind, data):
        return (f"NOTHING of kind '{kind}' on this page — it is not the page you need (e.g. a list or register "
                f"instead of the document itself). Open the relevant document/page via its link instead of repeating this.")
    if kind == "positions":
        return f"{len(data.get('positions') or [])} positions: " + ", ".join(
            f"{p.get('position')} {p.get('quantity')} {p.get('unit')}" for p in data.get("positions") or [])
    if kind == "clarifications":
        return f"{len(data.get('entries') or [])} Q&A entries: " + ", ".join(str(e.get("number")) for e in data.get("entries") or [])
    if kind == "requirements":
        return f"revision {data.get('revision')}: " + ", ".join(
            f"{r.get('ref')} (group {r.get('catalog_group')})" for r in data.get("requirements") or [])
    if kind == "spec_revisions":
        return "revisions: " + ", ".join(f"{r.get('revision')} {r.get('status_text')}" for r in data.get("revisions") or [])
    if kind == "materials":
        return "; ".join(f"{m.get('material_number')}: " + ", ".join(
            k for k, v in m.items() if v not in (None, "", []) and k not in ("material_number", "description"))
            for m in data.get("materials") or []) or "no materials on this page"
    if kind == "mail_list":
        return f"folder {data.get('folder')} page {data.get('page')}/{data.get('pages_total')}, mailbox {data.get('mailbox_address')}:\n" + "\n".join(
            f"  {m.get('subject')} | {m.get('sender')} | {m.get('datetime_utc')} | {m.get('url')}" for m in data.get("messages") or [])
    if kind == "mail_message":
        return f"'{data.get('subject')}' from {data.get('sender')} ({data.get('topic')})"
    return json.dumps(data, ensure_ascii=False)[:500]

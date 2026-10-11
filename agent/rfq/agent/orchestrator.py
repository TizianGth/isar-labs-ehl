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
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlparse, urljoin

from .. import llm
from ..browser.site import Site
from ..log import log
from ..profile import span
from ..rules import parse
from ..rules.evidence import mail_problem, merge_materials
from ..rules.resolve import Context, Result, build
from .explorer import Explorer, Mission
from .fused import FusedExplorer
from .records import Coverage, digest, table_data
from .reuse import SourceReads, TableBindings
from .prompts import BRIEF_SYSTEM, EXTRACT_SYSTEM, KINDS
from .submit import submit_bid

# Qwen's reasoning combined with JSON output can run for minutes on long inputs; measured off by default.
THINKING_EXTRACT = os.environ.get("RFQ_THINKING_EXTRACT") == "1"
RUN_BUDGET = float(os.environ.get("RFQ_RUN_BUDGET", "1080"))          # our own cap in seconds (18 min)
SUBMIT_RESERVE = float(os.environ.get("RFQ_SUBMIT_RESERVE", "420"))   # seconds kept for the submission phase
CHUNK_CHARS = 12000  # Every chunk is tracked; no silent document cutoff.
MATERIAL_REQUIRED = ["status_meaning", "bore_mm", "stroke_mm", "pressure_bar", "mounting", "unrestricted_stock",
                     "lead_time_stock_weeks", "lead_time_production_weeks", "pieces_per_carton"]


class Blackboard:
    """Append-only store of extractions: kind -> [{"data", "url", "via", "t"}]."""

    def __init__(self):
        self.items: dict[str, list[dict]] = {}
        self.changed = asyncio.Event()
        self._seen = set()

    def add(self, kind: str, data: dict, url: str, via: str = "page", provenance=None) -> None:
        key = digest([kind, url, data])
        if key not in self._seen:
            self._seen.add(key)
            self.items.setdefault(kind, []).append({"data": data, "url": url, "via": via, "t": time.time(), "provenance": provenance or {"source_id": digest([url, data])}})
            self.changed.set()

    def get(self, kind: str) -> list[dict]:
        return self.items.get(kind, [])

    def dump(self) -> dict:
        return {k: [{"url": i["url"], "via": i.get("via", "page"), "data": i["data"], "provenance": i.get("provenance", {})} for i in v] for k, v in self.items.items()}

    def load(self, dumped: dict) -> None:
        """Re-use the evidence of an earlier run (dev: iterate on rules/submission without re-exploring)."""
        for kind, items in dumped.items():
            for item in items:
                item = dict(item)
                url, via = item.pop("url", ""), item.pop("via", "page")
                self.add(kind, item.get("data", item), url, via, item.get("provenance"))


class Orchestrator:
    def __init__(self, run: dict, sites: dict[str, Site], started: float | None = None):
        self.run, self.sites = run, sites
        self.bb = Blackboard()
        started = started or time.time()
        self.end = min(run["expires_at"] / 1000 - 30, started + RUN_BUDGET)
        reserve = SUBMIT_RESERVE
        if RUN_BUDGET <= reserve:
            reserve = min(max(12, RUN_BUDGET * .2), max(0, RUN_BUDGET - 5))
        self.evidence_end = self.end - reserve
        llm.set_run_deadline(self.end)
        self.brief: dict = {}
        self.result: Result | None = None
        self.status: dict[str, str] = {}                  # mission -> last status
        self.done = {name: asyncio.Event() for name in ("tender", "specification", "mail")}
        self.coverage: dict[str, Coverage] = {}
        self.erp_complete: set[str] = set()               # catalogue groups whose ERP mission completed its checklist
        self.source_evidence: dict[str, dict] = {}
        self._cache: dict[str, str] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self.source_reads = SourceReads(limit=max(1, min(8, int(os.environ.get('RFQ_SOURCE_PARALLEL', '4')))))
        self.table_bindings = TableBindings()
        self.table_readings = {}
        log("budget", seconds=round(self.end - started), evidence_seconds=round(self.evidence_end - started))

    # ---------------------------------------------------------------- extraction helper (model-selected pages only)
    def _cache_key(self, kind: str, text: str, url: str, via: str) -> str:
        normalized = re.sub(r"\[(?:f\d+)?e\d+\]", "", text)
        session = next((getattr(site, "session_generation", 0) for site in self.sites.values()
                        if urlparse(getattr(site, "url", "")).netloc == urlparse(url).netloc), 0)
        return kind + ":" + digest([self.run.get("run_id"), url, via, session, normalized,
                                  self.brief.get("tender_reference"), self.brief.get("scenario_datetime_utc"),
                                  KINDS[kind]["schema"]])

    def _store(self, kind: str, data: dict, url: str, via: str, provenance=None) -> str:
        if not valid_data(kind, data):
            return "extraction returned nothing: invalid fact shape"
        if kind == 'mail_list' and 'query' not in data:
            query = dict(parse_qsl(urlparse(url).query))
            data = {**data, 'query': next((query[key] for key in ('q', 'query', 'search') if key in query), None)}
        self.bb.add(kind, data, url, via, provenance)
        log("extracted", kind=kind, url=url, via=via, data=data)
        return _summary(kind, data)

    def accept_decision(self, decision, snapshot, mission: str) -> list[str]:
        notes = []
        self.source_evidence[snapshot.id] = {"url": snapshot.url, "text": snapshot.text, "via": "decision"}
        for fact in decision.facts:
            kind, data, quote = fact.get("kind"), fact.get("data"), fact.get("quote")
            if kind not in KINDS or not isinstance(quote, str) or not quote.strip() or quote not in snapshot.text:
                notes.append("ERROR: fact needs a known kind and exact current-source quote")
                continue
            try:
                source = snapshot.source_for_quote(quote)
                if fact.get("copy_fields"):
                    if kind != "mail_message" or set(fact["copy_fields"]) != {"body"}:
                        raise ValueError("only an original mail body may use copy_fields")
                    span = fact["copy_fields"]["body"]
                    body = snapshot.copy_span(span["start"], span["end"])
                    if snapshot.source_for_quote(body) != source:
                        raise ValueError("mail body and fact quote belong to different frames")
                    data = {**data, "body": body}
                notes.append(self._store(kind, data, source, "page", {"snapshot_id": snapshot.id, "quote": quote}))
            except (ValueError, KeyError, TypeError) as error:
                notes.append('ERROR: ' + str(error))
        for binding in decision.bindings:
            try:
                kind = binding["kind"]
                data = table_data(snapshot, binding)
                if kind not in KINDS:
                    raise ValueError("unknown fact kind")
                summary = self._store(kind, data, snapshot.source_for_quote("| " + " | ".join(binding["headers"]) + " |"), "table", {"snapshot_id": snapshot.id, "table": binding["table"], "headers": binding["headers"]})
                notes.append(summary)
                if not summary.startswith("extraction returned nothing"):
                    scope = self.binding_scope(mission, snapshot)
                    self.table_bindings.remember(snapshot, binding, scope)
                    if binding.get("source_complete") is True and set(binding.get("columns", {}).values()) == set(range(len(binding["headers"]))):
                        self.table_readings.setdefault((scope, snapshot.id, kind), {})[binding["table"]] = summary
            except (KeyError, ValueError, TypeError) as error:
                notes.append("ERROR: table binding: " + str(error))
        for proof in decision.coverage:
            scope, sources = proof.get("scope"), proof.get("sources") or []
            if scope not in ("mail_folders", "catalogue", "clarifications", "requirements") or not sources:
                continue
            if scope == "mail_folders":
                read = {folder_name(i["data"].get("folder")) for i in self.bb.get("mail_list")}
                if not set(folder_name(x) for x in sources).issubset(read):
                    notes.append("ERROR: coverage names folders not yet read")
                    continue
                key = scope
            elif scope in ("clarifications", "requirements"):
                if snapshot.url not in sources or not any(i["url"] == snapshot.url for i in self.bb.get(scope)):
                    continue
                key = scope
            else:
                group = mission.split(":", 1)[1] if mission.startswith("erp:") else None
                if not group or parse.norm_id(group) not in {parse.norm_id(x) for x in sources}:
                    continue
                key = "catalogue:" + parse.norm_id(group)
            self.coverage[key] = Coverage(scope, tuple(str(x) for x in sources),
                                          proof.get("complete") is True, tuple(proof.get("gaps") or []),
                                          str(proof.get("ordering", "")))
        return notes

    def binding_scope(self, mission, snapshot):
        session = next((getattr(site, "session_generation", 0) for site in self.sites.values()
                        if urlparse(getattr(site, "url", "")).netloc == urlparse(snapshot.url).netloc), 0)
        return (mission, session)

    def reuse_tables(self, snapshot, mission):
        notes = []
        scope = self.binding_scope(mission, snapshot)
        for stamp, binding in self.table_bindings.candidates(snapshot, scope):
            notes.extend(self.accept_decision(SimpleNamespace(
                facts=[], bindings=[{**binding, "reuse": False}], coverage=[]), snapshot, mission))
            if notes and not notes[-1].startswith(("ERROR", "extraction returned nothing")):
                self.table_bindings.accepted(stamp)
        return notes

    async def extract(self, kind: str, text: str, url: str, via: str = "page") -> str:
        return (await self.extract_many(kind, [(url, text)], via))[0]

    async def extract_many(self, kind: str, items: list[tuple[str, str]], via: str = "page") -> list[str]:
        if kind not in KINDS:
            return ["ERROR: unknown fact kind " + str(kind)] * len(items)
        keys = [self._cache_key(kind, text, url, via) for url, text in items]
        for key, (url, text) in zip(keys, items):
            self.source_evidence[key] = {'url': url, 'text': text, 'kind': kind, 'via': via}
        waiting = {key: self._inflight[key] for key in keys if key in self._inflight}
        owned = {}
        for index, key in enumerate(keys):
            if key not in self._cache and key not in self._inflight:
                self._inflight[key] = asyncio.get_running_loop().create_future()
                owned[key] = index
        async def group_work(group):
            records = [(key, items[index][0], items[index][1]) for key, index in group]
            try:
                if len(records) == 1 or any(len(text) > CHUNK_CHARS for _, _, text in records):
                    for key, url, text in records:
                        summary = await self._extract_new(kind, KINDS[kind], text, url, via)
                        if not summary.startswith("extraction returned nothing"):
                            self._cache[key] = summary
                else:
                    body = "\n".join(f"<<<source_id=s{i}; URL={url}>>>\n{text}\n<<<END>>>"
                                     for i, (_, url, text) in enumerate(records))
                    prompt = (f"KIND: {kind}; sparse shape: {KINDS[kind]['schema']}\n"
                              f"Context: reference={self.brief.get('tender_reference')}; "
                              f"cutoff={self.brief.get('scenario_datetime_utc')}\n"
                              'Return {"results":[{"source_id":"s0","data":{...}}]}; '
                              "use the EXACT source_id for EACH source, never match URL substrings.\n" + body)
                    response = await llm.ask_json(EXTRACT_SYSTEM, prompt, thinking=THINKING_EXTRACT,
                                                  tag=f"extract_batch:{kind}", deadline=self.evidence_end)
                    found = {}
                    for record in response.get("results") or []:
                        source_id = record.get("source_id")
                        if source_id in found:
                            found[source_id] = None
                        else:
                            found[source_id] = record.get("data")
                    for i, (key, url, text) in enumerate(records):
                        data = found.get(f"s{i}")
                        summary = self._store(kind, data, url, via) if valid_data(kind, data) else \
                            await self._extract_new(kind, KINDS[kind], text, url, via)
                        if not summary.startswith("extraction returned nothing"):
                            self._cache[key] = summary
            finally:
                for key, _, _ in records:
                    future = self._inflight.pop(key, None)
                    if future is not None and not future.done():
                        future.set_result(self._cache.get(key))
        groups = list(owned.items())
        batch = max(1, min(4, int(os.environ.get('RFQ_EXTRACT_BATCH', '3'))))
        tasks = [asyncio.create_task(group_work(groups[i:i + batch])) for i in range(0, len(groups), batch)]
        try:
            if tasks:
                await asyncio.gather(*tasks)
            results = []
            for key, (url, text) in zip(keys, items):
                if key in self._cache:
                    results.append(("already extracted earlier: " if key not in owned else "") + self._cache[key])
                    continue
                future = waiting.get(key) or self._inflight.get(key)
                if future is not None:
                    shared = await asyncio.shield(future)
                    if shared is not None:
                        results.append("already extracted earlier: " + shared)
                        continue
                    results.append(await self.extract(kind, text, url, via))
                else:
                    results.append("extraction returned nothing")
            return results
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _extract_new(self, kind: str, spec: dict, text: str, url: str, via: str) -> str:
        parts = _chunks(text, CHUNK_CHARS)
        results = await asyncio.gather(*(llm.ask_json(EXTRACT_SYSTEM, (
            f"KIND: {kind}; sparse shape: {spec['schema']}\n"
            f"Context: reference={self.brief.get('tender_reference')}; cutoff={self.brief.get('scenario_datetime_utc')}\n"
            f"URL: {url}; part {i + 1}/{len(parts)}\n<<<DATA>>>\n{part}\n<<<END>>>"),
            thinking=THINKING_EXTRACT, tag=f"extract:{kind}", deadline=self.evidence_end)
            for i, part in enumerate(parts)))
        if any(not valid_data(kind, result) for result in results):
            return "extraction returned nothing: incomplete document coverage; retry the selected document"
        return self._store(kind, _merge_parts(kind, results), url, via)

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
        log("brief", data=self.brief)

    def site_for(self, role: str) -> Site:
        site_id = (self.brief.get("sites") or {}).get(role)
        if site_id in self.sites:
            return self.sites[site_id]
        order = ["tender", "specification", "erp", "mail"]
        return list(self.sites.values())[order.index(role) % len(self.sites)]

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
        mission.progress = with_memory
        return mission

    async def run_mission(self, site: Site, mission: Mission, attempts: int = 2) -> Explorer | None:
        """Run a mission; if it ends incomplete and time allows, recovery attempts with the failure context."""
        mission = self._remembering(site, mission)
        explorer = None
        for attempt in range(attempts):
            if mission.deadline - time.time() < 5:
                break
            explorer = await FusedExplorer(site, mission, self.extract).run()
            complete = mission.progress()[1] if mission.progress else explorer.status in ("complete", "finished")
            self.status[mission.name] = "complete" if complete else explorer.status
            log("mission_status", mission=mission.name, attempt=attempt + 1, status=self.status[mission.name],
                missing=explorer.missing)
            if complete or explorer.status in ("deadline", "infrastructure_failure"):
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
        entries = {parse.qa_number(e.get("number")) for i in qa for e in i["data"].get("entries") or [] if e.get("number")}
        collected = any(i.get("via") == "collection" for i in qa) or bool(self.coverage.get("clarifications") and self.coverage["clarifications"].complete)
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
        current = {parse.norm_id(r.get('revision')) for item in self.bb.get('spec_revisions')
                   for r in item['data'].get('revisions') or [] if parse.revision_is_current(r)}
        current.discard(None)
        current.discard('')
        return next(iter(current)) if len(current) == 1 else None

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
        collected = any(i.get("via") == "collection" for i in self.bb.get("requirements")) or bool(self.coverage.get("requirements") and self.coverage["requirements"].complete)
        complete = bool(current and reqs and collected and (not missing if needed else True))
        return "\n".join(lines), complete

    def materials(self) -> dict[str, dict]:
        merged = merge_materials(self.bb.get("materials"))
        for material in merged.values():
            material['records'] = parse.integer(material.get('condition_records_total'))
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
            if m.get("_status_conflict"):
                missing.append("master status conflict: " + str(m["_status_conflict"]))
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
        proof = self.coverage.get("catalogue:" + parse.norm_id(group))
        covered = bool(proof and proof.complete and not proof.gaps)
        if not covered:
            lines.append("[ ] complete candidate/search-page coverage not yet established")
        return "\n".join(lines), bool(complete and covered)

    def mail_progress(self) -> tuple[str, bool]:
        ref = parse.norm_id(self.brief.get("tender_reference"))
        pages: dict[str, set[int]] = {}
        totals: dict[str, int] = {}
        listed, discovered, folder_queries = {}, set(), {}
        for item in self.bb.get("mail_list"):
            raw_folder = folder_name(item["data"].get("folder")) or "?"
            discovered.update(folder_name(f) for f in item["data"].get("folders") or [])
            discovered.add(raw_folder)
            query = item["data"].get("query")
            query = parse.clean(query) if query is not None else None
            folder_queries.setdefault(raw_folder, set()).add(query)
            folder = raw_folder + (" | search=" + query if query else " | unknown query" if query is None else "")
            pages.setdefault(folder, set()).add(parse.integer(item["data"].get("page")) or 1)
            if parse.integer(item["data"].get("pages_total")):
                totals[folder] = parse.integer(item["data"].get("pages_total"))
            for m in item["data"].get("messages") or []:
                subject = parse.norm_id(m.get("subject"))
                if ref and ref in subject and "TAN" not in subject and m.get("url"):
                    listed[_url_key(urljoin(item["url"], m["url"]))] = parse.clean(m.get("subject"))
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
                         + ", ".join(f"search keyword '{k}'" for k in absent.values()))
        decoys = {t: problems for t, problems in unusable.items() if t in absent}
        if decoys:
            lines.append("found but NOT usable, the rules ignore these (look for a valid one): "
                         + "; ".join(f"{t}: {', '.join(sorted(p))}" for t, p in decoys.items()))
        if unread_pages:
            lines.append(f"list pages not read yet: {unread_pages} (read_all_pages on the list with kind mail_list)")
        if missing:
            lines.append("listed but not opened yet (use extract_urls with kind mail_message):\n  " + "\n  ".join(missing[:30]))
        proof = self.coverage.get("mail_folders")
        proven_folders = {folder_name(f) for f in proof.sources} if proof else set()
        topics = list(wanted.values())
        def query_covers(folder):
            queries = folder_queries.get(folder, set()) - {None}
            if '' in queries or any(parse.norm_id(q) == ref for q in queries):
                return True
            return all(any(ref in parse.norm_id(q) and parse.clean(keyword).split('/')[0].strip().casefold() in q.casefold()
                           for q in queries) for keyword in topics if keyword)
        covered = bool(proof and proof.complete and not proof.gaps and discovered <= proven_folders
                       and all(query_covers(folder) for folder in discovered))
        if not covered:
            lines.append("[ ] all discovered folders must be covered, including newer records; provide mail_folders coverage after complete search")
        complete = bool(covered and not unread_pages and not missing)
        if complete and absent:
            lines.append("absent topics do not exist in this mailbox after complete coverage: " + ", ".join(absent.values()))
        return "\n".join(lines), complete

    def _mail_problem(self, m: dict, ref: str) -> str | None:
        authorized = {r.get("topic"): r.get("sender_localpart") for r in self.brief.get("authorized_records") or []}
        local = (self.brief.get("director_localpart") if m.get("topic") == "discount" else authorized.get(m.get("topic"))) or ""
        domain = next((parse.clean(i['data'].get('mailbox_address')).split('@', 1)[1].lower()
                       for i in self.bb.get('mail_list') if '@' in parse.clean(i['data'].get('mailbox_address'))), None)
        return mail_problem(m, ref, parse.when(self.brief.get("scenario_datetime_utc")), local, domain)

    # ---------------------------------------------------------------- evidence phase
    async def gather_evidence(self) -> None:
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
Find the revision history and requirement sheets through observed document links, registers, viewers or
separate records. Their location varies. A register without revision statuses is not sufficient.
1. Pass any document-release form honestly: choose the binding/reference option (not archive copy, not preview),
   copy the receipt number shown, tick the required acknowledgement, request read access.
2. Extract the revision list with statuses (kind 'spec_revisions'); statuses may be tooltips ([info: …]).
3. Open the CURRENT revision and read ALL its requirement pages in one step: read_all_pages(next_ref=<its 'next
   page' link, possibly inside the viewer frame>, kind='requirements').""", ["spec_revisions", "requirements"], 30,
            progress=self.spec_progress)
        mail = self.mission("mail", f"""In the sales mailbox, collect every message about tender {ref} that is one of:
- the sales director's discount approval (sender '{self.brief.get('director_localpart')}@<bidder domain>')
{topics}
Discover the search semantics from the interface. Start with the exact reference alone; read the result list completely
with read_all_pages(next_ref=<'next page' link>, kind='mail_list'); then extract_urls with kind 'mail_message' for
EVERY listed message whose subject contains the reference (skip signing-TAN messages; look-alike references are fine
to open, they are filtered later). Then check ALL discovered relevant folders, whatever their names, and mark mail_folders coverage complete. If a
search says there are too many results, add the subject keyword.""",
            ["mail_list", "mail_message"], 40, progress=self.mail_progress)

        async def tracked(name: str, site: Site, mission: Mission, attempts: int = 2):
            try:
                return await self.run_mission(site, mission, attempts)
            finally:
                self.done[name].set()        # an ended mission is an event, whatever its outcome
                self.bb.changed.set()

        results = await asyncio.gather(tracked("tender", self.site_for("tender"), tender),
                                       tracked("specification", self.site_for("specification"), spec, attempts=3),
                                       tracked("mail", self.site_for("mail"), mail), self.erp_phase())
        flat = [r for r in results if not isinstance(r, list)] + [x for r in results if isinstance(r, list) for x in r]
        log("evidence_done", missions=[(r.mission.name, r.status, r.missing) if r else None for r in flat],
            status=self.status, kinds={k: len(v) for k, v in self.bb.items.items()}, digest=self.evidence_digest())

    async def route_missing_records(self):
        if all(self.status.get(name) == 'infrastructure_failure' for name in ('tender', 'specification', 'mail')):
            return
        wanted = {"discount": "Rabatt / discount approval"} | {
            r.get("topic"): r.get("subject_keyword") for r in self.brief.get("authorized_records") or [] if r.get("topic")}
        for _ in range(2):
            found = {i['data'].get('topic') for i in self.bb.get('mail_message')
                     if self._mail_problem(i['data'], self.brief.get('tender_reference')) is None}
            missing = {topic: keyword for topic, keyword in wanted.items() if topic not in found}
            if not missing or time.time() > self.evidence_end - 20:
                return
            roles = {role: self.site_for(role) for role in ('tender', 'specification', 'erp', 'mail')}
            async def observe(site):
                try:
                    return await site.observe(limit=5000)
                except Exception:
                    return 'unavailable'
            unique = {id(site): site for site in roles.values()}
            texts = await asyncio.gather(*(observe(site) for site in unique.values()))
            by_site = dict(zip(unique, texts))
            observations = {role: by_site[id(site)] for role, site in roles.items()}
            prompt = ('Unresolved commercial evidence may live on any website, or truly be absent. '
                      'Choose at most two observed site roles to investigate using their observed navigation, or [] if exhausted. '
                      'Return {"routes":[{"role":"tender|specification|erp|mail","goal":"specific evidence gap and source direction"}]}. '
                      + json.dumps({'reference': self.brief.get('tender_reference'), 'missing': missing,
                                    'observations': observations}, ensure_ascii=False))
            routes = await llm.ask_json('Route unresolved evidence from runtime observations. Never invent URLs or use site-specific scripts.',
                                       prompt, tag='route', deadline=self.evidence_end, max_tokens=600)
            selected, jobs = [], {}
            for route in routes.get('routes') or []:
                role = route.get('role')
                if role not in observations or role in selected:
                    continue
                selected.append(role)
                mission = self.mission('commercial_recovery', str(route.get('goal', '')) +
                                       '\nCollect dated exact-reference records with authorized sender, using mail_message facts even when a record is not in mail. '
                                       'Read all applicable records before the cutoff, including withdrawals. Finish when the selected register is exhausted.',
                                       ['mail_message'], 12)
                mission.context = json.dumps({'authorized_records': self.brief.get('authorized_records'),
                                              'director_localpart': self.brief.get('director_localpart'), 'missing': missing}, ensure_ascii=False)
                site = roles[role]
                jobs.setdefault(id(site), []).append((site, mission))
                if len(selected) == 2:
                    break
            if not selected:
                return
            async def recover(group):
                for site, mission in group:
                    await self.run_mission(site, mission, attempts=1)
            results = await asyncio.gather(*(recover(group) for group in jobs.values()), return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    log('recovery_error', error=f'{type(result).__name__}: {result}'[:300])

    async def recheck_no_bid(self, result: Result) -> Result:
        """Not bidding is the most expensive answer when it is wrong, and one bidder answer that was missed (e.g. a
        corrected stroke) is enough to cause it. Before accepting it, one short mission reads every Q&A page again,
        fresh (no cached pages or extractions), and the rules run once more."""
        if self.tender_progress()[1] and all(self.erp_progress(group, reqs)[1] for group, reqs in self.erp_groups().items()):
            log('no_bid_evidence_complete', groups=len(self.erp_groups()))
            return result
        reasons = [line for line in result.trace if "no_bid" in line or "fitting=[]" in line or "nominal" in line]
        log("no_bid_recheck", reasons=reasons[:12])
        site = self.site_for("tender")
        site.fresh_start()
        site.collections.clear()
        self._cache = {k: v for k, v in self._cache.items() if not k.startswith("clarifications")}
        before_sources = set(self.source_evidence)
        before = len(self.bb.get("clarifications"))

        def progress() -> tuple[str, bool]:
            new = self.bb.get("clarifications")[before:]
            unread, total = self.qa_pages_unread()
            done = (any(i.get("via") == "collection" for i in new) or any(k not in before_sources and v.get("kind") == "clarifications" and v.get("via") == "collection" for k, v in self.source_evidence.items())) and not unread
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
            await asyncio.wait_for(self.run_mission(site, mission, attempts=1), timeout=max(.1, mission.deadline - time.time()))
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
            "qa": sorted({parse.qa_number(e.get("number")) for i in qa for e in i["data"].get("entries") or [] if e.get("number")}),
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
        base, started, fingerprints = self.site_for("erp"), {}, {}
        base_chain: asyncio.Task | None = None       # missions that have to run on the base tab, one after another
        try:
            while time.time() < self.evidence_end - 5:
                self.bb.changed.clear()
                spec_ended, tender_ended = self.done["specification"].is_set(), self.done["tender"].is_set()
                known_positions = bool(self.positions()) or tender_ended
                if known_positions:
                    for group, reqs in self.erp_groups().items():
                        fingerprint = digest(reqs)
                        if group in started and (fingerprints[group] == fingerprint or not started[group].done()):
                            continue
                        fingerprints[group] = fingerprint
                        mission = self.erp_mission(group, reqs)
                        site = base if base_chain is None else await self._tab(base)
                        if site is base:
                            previous = base_chain
                            started[group] = base_chain = asyncio.create_task(self._after(previous, base, mission))
                        else:
                            started[group] = asyncio.create_task(self.run_mission(site, mission))
                        started[group].add_done_callback(lambda task: self.bb.changed.set())
                        log("erp_group_started", group=group, refs=[r.get("ref") for r in reqs], own_tab=site is not base)
                if spec_ended and tender_ended and all(t.done() for t in started.values()):
                    break
                try:
                    await asyncio.wait_for(self.bb.changed.wait(), max(.1, self.evidence_end - 5 - time.time()))
                except asyncio.TimeoutError:
                    break
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

    async def _after(self, previous: asyncio.Task | None, site: Site, mission: Mission):
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        return await self.run_mission(site, mission)

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
        return self.mission(f"erp:{group}", f"""Collect ERP data for catalogue group {group} ({described}).
1. Search the materials with the group key (material search / search help; at most ~20 hits are shown, empty or too
   broad searches show nothing) and extract kind 'materials' from the COMPLETE result list, including pagination. Mark catalogue coverage complete
with sources=[the catalogue group] only after all search pages are read.
2. For EVERY material of the group (look-alikes differ in one attribute or in status) open its detail views and
   extract kind 'materials' from each: technical data, status (+ its legend) and lead times, stock down to the
   storage-location level (freely usable stock), and ALL price condition records with their quantity scales (scales
   may be on a separate tab/view; several records may be paged). Prefer extract_urls with several detail URLs.
The progress checklist tells you what is still missing per material.""", ["materials"], 35,
                            progress=lambda g=group: self.erp_progress(g, self.erp_groups().get(g, reqs)))

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
        for line in result.trace:
            log("trace", line=line)
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
                with span("evidence", agent="orchestrator", category="phase"):
                    await asyncio.wait_for(self.gather_evidence(), timeout=max(.1, self.evidence_end - time.time()))
                    await asyncio.wait_for(self.route_missing_records(), timeout=max(.1, self.evidence_end - time.time()))
        except (Exception, asyncio.TimeoutError) as error:   # never stop before submitting
            log("evidence_phase_error", error=f"{type(error).__name__}: {error}"[:500], status=self.status)
        finally:
            await self.source_reads.close()
        try:
            with span("resolve", agent="orchestrator", category="compute"):
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
            with span("submission", agent="submit", category="phase"):
                await asyncio.wait_for(submit_bid(self, result), timeout=max(.1, self.end - time.time()))
        except (Exception, asyncio.TimeoutError) as error:
            log("submit_error", error=f"{type(error).__name__}: {error}"[:500])
        await self.source_reads.close()
        log("reuse_summary", source=self.source_reads.stats, tables=self.table_bindings.stats)
        log("usage", **llm.usage)
        return result


def _page_pattern(url) -> str:
    """Keep runtime-discovered page types while hiding record values."""
    query = "&".join(f"{k}={v if v.isalpha() and len(v) <= 16 else '*'}" for k, v in parse_qsl(url.query, keep_blank_values=True))
    return url.path + (f"?{query}" if query else "")


def folder_name(value):
    return re.sub(r'\s*(?:\(\d+\)|\[\d+\])\s*$', '', parse.clean(value)).casefold()


def valid_data(kind: str, data) -> bool:
    if not isinstance(data, dict) or not data or kind not in KINDS:
        return False
    fields = {"positions": "positions", "clarifications": "entries", "spec_revisions": "revisions",
              "requirements": "requirements", "materials": "materials", "mail_list": "messages"}
    field = fields.get(kind)
    if field:
        return isinstance(data.get(field), list) and all(isinstance(row, dict) for row in data[field])
    return True


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
    return ((parsed.scheme + "://" + parsed.netloc if parsed.netloc else "") + parsed.path + "?" + parsed.query).rstrip("?")


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

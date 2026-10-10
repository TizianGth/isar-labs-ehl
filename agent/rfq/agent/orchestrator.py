"""Orchestrator: brief -> parallel evidence missions (one browser per website) -> rules -> submission.

Phases and deadlines follow docs/02-architecture.md. Every mission gets a progress checklist computed from the
blackboard, so the model always knows what is still missing and missions end as soon as their evidence is complete.
Every phase degrades gracefully: a failed mission leaves gaps that the rules handle; the submission phase always
runs, so the run ends with a signed bid whenever possible.
"""
import asyncio
import hashlib
import json
import os
import re
import time

from .. import llm
from ..browser.site import Site
from ..log import log
from ..rules import parse
from ..rules.resolve import Context, Result, build
from .explorer import Explorer, Mission
from .prompts import BRIEF_SYSTEM, EXTRACT_SYSTEM, KINDS
from .submit import submit_bid

# Qwen's reasoning combined with JSON output can run for minutes on long inputs; measured off by default.
THINKING_EXTRACT = os.environ.get("RFQ_THINKING_EXTRACT") == "1"
SUBMIT_RESERVE = float(os.environ.get("RFQ_SUBMIT_RESERVE", "480"))   # seconds kept for the submission phase
MATERIAL_REQUIRED = ["status_meaning", "bore_mm", "stroke_mm", "pressure_bar", "mounting", "unrestricted_stock",
                     "lead_time_stock_weeks", "lead_time_production_weeks", "pieces_per_carton"]


class Blackboard:
    """Append-only store of extractions: kind -> [{"data", "url", "t"}]."""

    def __init__(self):
        self.items: dict[str, list[dict]] = {}

    def add(self, kind: str, data: dict, url: str) -> None:
        self.items.setdefault(kind, []).append({"data": data, "url": url, "t": time.time()})

    def get(self, kind: str) -> list[dict]:
        return self.items.get(kind, [])

    def dump(self) -> dict:
        return {k: [{"url": i["url"], "data": i["data"]} for i in v] for k, v in self.items.items()}

    def load(self, dumped: dict) -> None:
        """Re-use the evidence of an earlier run (dev: iterate on rules/submission without re-exploring)."""
        for kind, items in dumped.items():
            for item in items:
                item = dict(item)
                url = item.pop("url", "")
                self.add(kind, item["data"] if set(item) == {"data"} else item, url)


class Orchestrator:
    def __init__(self, run: dict, sites: dict[str, Site]):
        self.run, self.sites = run, sites
        self.bb = Blackboard()
        self.end = run["expires_at"] / 1000
        self.brief: dict = {}
        self.result: Result | None = None
        self._cache: dict[str, str] = {}

    # ---------------------------------------------------------------- extraction helper (model-selected pages only)
    async def extract(self, kind: str, text: str, url: str) -> str:
        spec = KINDS.get(kind)
        if spec is None:
            return f"ERROR: unknown kind {kind}; use one of {list(KINDS)}"
        key = kind + hashlib.sha1(re.sub(r"\[(?:f\d+)?e\d+\]", "", text.split("\n", 1)[-1]).encode()).hexdigest()
        if key in self._cache:
            return "already extracted earlier: " + self._cache[key]
        prompt = (f"KIND: {kind} ({spec['describe']})\nReturn JSON with this shape:\n{spec['schema']}\n\n"
                  f"Task context: tender reference {self.brief.get('tender_reference')}, scenario date "
                  f"{self.brief.get('scenario_datetime_utc')}.\nPAGE URL: {url}\n<<<PAGE TEXT>>>\n{text[:60000]}\n<<<END>>>")
        data = await llm.ask_json(EXTRACT_SYSTEM, prompt, thinking=THINKING_EXTRACT, tag=f"extract:{kind}")
        if not data:
            return "extraction returned nothing"
        self.bb.add(kind, data, url)
        log("extracted", kind=kind, url=url[:160], data=data)
        self._cache[key] = summary = _summary(kind, data)
        return summary

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
                       deadline=self.end - SUBMIT_RESERVE, context=context, progress=progress)

    async def explore(self, site: Site, mission: Mission) -> Explorer | None:
        try:
            return await Explorer(site, mission, self.extract).run()
        except Exception as error:
            log("mission_crashed", mission=mission.name, error=f"{type(error).__name__}: {error}"[:500])
            return None

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
        collected = any("#collected" in i["url"] for i in qa)
        lines = [
            f"[{'x' if positions else ' '}] positions: " + (", ".join(f"{p['position']} {p.get('quantity')} {p.get('unit')} "
                                                                       f"-> {p.get('requirement_ref')}" for p in positions) or "not found yet"),
            f"[{'x' if entries else ' '}] Q&A entries: {len(entries)}" + ("" if collected or not entries else
                                                                          " (only one page extracted: make sure ALL pages were read)"),
        ]
        return "\n".join(lines), bool(positions and entries and collected)

    def current_revision(self) -> str | None:
        current = None
        for item in self.bb.get("spec_revisions"):
            for r in item["data"].get("revisions") or []:
                status = parse.clean(r.get("status_text")).lower()
                if r.get("is_current") is True or any(w in status for w in ("gültig", "aktuell", "current")):
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

    def spec_progress(self) -> tuple[str, bool]:
        current, reqs = self.current_revision(), self.requirements()
        needed = {parse.norm_id(p.get("requirement_ref")) for p in self.positions() if p.get("requirement_ref")}
        missing = sorted(needed - set(reqs))
        lines = [f"[{'x' if current else ' '}] revision marked current: {current or 'unknown (extract the revision list with statuses)'}",
                 f"[{'x' if reqs else ' '}] requirements of the current revision: {sorted(reqs) or 'none yet'}"]
        if needed:
            lines.append(f"[{'x' if not missing else ' '}] requirements referenced by the tender positions: "
                         f"{sorted(needed)}" + (f" — missing {missing}" if missing else ""))
        complete = bool(current and reqs and (not missing if needed else any("#collected" in i["url"] for i in self.bb.get("requirements"))))
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
        topics = sorted({i["data"].get("topic") for i in self.bb.get("mail_message") if i["data"].get("topic")})
        missing = [f"{subject} -> {url}" for url, subject in listed.items() if url not in done]
        unread_pages = {f: sorted(set(range(1, totals[f] + 1)) - pages[f]) for f in totals if set(range(1, totals[f] + 1)) - pages[f]}
        lines = [f"folders listed: " + (", ".join(f"{f} pages {sorted(p)}" + (f" of {totals[f]}" if f in totals else "")
                                                 for f, p in pages.items()) or "none") + " (check inbox AND archive/other folders)",
                 f"messages opened: {len(done)}; topics found: {topics}"]
        if unread_pages:
            lines.append(f"list pages not read yet: {unread_pages} (read_all_pages on the list, then extract mail_list)")
        if missing:
            lines.append("listed but not opened yet (use extract_urls with kind mail_message):\n  " + "\n  ".join(missing[:30]))
        return "\n".join(lines), len(pages) >= 2 and bool(listed) and not missing and not unread_pages

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
   document links (one link per document).
3. ALL bidder questions & answers (Q&A / Bieterfragen) of every page: use read_all_pages on the list's 'next page'
   link (it may sit inside a frame, ref like f2e7), then extract kind 'clarifications' with source='collected'.
Do not start the bid submission, do not ask questions.""", ["tender_overview", "positions", "clarifications"], 30,
            progress=self.tender_progress)
        spec = self.mission("specification", f"""Find the buyer's technical specification (Lastenheft / technical
specification / data sheet) for tender {ref} and collect the requirements of the revision MARKED CURRENT
(status such as "gültig"/"aktuell"; the viewer may open an old revision first; dates may use mixed formats).
1. Pass any document-release form honestly: choose the binding/reference option (not archive copy, not preview),
   copy the receipt number shown, tick the required acknowledgement, request read access.
2. Extract the revision list with statuses (kind 'spec_revisions'); statuses may be tooltips ([info: …]).
3. Open the CURRENT revision and read ALL requirement pages (read_all_pages on its 'next page' link), then extract
   kind 'requirements' with source='collected'.""", ["spec_revisions", "requirements"], 30, progress=self.spec_progress)
        mail = self.mission("mail", f"""In the sales mailbox, collect every message about tender {ref} that is one of:
- the sales director's discount approval (sender '{self.brief.get('director_localpart')}@<bidder domain>')
{topics}
Mail search combines words with AND. Strategy: search the exact reference alone; read all result pages
(read_all_pages on the 'next page' link) and extract kind 'mail_list' with source='collected'; then extract_urls with
kind 'mail_message' for EVERY listed message whose subject contains the reference (skip signing-TAN messages;
look-alike references are fine to open, they are filtered later). Then switch to the archive folder (and any other
folder) and do the same. If a search says there are too many results, add the subject keyword.""",
            ["mail_list", "mail_message"], 40, progress=self.mail_progress)
        results = await asyncio.gather(self.explore(self.site_for("tender"), tender),
                                       self.explore(self.site_for("specification"), spec),
                                       self.explore(self.site_for("mail"), mail), self.erp_phase())
        flat = [r for r in results if not isinstance(r, list)] + [x for r in results if isinstance(r, list) for x in r]
        log("evidence_done", missions=[(r.mission.name, r.summary[:200], r.missing) if r else None for r in flat],
            kinds={k: len(v) for k, v in self.bb.items.items()})

    async def erp_phase(self) -> list:
        """One ERP mission per catalogue group, in parallel tabs of the ERP browser (shared session)."""
        while not self.spec_progress()[1] and time.time() < self.end - SUBMIT_RESERVE - 240:
            await asyncio.sleep(2)
        reqs = self.requirements()
        needed = {parse.norm_id(p.get("requirement_ref")) for p in self.positions() if p.get("requirement_ref")}
        if needed & set(reqs):
            reqs = {k: v for k, v in reqs.items() if k in needed}   # only what the tender asks for
        groups: dict[str, list[dict]] = {}
        for r in reqs.values():
            groups.setdefault(parse.clean(r.get("catalog_group")) or "?", []).append(r)
        log("erp_groups", groups={g: [r.get("ref") for r in rs] for g, rs in groups.items()})
        base = self.site_for("erp")
        missions = []
        for index, (group, rs) in enumerate(groups.items()):
            described = "; ".join(f"requirement {r.get('ref')}: bore {r.get('bore_mm')}, stroke {r.get('stroke_mm')}"
                                  f"{' (usable stroke; the nominal ERP stroke is longer)' if r.get('usable_stroke') else ''}, "
                                  f"pressure {r.get('pressure_bar')}, mounting {r.get('mounting')}, certification "
                                  f"{r.get('certification') or 'none'}" for r in rs)
            mission = self.mission(f"erp:{group}", f"""Collect ERP data for catalogue group {group} ({described}).
1. Search the materials with the group key (material search / search help; at most ~20 hits are shown, empty or too
   broad searches show nothing) and extract kind 'materials' from the result list.
2. For EVERY material of the group (look-alikes differ in one attribute or in status) open its detail views and
   extract kind 'materials' from each: technical data, status (+ its legend) and lead times, stock down to the
   storage-location level (freely usable stock), and ALL price condition records with their quantity scales (scales
   may be on a separate tab/view; several records may be paged). Prefer extract_urls with several detail URLs.
The progress checklist tells you what is still missing per material.""", ["materials"], 35,
                                   progress=lambda g=group, rs=rs: self.erp_progress(g, rs))
            site = base if index == 0 else await base.spawn()
            missions.append(self.explore(site, mission))
        return list(await asyncio.gather(*missions))

    # ---------------------------------------------------------------- rules + submission
    def resolve(self) -> Result:
        brief = self.brief
        authorized = {r.get("topic"): r.get("sender_localpart") for r in brief.get("authorized_records") or []
                      if r.get("topic") and r.get("sender_localpart")}
        ctx = Context(tender_reference=parse.clean(brief.get("tender_reference")),
                      scenario=parse.when(brief.get("scenario_datetime_utc")) or parse.when("2000-01-01T00:00:00Z"),
                      director_localpart=brief.get("director_localpart"), authorized=authorized)
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
                log("replay", kinds={k: len(v) for k, v in self.bb.items.items()})
            else:
                await self.read_brief()
                await asyncio.wait_for(self.gather_evidence(), timeout=max(60, self.end - SUBMIT_RESERVE - time.time()))
        except Exception as error:   # never stop before submitting
            log("evidence_phase_error", error=f"{type(error).__name__}: {error}"[:500])
        try:
            result = self.resolve()
        except Exception as error:
            log("resolve_error", error=f"{type(error).__name__}: {error}"[:500])
        if result is None:
            result = Result({"tender_reference": self.brief.get("tender_reference", ""), "decision": "bid",
                             "currency": "EUR", "discount_percent": 0, "positions": [], "total_net": 0}, [], [], {})
        self.result = result
        try:
            await submit_bid(self, result)
        except Exception as error:
            log("submit_error", error=f"{type(error).__name__}: {error}"[:500])
        log("usage", **llm.usage)
        return result


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


def _summary(kind: str, data: dict) -> str:
    """A complete but compact confirmation of what was extracted, for the navigating model."""
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
        return f"folder {data.get('folder')}, mailbox {data.get('mailbox_address')}:\n" + "\n".join(
            f"  {m.get('subject')} | {m.get('sender')} | {m.get('datetime_utc')} | {m.get('url')}" for m in data.get("messages") or [])
    if kind == "mail_message":
        return f"'{data.get('subject')}' from {data.get('sender')} ({data.get('topic')})"
    return json.dumps(data, ensure_ascii=False)[:500]

"""One inference reads the current page and chooses its next operations."""
import asyncio
import json
import time

from .. import llm
from ..log import log
from ..profile import span
from .explorer import ACTIONS, OUTSIDE_STATE, Explorer, _hints, _links
from .prompts import EXTRACT_SYSTEM, FUSED_SYSTEM, KINDS
from .routing import choose
from .records import ActionResult, Decision, Observation


def field_guide(kind):
    """The original fact contract with repeated prose/whitespace removed."""
    return ' '.join(KINDS[kind]['schema'].split())


class FusedExplorer(Explorer):
    snapshot: Observation | None = None

    async def _loop(self, tools, tag):
        if hasattr(self.site, 'wait_started'):
            await self.site.wait_started()
        allowed = {t['function']['name'] for t in tools} | {'read_region', 'bind_fields'}
        operation_guide = '\n'.join(
            t['function']['name'] + '(' + ','.join(t['function']['parameters'].get('properties', {})) + ')'
            for t in tools)
        operation_guide += '\nfill_fields(fields:[{ref,value,apply?}],then_click?); read_region(region); bind_fields(fields:[{ref,path,format?,apply?}],then_click?)'
        intro = (f'MISSION {self.mission.name} on {self.site.site_id}: {self.mission.goal}\n'
                 f'CONTEXT: {self.mission.context}\nOPERATIONS:\n{operation_guide}')
        orch = getattr(self.extractor, '__self__', None)
        repeats = {}
        premature_finish = False
        self.source_cache = {}
        for step in range(self.mission.max_steps):
            if self.mission.deadline - time.time() < 5:
                self.status = 'deadline'
                self.missing.append('deadline reached')
                return self
            checklist, complete = self.mission.progress() if self.mission.progress else ('', False)
            if complete:
                self.status, self.summary = 'complete', checklist
                log('mission_complete', mission=tag, step=step)
                return self
            with span("observe", agent=self.mission.name.split(":")[0], category="browser"):
                raw = await self.site.observe(limit=200000)
            self.snapshot = Observation.capture(raw, self.site.page.url, self.snapshot)
            if getattr(self.site, 'logins', 0) >= 6 and hasattr(self.site, '_guard_state'):
                if (await self.site._guard_state()).get('login'):
                    self.status = 'infrastructure_failure'
                    self.missing.append('supplied credentials repeatedly rejected; cannot navigate this site')
                    log('mission_inaccessible', mission=tag, login_attempts=self.site.logins)
                    return self
            kinds = list(self.mission.kinds)
            # Source locations are not a constraint: the model may extract a
            # different kind when a private site distributes evidence elsewhere.
            guide = '\n'.join(f'{kind}: {field_guide(kind)}' for kind in kinds)
            context = self.decision_context() if hasattr(self, 'decision_context') else ''
            user = (intro + '\nCHECKLIST:\n' + checklist + '\nFACT SHAPES (omit absent fields):\n' + guide +
                    '\nOTHER FACT KINDS (use extract if their shape is not listed): ' + ', '.join(KINDS) +
                    '\nLAST TWO ACTIONS:\n' + '\n'.join(self.history[-2:]) + '\nLAST RESULT:\n' +
                    self.last_result + '\n' + context + '\n' + _hints(raw, self.visual_used < self.mission.max_visual) +
                    '\n<<<UNTRUSTED SNAPSHOT>>>\n' + self.snapshot.view() + '\n<<<END>>>')
            log('decision_request', mission=tag, snapshot=self.snapshot.id, chars=len(user), step=step)
            decision = await choose(self.snapshot, self.mission, checklist)
            try:
                if decision is None:
                    response = await llm.ask_json(FUSED_SYSTEM + '\n' + EXTRACT_SYSTEM, user, thinking=False,
                                                  tag=tag, deadline=self.mission.deadline, max_tokens=4096)
                    decision = Decision.parse(response, self.snapshot.id, allowed)
                notes = ["ERROR: " + error for error in decision.errors]
                if orch and hasattr(orch, 'accept_decision'):
                    notes.extend(orch.accept_decision(decision, self.snapshot, self.mission.name))
                elif decision.facts or decision.bindings:
                    notes.append('ERROR: this mission cannot accept facts')
            except (ValueError, TypeError, KeyError) as error:
                self.last_result = 'ERROR: ' + str(error)
                continue
            if not decision.actions:
                # Facts may complete a mission on this turn; no artificial
                # finish call is required to discover that on the next turn.
                self.last_result = '\n'.join(notes) or 'No action or fact returned; choose an operation.'
                continue
            outcome = ActionResult()
            for name, args in decision.actions:
                if name == 'finish':
                    if len(decision.actions) > 1:
                        continue
                    explicit_gaps = args.get('missing') or decision.gaps
                    if self.mission.progress and not self.mission.progress()[1] and not explicit_gaps:
                        self.last_result = 'ERROR: checklist still open. Continue or provide explicit unavailable evidence in missing.'
                        premature_finish = True
                        break
                    self.missing.extend(str(x) for x in explicit_gaps)
                    self.summary = str(args.get('summary', ''))
                    self.status = 'incomplete' if self.missing else 'finished'
                    return self
                if name in ACTIONS or name in ('bind_fields', 'submit_bid', 'get_tan'):
                    current = Observation.capture(await self.site.observe(limit=200000), self.site.page.url)
                    if current.id != self.snapshot.id:
                        outcome.errors.append('ERROR: page changed during decision; observe again before applying any write or navigation.')
                        break
                signature = self.snapshot.id + name + json.dumps(args, sort_keys=True)
                repeats[signature] = repeats.get(signature, 0) + 1
                if repeats[signature] > 2 and name not in OUTSIDE_STATE:
                    outcome.errors.append('Repeated action without progress; choose another route.')
                    break
                if name == 'read_region':
                    result = self.snapshot.regions.get(str(args.get('region')), 'ERROR: unknown region')
                elif name == 'bind_fields':
                    result = await self.bind_fields(args) if hasattr(self, 'bind_fields') else 'ERROR: no bid value bindings in this mission'
                else:
                    with span("action", agent=self.mission.name.split(":")[0], category="browser"):
                        result = await self._call(name, args)
                outcome.applied.append(name)
                notes.append(result)
                self.history.append(name + ' -> ' + result[:160])
                log('action', mission=tag, step=step, tool=name, args=args, result=result[:300])
                if self.stop:
                    return self
                if result.startswith(('ERROR', 'NOT_DISPATCHED')) or 'STOPPED' in result or 'OUTCOME UNKNOWN' in result:
                    outcome.errors.append(result)
                    break
                # Every interaction can rerender a different frame/control while
                # keeping the URL. Do not apply stale bindings to that new state.
                if name in ACTIONS or name == 'bind_fields':
                    after = Observation.capture(await self.site.observe(limit=200000), self.site.page.url)
                    outcome.next_snapshot = after.id
                    if after.id != self.snapshot.id:
                        break
            if not premature_finish:
                self.last_result = '\n'.join(notes + outcome.errors)
            premature_finish = False
        self.status = 'incomplete'
        self.missing.append('step budget exhausted')
        return self

    async def _extract_urls(self, args):
        kind = args.get('kind') or (next(iter(self.mission.kinds)) if len(self.mission.kinds) == 1 else None)
        if kind not in KINDS:
            return 'ERROR: select a fact kind before opening sources'
        selected = list(dict.fromkeys(self.site.absolute(str(u)) for u in args.get('urls') or []))[:12]
        gate = asyncio.Semaphore(4)
        async def fetch(url):
            if not self.site.was_seen(url):
                return url, 'ERROR: URL was not observed'
            key = (kind, url, getattr(self.site, 'session_generation', 0))
            if key in self.source_cache:
                return url, self.source_cache[key]
            async with gate:
                return url, await self.site.read_url(url)
        fetched = await asyncio.gather(*(fetch(url) for url in selected))
        items = [(url, text) for url, text in fetched if not text.startswith('ERROR')]
        orch = getattr(self.extractor, '__self__', None)
        if orch and hasattr(orch, 'extract_many'):
            summaries = await orch.extract_many(kind, items, via='page') if items else []
        else:
            summaries = await asyncio.gather(*(self.extractor(kind, text, url, via='page') for url, text in items))
        blocks = [url + ': ' + text for url, text in fetched if text.startswith('ERROR')]
        for (url, text), summary in zip(items, summaries):
            if not summary.startswith('extraction returned nothing'):
                self.source_cache[(kind, url, getattr(self.site, 'session_generation', 0))] = text
            # All offered links are returned. No hidden "offered" set suppresses
            # records that exceeded a previous prompt's character limit.
            links = _links([(url, text)])
            blocks.append(url + ': ' + summary + ('\nSOURCE LINKS:\n' + '\n'.join(label + ' -> ' + link for label, link in links) if links else ''))
        return '\n'.join(blocks)

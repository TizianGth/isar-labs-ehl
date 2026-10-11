"""One inference reads the current page and chooses its next operations."""
import asyncio
import json
import os
import re
from urllib.parse import urlparse, unquote
import time

from .. import llm
from ..browser import documents
from ..log import log
from ..profile import span
from .explorer import ACTIONS, OUTSIDE_STATE, Explorer, _hints, _links
from .prompts import (EXTRACT_SYSTEM, FUSED_SYSTEM, FUSED_TABLE_SYSTEM, FUSED_FORM_SYSTEM,
                      FUSED_MAIL_SYSTEM, FUSED_PREFETCH_SYSTEM, KINDS, FIELD_GUIDES)
from .routing import choose
from .records import ActionResult, Decision, Observation


def field_guide(kind):
    """The original fact contract with repeated prose/whitespace removed."""
    return FIELD_GUIDES.get(kind, ' '.join(KINDS[kind]['schema'].split()))


def compact_progress(text):
    lines = text.splitlines()
    done = [line for line in lines if line.startswith('[x]')]
    return '\n'.join(([f'{len(done)} completed checks; read_progress for full details.'] if done else []) +
                     [line for line in lines if not line.startswith('[x]')])


def prompt_guides(snapshot, kinds, requested=()):
    # Pure controls/headers do not need business schemas. The model can request
    # any schema; source locations never restrict available evidence kinds.
    substantive = [line for line in snapshot.text.splitlines()
                   if line.strip() and not re.search(r'\[(?:f\d+)?e\d+\]|^heading |^=== frame ', line)]
    selected = list(dict.fromkeys(list(kinds if substantive else []) + list(requested)))
    return '\n'.join(f'{kind}: {field_guide(kind)}' for kind in selected if kind in KINDS)


class FusedExplorer(Explorer):
    snapshot: Observation | None = None

    async def _loop(self, tools, tag):
        if hasattr(self.site, 'wait_started'):
            await self.site.wait_started()
        allowed = {t['function']['name'] for t in tools} | {'read_region', 'field_guide', 'read_progress', 'read_result'}
        if hasattr(self, 'bind_fields'):
            allowed.add('bind_fields')
        if hasattr(self, 'reuse_fields'):
            allowed.add('reuse_fields')
        if 'read_document' in allowed:
            allowed.add('prefetch_documents')
        operation_guide = '\n'.join(
            t['function']['name'] + '(' + ','.join(t['function']['parameters'].get('properties', {})) + ')'
            for t in tools)
        operation_guide += '\nfill_fields(fields:[{ref,value,apply?}],then_click?); read_region(region);'
        if 'bind_fields' in allowed:
            operation_guide += ' bind_fields(fields:[{ref,path,format?,apply?}],then_click?,reuse?);'
        operation_guide += '\nfield_guide(kinds); read_progress(); read_result();'
        if 'prefetch_documents' in allowed:
            operation_guide += ' prefetch_documents(urls≤2);'
        if 'reuse_fields' in allowed:
            operation_guide += ' reuse_fields(handle,position);'
        intro = (f'MISSION {self.mission.name} on {self.site.site_id}: {self.mission.goal}\n'
                 f'CONTEXT: {self.mission.context}\nOPERATIONS:\n{operation_guide}')
        orch = getattr(self.extractor, '__self__', None)
        repeats = {}
        premature_finish = False
        self.requested_guides = []
        self.prefetched = {}
        self.result_views = {}
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
            self.full_progress = checklist
            if orch and hasattr(orch, 'reuse_tables'):
                reused = orch.reuse_tables(self.snapshot, self.mission.name)
                if reused:
                    self.last_result = '\n'.join(reused)
                    checklist, complete = self.mission.progress() if self.mission.progress else ('', False)
                    if complete:
                        self.status, self.summary = 'complete', checklist
                        return self
            kinds = list(self.mission.kinds)
            # Source locations are not a constraint: the model may extract a
            # different kind when a private site distributes evidence elsewhere.
            guide = prompt_guides(self.snapshot, kinds, self.requested_guides)
            context = self.decision_context() if hasattr(self, 'decision_context') else ''
            user = (intro + '\nCHECKLIST:\n' + compact_progress(checklist) + '\nFACT SHAPES (JSON keys/nesting; raw values unless specified; flags=JSON booleans, page counts=integers; omit absent fields):\n' + guide +
                    '\nOTHER FACT KINDS (use extract if their shape is not listed): ' + ', '.join(KINDS) +
                    '\nLAST TWO ACTIONS:\n' + '\n'.join(self.history[-2:]) + '\nLAST RESULT:\n' +
                    self.result_view() + '\n' + context + '\n' + _hints(raw, self.visual_used < self.mission.max_visual) +
                    '\n<<<UNTRUSTED SNAPSHOT>>>\n' + self.snapshot.view(limit=max(2400, int(os.environ.get('RFQ_DECISION_VIEW_CHARS', '6500'))),
                        sample_rows=int(os.environ.get('RFQ_TABLE_SAMPLES', '3'))) + '\n<<<END>>>')
            log('decision_request', mission=tag, snapshot=self.snapshot.id, chars=len(user), step=step)
            decision = await choose(self.snapshot, self.mission, checklist)
            try:
                if decision is None:
                    system = FUSED_SYSTEM + '\n' + EXTRACT_SYSTEM
                    if self.snapshot.tables:
                        system += '\n' + FUSED_TABLE_SYSTEM
                    if 'bind_fields' in allowed:
                        system += '\n' + FUSED_FORM_SYSTEM
                    if 'mail_message' in kinds or 'mail_message' in self.requested_guides:
                        system += '\n' + FUSED_MAIL_SYSTEM
                    if 'prefetch_documents' in allowed and re.search(r'\.(pdf|csv|txt|zip)\b', raw, re.I):
                        system += '\n' + FUSED_PREFETCH_SYSTEM
                    response = await llm.ask_json(system, user, thinking=False,
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
                if name in ACTIONS or name in ('bind_fields', 'reuse_fields', 'submit_bid', 'get_tan'):
                    current = Observation.capture(await self.site.observe(limit=200000), self.site.page.url)
                    if current.id != self.snapshot.id:
                        outcome.errors.append('ERROR: page changed during decision; observe again before applying any write or navigation.')
                        break
                signature = self.snapshot.id + name + json.dumps(args, sort_keys=True)
                repeats[signature] = repeats.get(signature, 0) + 1
                if repeats[signature] > 2 and name not in OUTSIDE_STATE:
                    outcome.errors.append('Repeated action without progress; choose another route.')
                    break
                if name in ACTIONS or name in ('bind_fields', 'reuse_fields', 'submit_bid', 'get_tan'):
                    await self.clear_prefetch()
                if name == 'field_guide':
                    self.requested_guides = [kind for kind in args.get('kinds', []) if kind in KINDS]
                    result = 'Requested schemas will appear in the next decision.'
                elif name == 'read_progress':
                    result = self.full_progress
                elif name == 'read_result':
                    result = self.result_views.get(args.get('handle'), self.last_result)
                    self.expanded_result = result
                elif name == 'prefetch_documents':
                    result = await self.prefetch_documents(args)
                elif name == 'reuse_fields':
                    result = await self.reuse_fields(args)
                elif name == 'read_region':
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
                if name in ACTIONS or name in ('bind_fields', 'reuse_fields'):
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
        selected = list(dict.fromkeys(self.site.absolute(str(u)) for u in args.get('urls') or []))
        gate = asyncio.Semaphore(max(1, min(8, int(os.environ.get('RFQ_SOURCE_PARALLEL', '4')))))
        async def fetch(url):
            if not self.site.was_seen(url):
                return url, 'ERROR: URL was not observed'
            async with gate:
                orch = getattr(self.extractor, '__self__', None)
                if orch and hasattr(orch, 'source_reads'):
                    text = await orch.source_reads.read(self.site, url, lambda: self.site.read_url(url))
                else:
                    text = await self.site.read_url(url)
                return url, text
        async def consume(group):
            fetched = await asyncio.gather(*(fetch(url) for url in group))
            items = [(url, text) for url, text in fetched if not text.startswith('ERROR')]
            orch = getattr(self.extractor, '__self__', None)
            if orch and hasattr(orch, 'extract_many'):
                summaries = [None] * len(items)
                remaining = []
                for index, (url, text) in enumerate(items):
                    reading = Observation.capture(text, url)
                    scope = orch.binding_scope(self.mission.name, reading)
                    known = orch.table_readings.get((scope, reading.id, kind), {})
                    if reading.tables and set(known) == set(reading.tables):
                        summaries[index] = '\n'.join(known.values())
                        continue
                    bindings = [(stamp, binding) for stamp, binding in orch.table_bindings.candidates(reading, scope)
                                if binding['kind'] == kind and binding.get('source_complete') is True
                                and set(binding.get('columns', {}).values()) == set(range(len(binding['headers'])))]
                    if reading.tables and {binding['table'] for _, binding in bindings} == set(reading.tables):
                        notes = orch.reuse_tables(reading, self.mission.name)
                        if notes and all(not note.startswith(('ERROR', 'extraction returned nothing')) for note in notes):
                            summaries[index] = '\n'.join(notes)
                            continue
                    remaining.append((index, (url, text)))
                fresh = await orch.extract_many(kind, [item for _, item in remaining], via='page') if remaining else []
                for (index, _), summary in zip(remaining, fresh):
                    summaries[index] = summary
            else:
                summaries = await asyncio.gather(*(self.extractor(kind, text, url, via='page') for url, text in items))
            blocks = [url + ': ' + text for url, text in fetched if text.startswith('ERROR')]
            for (url, text), summary in zip(items, summaries):
                # All offered links are returned. No hidden "offered" set suppresses
                # records that exceeded a previous prompt's character limit.
                links = _links([(url, text)])
                blocks.append(url + ': ' + summary + ('\nSOURCE LINKS:\n' + '\n'.join(label + ' -> ' + link for label, link in links) if links else ''))
            return '\n'.join(blocks)
        batch = max(1, min(4, int(os.environ.get('RFQ_EXTRACT_BATCH', '3'))))
        # Begin extracting ready groups while other selected sources still load.
        # Group width preserves batching; all selected URLs remain accounted for.
        tasks = [asyncio.create_task(consume(selected[index:index + batch]))
                 for index in range(0, len(selected), batch)]
        try:
            return '\n'.join(await asyncio.gather(*tasks))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    def result_view(self):
        expanded = getattr(self, 'expanded_result', None)
        if expanded is not None:
            self.expanded_result = None
            return expanded
        limit = max(400, int(os.environ.get('RFQ_RESULT_CHARS', '1200')))
        if len(self.last_result) <= limit:
            return self.last_result
        from .records import digest
        handle = digest(self.last_result)
        self.result_views[handle] = self.last_result
        return self.last_result[:limit] + f'\n[{len(self.last_result)} chars; read_result(handle="{handle}") for exact full result and links]'

    async def run(self, tools=None):
        try:
            return await super().run(tools)
        finally:
            await self.clear_prefetch()
            if hasattr(self, 'form_stats'):
                log('form_reuse_summary', **self.form_stats)

    async def clear_prefetch(self):
        tasks = [item[2] for item in getattr(self, 'prefetched', {}).values()]
        self.prefetched = {}
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def prefetch_documents(self, args):
        urls = list(dict.fromkeys(self.site.absolute(str(url)) for url in args.get('urls', [])))
        if len(set(urls) | set(self.prefetched)) > 2:
            return 'ERROR: at most two selected documents may be prefetched'
        # Document GETs only: no helper-tab guards, button clicks or form actions.
        if any(not self.site.was_seen(url) or urlparse(url).netloc != self.site.origin or
               not urlparse(url).path.lower().endswith(('.pdf', '.csv', '.txt', '.zip')) for url in urls):
            return 'ERROR: prefetch requires observed same-site file links'
        for url in urls:
            if url not in self.prefetched:
                self.prefetched[url] = (self.snapshot.id, self.site.session_generation,
                    asyncio.create_task(self._prefetch_file(url)))
        return f'Prefetch started for {len(urls)} selected documents; no extraction performed.'

    async def _read_document(self, args):
        url = self.site.absolute(str(args['url'])) if args.get('url') else None
        saved = self.prefetched.pop(url, None)
        if saved and saved[:2] == (self.snapshot.id, self.site.session_generation) and not args.get('member'):
            result, etag = await saved[2]
            if etag:
                # Revalidate even when the source page did not change. A strong
                # validator is the only permission to reuse completed file bytes.
                response = await self.site.context.request.get(url, headers={'If-None-Match': etag},
                    timeout=max(1, min(4000, (self.mission.deadline - time.time()) * 1000)))
                if response.status != 304:
                    result = await self._document_response(response, url)
            else:
                result = await documents.read_document(self.site, url=url)
        else:
            if saved:
                saved[2].cancel()
                await asyncio.gather(saved[2], return_exceptions=True)
            result = await documents.read_document(self.site, ref=args.get('ref'), url=args.get('url'), member=args.get('member'))
        if result.error:
            return 'ERROR: ' + result.error
        orch = getattr(self.extractor, '__self__', None)
        if orch and hasattr(orch, 'source_evidence'):
            from .records import digest
            orch.source_evidence[digest([result.source, result.text])] = {'url': result.source, 'text': result.text, 'via': 'document'}
        if args.get('kind') and result.text:
            summary = await self.extractor(str(args['kind']), result.text, result.source, via='document')
            return result.describe() + '\nextracted: ' + summary
        return result.describe() + '\n' + result.text

    async def _document_response(self, response, url):
        if response.status >= 400 or urlparse(response.url).netloc != self.site.origin:
            return documents.Document(url, error='document response failed origin/status validation')
        data = documents._limit(await response.body())
        name = unquote(urlparse(response.url).path.rsplit('/', 1)[-1])
        mime = response.headers.get('content-type', '').split(';')[0].strip()
        return documents.parse(data, name, mime, response.url, None)

    async def _prefetch_file(self, url):
        with span('prefetch_document', agent=self.mission.name.split(':')[0], category='browser'):
            response = await self.site.context.request.get(url,
                timeout=max(1, min(4000, (self.mission.deadline - time.time()) * 1000)))
            document = await self._document_response(response, url)
            etag = response.headers.get('etag', '')
            return document, etag if etag.startswith('"') and etag.endswith('"') else ''

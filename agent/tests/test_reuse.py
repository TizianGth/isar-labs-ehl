"""Run-local reuse must preserve current evidence and model-selected semantics."""
import asyncio
import re
import time
from types import SimpleNamespace

import pytest

from rfq import llm
from rfq.agent import orchestrator as module
from rfq.agent.explorer import Mission
from rfq.agent.fused import FusedExplorer, compact_progress, prompt_guides, field_guide
from rfq.agent.orchestrator import Orchestrator
from rfq.agent.prompts import FIELD_GUIDES, KINDS
from rfq.agent.records import Observation, Decision
from rfq.agent.reuse import SourceReads, TableBindings
from rfq.agent.submit import SubmitExplorer
from rfq.browser import documents


def site(context=None):
    return SimpleNamespace(context=context or object(), site_id='synthetic', session_generation=0,
                           origin='synthetic.test', url='https://synthetic.test')


def test_concurrent_reads_share_across_kinds_but_later_reads_are_fresh():
    async def scenario():
        pool, s, release = SourceReads(), site(), asyncio.Event()
        calls = []
        async def load():
            calls.append(len(calls))
            await release.wait()
            return str(len(calls))
        one = asyncio.create_task(pool.read(s, '/source', load))
        two = asyncio.create_task(pool.read(s, '/source', load))
        await asyncio.sleep(0)
        release.set()
        assert await asyncio.gather(one, two) == ['1', '1']
        assert await pool.read(s, '/source', load) == '2'
        s.session_generation += 1
        assert await pool.read(s, '/source', load) == '3'
        assert pool.stats == {'loads': 3, 'shared': 1}
        await pool.close()
    asyncio.run(scenario())


def test_cancellation_does_not_kill_another_read_waiter():
    async def scenario():
        pool, s, release = SourceReads(), site(), asyncio.Event()
        async def load():
            await release.wait()
            return 'current'
        one = asyncio.create_task(pool.read(s, '/source', load))
        two = asyncio.create_task(pool.read(s, '/source', load))
        await asyncio.sleep(0)
        one.cancel()
        await asyncio.gather(one, return_exceptions=True)
        release.set()
        assert await two == 'current'
        await pool.close()
    asyncio.run(scenario())


def test_source_parallelism_is_per_browser_context():
    async def scenario():
        pool, release = SourceReads(limit=1), asyncio.Event()
        entered = []
        async def load():
            entered.append(1)
            if len(entered) == 2:
                release.set()
            await release.wait()
            return 'ok'
        tasks = [pool.read(site(), '/source', load), pool.read(site(), '/source', load)]
        assert await asyncio.wait_for(asyncio.gather(*tasks), .5) == ['ok', 'ok']
        await pool.close()
    asyncio.run(scenario())


def binding():
    return {'kind': 'materials', 'table': 't1', 'headers': ['Material', 'Stock'],
            'columns': {'material_number': 0, 'unrestricted_stock': 1},
            'list_field': 'materials', 'reuse': True, 'source_complete': True}


def observation(stock='1.234,50 (de-DE)', context='Freely usable stock', frame=''):
    return Observation.capture(frame + context + '\n| Material | Stock |\n| MAT-S | ' + stock + ' |',
                               'https://synthetic.test/data')


def test_binding_reuse_reads_changed_values_and_preserves_all_rows():
    cache = TableBindings()
    original = observation()
    cache.remember(original, binding(), ('erp', 0))
    changed = observation('9.876,50 (de-DE)')
    stamp, replay = next(cache.candidates(changed, ('erp', 0)))
    from rfq.agent.records import table_data
    assert table_data(changed, replay)['materials'][0]['unrestricted_stock'] == '9.876,50 (de-DE)'
    cache.accepted(stamp)
    assert not list(cache.candidates(changed, ('erp', 0)))
    many = Observation.capture('Freely usable stock\n| Material | Stock |\n' + '\n'.join(
        f'| MAT-S{i} | {i},50 (de-DE) |' for i in range(120)), original.url)
    _, replay = next(cache.candidates(many, ('erp', 0)))
    assert len(table_data(many, replay)['materials']) == 120


@pytest.mark.parametrize('changed', [observation('1,234.50 (en-US)'), observation(context='Inspection stock'),
    observation(frame='=== frame 1: https://synthetic.test/other ===\n'),
    Observation.capture('| Stock | Material |\n| 1 | MAT-S |', 'https://synthetic.test/data'),
    Observation.capture('Freely usable stock\n| Material | Stock |\n| MAT-S | 1 | extra |', 'https://synthetic.test/data')])
def test_changed_context_locale_frames_or_shape_reject_binding(changed):
    cache = TableBindings()
    cache.remember(observation(), binding(), ('erp', 0))
    assert not list(cache.candidates(changed, ('erp', 0)))
    assert not list(cache.candidates(observation('2 (de-DE)'), ('erp', 1)))


def test_reused_selected_table_avoids_extraction_request(monkeypatch):
    async def scenario():
        s = site()
        s.absolute = lambda url: url
        s.was_seen = lambda url: True
        changed = observation('2 (de-DE)')
        async def read_url(url):
            return changed.text
        s.read_url = read_url
        o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {'synthetic': s})
        o.table_bindings.remember(observation(), binding(), o.binding_scope('erp', observation()))
        ex = FusedExplorer(s, Mission('erp', 'collect', {'materials': ''}), o.extract)
        async def fail(*args, **kwargs):
            pytest.fail('validated selected table should not require inference')
        monkeypatch.setattr(llm, 'ask_json', fail)
        result = await ex._extract_urls({'kind': 'materials', 'urls': [changed.url]})
        assert 'unrestricted_stock' in result
        assert o.bb.get('materials')[0]['data']['materials'][0]['unrestricted_stock'] == '2 (de-DE)'
        assert o.source_evidence[changed.id]['text'] == changed.text
        await o.source_reads.close()
    asyncio.run(scenario())


def test_navigation_guides_are_sparse_but_can_be_requested():
    menu = Observation.capture('heading "Menu"\n[e1] link "Open" -> /source', 'https://synthetic.test')
    assert prompt_guides(menu, ['materials']) == ''
    assert 'status_scope' in prompt_guides(menu, ['materials'], ['materials'])
    assert compact_progress('[x] old values\n[ ] required stock') == '1 completed checks; read_progress for full details.\n[ ] required stock'
    for kind, guide in FIELD_GUIDES.items():
        keys = set(re.findall(r'"([a-z_]+)"\s*:', KINDS[kind]['schema']))
        assert keys.issubset(set(re.findall(r'\b[a-z_]+\b', guide))), (kind, keys)
        assert len(field_guide(kind)) < len(' '.join(KINDS[kind]['schema'].split()))


def test_long_result_is_reversible():
    ex = FusedExplorer.__new__(FusedExplorer)
    ex.last_result, ex.result_views = 'exact −1.234,50 (de-DE)\n' * 200, {}
    assert 'read_result' in ex.result_view()
    assert next(iter(ex.result_views.values())) == ex.last_result


def test_mission_starts_with_less_than_one_minute(monkeypatch):
    calls = []
    async def run(self):
        calls.append(1)
        self.status = 'complete'
        return self
    monkeypatch.setattr(module.FusedExplorer, 'run', run)
    o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {})
    o._remembering = lambda site, mission: mission
    mission = Mission('small', 'read', {}, deadline=time.time()+20, progress=lambda: ('', True))
    asyncio.run(o.run_mission(site(), mission))
    assert calls == [1]


def test_prefetch_is_bounded_selected_and_invalidated(monkeypatch):
    async def scenario():
        s = site()
        s.absolute = lambda url: url
        s.was_seen = lambda url: url.startswith('https://synthetic.test/')
        calls = []
        async def read(site, **kwargs):
            calls.append(kwargs['url'])
            return documents.Document(kwargs['url'], text='Exact current evidence')
        monkeypatch.setattr(documents, 'read_document', read)
        ex = FusedExplorer.__new__(FusedExplorer)
        ex.site, ex.snapshot, ex.prefetched, ex.extractor = s, observation(), {}, None
        ex.mission = Mission('documents', 'read selected files', {}, deadline=time.time()+20)
        async def prefetch(url):
            return await read(s, url=url), ''
        ex._prefetch_file = prefetch
        assert (await ex.prefetch_documents({'urls': ['https://foreign.test/file.pdf']})).startswith('ERROR')
        assert (await ex.prefetch_documents({'urls': ['https://synthetic.test/action']})).startswith('ERROR')
        url = 'https://synthetic.test/file.pdf'
        assert 'started' in await ex.prefetch_documents({'urls': [url]})
        await asyncio.sleep(0)
        assert 'Exact current evidence' in await ex._read_document({'url': url})
        assert calls == [url, url]  # no strong validator: freshness requires a new read
        await ex.prefetch_documents({'urls': [url]})
        await ex.clear_prefetch()
        assert not ex.prefetched
    asyncio.run(scenario())


def test_form_reuse_matches_unique_current_identity_and_keeps_answer_paths():
    async def scenario():
        ex = SubmitExplorer.__new__(SubmitExplorer)
        ex.snapshot = Observation.capture('[e1] textbox "Quantity"', 'https://synthetic.test/form')
        s = site()
        identity = {'url': ex.snapshot.url, 'frame': ex.snapshot.url, 'identity': 'INPUT|number|quantity'}
        async def identify(ref):
            return {**identity, 'node': 'fresh-node'}
        s.field_identity = identify
        ex.site = s
        ex.field_templates = {'handle': (0, [{'path': '/positions/old/quantity', 'format': 'integer', 'identity': identity}])}
        ex.values = lambda: {'positions': {'new': {'quantity': '7'}}}
        applied = []
        async def bind(args):
            applied.append(args)
            return 'readback verified'
        ex.bind_fields = bind
        assert await ex.reuse_fields({'handle': 'handle', 'position': 'new'}) == 'readback verified'
        assert applied[0]['fields'] == [{'ref': 'e1', 'path': '/positions/new/quantity', 'format': 'integer'}]
        ex.snapshot.controls['e2'] = '[e2] textbox "Quantity"'
        assert 'ambiguous' in await ex.reuse_fields({'handle': 'handle', 'position': 'new'})
        assert len(applied) == 1
        s.session_generation += 1
        assert 'session changed' in await ex.reuse_fields({'handle': 'handle', 'position': 'new'})
    asyncio.run(scenario())


def test_short_budget_keeps_positive_research_window(monkeypatch):
    monkeypatch.setattr(module, 'RUN_BUDGET', 60)
    monkeypatch.setattr(module, 'SUBMIT_RESERVE', 420)
    start = time.time()
    o = Orchestrator({'expires_at': (start+1000)*1000}, {}, started=start)
    assert o.evidence_end - start == pytest.approx(48)
    assert o.end - o.evidence_end == pytest.approx(12)


def test_other_table_context_change_prevents_reusing_frozen_context():
    cache = TableBindings()
    text = observation().text + '\nheading Revision context\n| Revision |\n| OLD |'
    original = Observation.capture(text, observation().url)
    cache.remember(original, binding(), ('erp', 0))
    changed = Observation.capture(text.replace('OLD', 'NEW').replace('1.234,50', '9.876,50'), original.url)
    assert not list(cache.candidates(changed, ('erp', 0)))


@pytest.mark.parametrize('etag,status,new_text', [('"version"', 304, 'old'), ('"version"', 200, 'new')])
def test_prefetched_file_revalidates_strong_etag(monkeypatch, etag, status, new_text):
    async def scenario():
        url = 'https://synthetic.test/data.txt'
        requests = []
        class Response:
            headers = {'content-type': 'text/plain'}
            async def body(self):
                return b'new'
        response = Response()
        response.status, response.url = status, url
        async def get(target, **kwargs):
            requests.append((target, kwargs['headers']))
            return response
        s = site(SimpleNamespace(request=SimpleNamespace(get=get)))
        s.absolute = lambda value: value
        ex = FusedExplorer.__new__(FusedExplorer)
        ex.site, ex.snapshot, ex.extractor = s, observation(), None
        ex.mission = Mission('documents', 'read', {}, deadline=time.time()+20)
        async def loaded():
            return documents.Document(url, text='old'), etag
        ex.prefetched = {url: (ex.snapshot.id, 0, asyncio.create_task(loaded()))}
        assert (await ex._read_document({'url': url})).endswith(new_text)
        assert requests == [(url, {'If-None-Match': etag})]
    asyncio.run(scenario())


def test_contract_retries_share_one_monotonic_startup_budget(monkeypatch):
    from rfq import contract
    now, calls = [0.0], []
    monkeypatch.setattr(contract.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(contract.time, 'sleep', lambda seconds: now.__setitem__(0, now[0]+seconds))
    def get(url, token, timeout):
        calls.append(timeout)
        now[0] += timeout
        return 503, 'unavailable'
    monkeypatch.setattr(contract, '_get', get)
    with pytest.raises(SystemExit):
        contract.load_run({'CHALLENGE_RUN_TOKEN': 'synthetic', 'CHALLENGE_API_URL': 'https://synthetic.test', 'RFQ_RUN_BUDGET': '60'})
    assert calls == [6.0]
    assert now[0] == 6.0


def test_layout_reuse_is_explicit_and_never_generalizes_fixed_context():
    original = observation()
    changed = Observation.capture(original.text.replace('MAT-S', 'MAT-T'), 'https://synthetic.test/other-data')
    cache = TableBindings()
    cache.remember(original, binding(), ('erp', 0))
    assert not list(cache.candidates(changed, ('erp', 0)))
    cache.remember(original, {**binding(), 'reuse_scope': 'layout'}, ('erp', 0))
    assert len(list(cache.candidates(changed, ('erp', 0)))) == 1
    other_site = Observation.capture(changed.text, 'https://other.test/other-data')
    assert not list(cache.candidates(other_site, ('erp', 0)))
    frozen = TableBindings()
    frozen.remember(original, {**binding(), 'reuse_scope': 'layout', 'context': {'catalog_group': 'Freely usable stock'}}, ('erp', 0))
    assert not list(frozen.candidates(changed, ('erp', 0)))


def test_untagged_numeric_locale_change_invalidates_layout():
    cache = TableBindings()
    original = observation('1.234,50')
    cache.remember(original, {**binding(), 'reuse_scope': 'layout'}, ('erp', 0))
    changed = Observation.capture(original.text.replace('1.234,50', '1,234.50'), original.url)
    assert not list(cache.candidates(changed, ('erp', 0)))


def test_partial_table_binding_cannot_suppress_full_source_extraction(monkeypatch):
    async def scenario():
        s = site()
        s.absolute, s.was_seen = lambda url: url, lambda url: True
        raw = 'Stocks and prices\n| Material | Stock | Price |\n| MAT-S | 2 (de-DE) | 3,50 EUR |'
        original = Observation.capture(raw.replace('2 (de-DE)', '1 (de-DE)'), 'https://synthetic.test/data')
        async def read(url):
            return raw
        s.read_url = read
        o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {'synthetic': s})
        partial = {**binding(), 'headers': ['Material', 'Stock', 'Price']}
        o.table_bindings.remember(original, partial, o.binding_scope('erp', original))
        calls = []
        async def ask(*args, **kwargs):
            calls.append(1)
            return {'materials': [{'material_number': 'MAT-S', 'unrestricted_stock': '2 (de-DE)'}]}
        monkeypatch.setattr(llm, 'ask_json', ask)
        ex = FusedExplorer(s, Mission('erp', 'collect', {'materials': ''}), o.extract)
        await ex._extract_urls({'kind': 'materials', 'urls': [original.url]})
        assert calls == [1]
        await o.source_reads.close()
    asyncio.run(scenario())


def test_complete_column_mapping_without_source_completeness_preserves_prose(monkeypatch):
    async def scenario():
        s = site()
        s.absolute, s.was_seen = lambda url: url, lambda url: True
        original = observation().text + '\nMaterial status: blocked. Latest condition: 3,50 EUR (de-DE).'
        raw = original.replace('1.234,50 (de-DE)', '2 (de-DE)')
        selected = Observation.capture(original, 'https://synthetic.test/data')
        async def read(url):
            return raw
        s.read_url = read
        o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {'synthetic': s})
        o.table_bindings.remember(selected, {**binding(), 'source_complete': False}, o.binding_scope('erp', selected))
        calls = []
        async def ask(system, user, **kwargs):
            calls.append(user)
            return {'materials': [{'material_number': 'MAT-S', 'status_meaning': 'blocked', 'unrestricted_stock': '2 (de-DE)'}]}
        monkeypatch.setattr(llm, 'ask_json', ask)
        ex = FusedExplorer(s, Mission('erp', 'collect', {'materials': ''}), o.extract)
        await ex._extract_urls({'kind': 'materials', 'urls': [selected.url]})
        assert len(calls) == 1 and 'Material status: blocked' in calls[0]
        assert o.bb.get('materials')[0]['data']['materials'][0]['status_meaning'] == 'blocked'
        await o.source_reads.close()
    asyncio.run(scenario())


def test_mail_body_copy_preserves_exact_numbers_and_rejects_other_frames():
    o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {})
    text = 'Subject source\nBegin approval\nExact −1.234,50 EUR (de-DE).\nEnd approval'
    obs = Observation.capture(text, 'https://synthetic.test/mail')
    fact = {'kind': 'mail_message', 'quote': 'Subject source', 'data': {'subject': 'synthetic'},
            'copy_fields': {'body': {'start': 'Begin approval', 'end': 'End approval'}}}
    o.accept_decision(Decision(obs.id, facts=[fact]), obs, 'mail')
    assert o.bb.get('mail_message')[0]['data']['body'] == text.split('\n', 1)[1]
    other = Observation.capture(text.replace('Begin approval', '=== frame 1: https://synthetic.test/other ===\nBegin approval'), obs.url)
    assert o.accept_decision(Decision(other.id, facts=[fact]), other, 'mail')[0].startswith('ERROR')
    duplicate = Observation.capture(text + '\nEnd approval', obs.url)
    assert o.accept_decision(Decision(duplicate.id, facts=[fact]), duplicate, 'mail')[0].startswith('ERROR')
    assert len(o.bb.get('mail_message')) == 1


def test_ready_source_batch_extracts_while_another_source_is_still_loading(monkeypatch):
    async def scenario():
        s, slow = site(), asyncio.Event()
        s.absolute, s.was_seen = lambda url: url, lambda url: True
        extracted = asyncio.Event()
        async def read(url):
            if url.endswith('slow'):
                await slow.wait()
            return 'Exact selected positions.'
        s.read_url = read
        o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {'synthetic': s})
        async def ask(*args, **kwargs):
            extracted.set()
            return {'positions': [{'position': 'SYN-P', 'quantity': '2'}]}
        monkeypatch.setattr(llm, 'ask_json', ask)
        ex = FusedExplorer(s, Mission('tender', 'read all', {'positions': ''}), o.extract)
        task = asyncio.create_task(ex._extract_urls({'kind': 'positions', 'urls': [
            'https://synthetic.test/one', 'https://synthetic.test/two', 'https://synthetic.test/three',
            'https://synthetic.test/slow']}))
        await asyncio.wait_for(extracted.wait(), .3)
        assert not task.done()
        slow.set()
        await task
        await o.source_reads.close()
    asyncio.run(scenario())


def test_erp_completion_wakes_dependency_scheduler_without_polling(monkeypatch):
    async def scenario():
        o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {})
        o.site_for = lambda role: site()
        o.bb.add('positions', {'positions': [{'position': 'SYN-P', 'requirement_ref': 'SYN-R'}]}, 'source')
        o.bb.add('requirements', {'requirements': [{'ref': 'SYN-R', 'catalog_group': 'SYN-G'}]}, 'source')
        o.done['specification'].set()
        o.done['tender'].set()
        async def run(self, site, mission, attempts=2):
            await asyncio.sleep(0)
            self.status[mission.name] = 'complete'
        monkeypatch.setattr(Orchestrator, 'run_mission', run)
        await asyncio.wait_for(o.erp_phase(), .3)
        assert o.erp_complete == {'SYN-G'}
    asyncio.run(scenario())

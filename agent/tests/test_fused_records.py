"""Counterfactual evidence, stale mappings, and unsupported completion."""
import asyncio
import time
from types import SimpleNamespace
import pytest
from rfq.agent.records import Observation, Decision, table_data, Coverage
from rfq.agent.fused import FusedExplorer
from rfq.agent.explorer import Mission
from rfq.agent.orchestrator import Orchestrator
from rfq.rules.evidence import merge_materials
from rfq import llm


def test_exact_table_binding_preserves_locale_and_rejects_changed_headers():
    obs = Observation.capture('| Material | Price |\n| MAT-A | −1.234,50 EUR (de-DE) |', 'https://synthetic.test/table')
    binding = {'table': 't1', 'headers': ['Material', 'Price'], 'columns': {'material_number': 0, 'raw_price': 1}, 'list_field': 'materials'}
    assert table_data(obs, binding)['materials'][0]['raw_price'] == '−1.234,50 EUR (de-DE)'
    changed = Observation.capture('| Price | Material |\n| 1 | MAT-A |', obs.url)
    with pytest.raises(ValueError, match='headers changed'):
        table_data(changed, binding)
    with pytest.raises(ValueError):
        Decision.parse({'snapshot_id': changed.id}, obs.id, {'click'})


@pytest.mark.parametrize('raw,expected', [('false', False), ('Nein', False), ('0', False), ('true', True), ('Ja', True)])
def test_table_boolean_flags_keep_their_resolver_meaning(raw, expected):
    obs = Observation.capture('| Requirement | Usable stroke |\n| SYN-A | ' + raw + ' |', 'https://synthetic.test/table')
    mapping = {'table': 't1', 'headers': ['Requirement', 'Usable stroke'],
               'columns': {'ref': 0, 'usable_stroke': 1}, 'list_field': 'requirements'}
    assert table_data(obs, mapping)['requirements'][0]['usable_stroke'] is expected
    ambiguous = Observation.capture(obs.text.replace(raw + ' |', 'pending |'), obs.url)
    with pytest.raises(ValueError, match='ambiguous boolean'):
        table_data(ambiguous, mapping)


def test_observation_omits_without_losing_exact_regions():
    text = '[e1] link "records" -> /records\n' + '\n'.join(f'record {i}: 12.4% −9 EUR' for i in range(900))
    obs = Observation.capture(text, 'https://synthetic.test')
    view = obs.view(2000)
    assert 'omitted; read_region' in view and '[e1]' in view
    assert 'record 899: 12.4% −9 EUR' in '\n'.join(obs.regions.values())


def test_material_inspection_does_not_override_master_and_conflict_is_explicit():
    def item(status, scope='material', code='A', verified=False):
        return {'url': '/source/' + status, 'data': {'materials': [{'material_number': 'MAT-A', 'status_meaning': status,
                'status_scope': scope, 'status_code': code, 'status_verified': verified}]}}
    mats = merge_materials([item('active'), item('blocked', 'inspection')])
    assert mats['MAT-A']['status_meaning'] == 'active'
    conflicted = merge_materials([item('active'), item('blocked')])['MAT-A']
    assert conflicted['status_meaning'] is None and conflicted['_status_conflict']
    resolved = merge_materials([item('active'), item('blocked'), item('active', verified=True)])['MAT-A']
    assert resolved['status_meaning'] == 'active' and '_status_conflict' not in resolved


def test_filtered_mail_query_cannot_prove_complete_folder():
    o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {})
    o.brief = {'tender_reference': 'SYN-A', 'authorized_records': [{'topic': 'packaging', 'subject_keyword': 'Verpackung'}]}
    o.bb.add('mail_list', {'folder': 'current', 'query': 'SYN-A Verpackung', 'folders': ['current'], 'messages': [], 'page': 1, 'pages_total': 1}, '/messages')
    o.coverage['mail_folders'] = Coverage('mail_folders', ('current',), True)
    assert not o.mail_progress()[1]
    o.bb.add('mail_list', {'folder': 'current', 'query': 'SYN-A', 'folders': ['current'], 'messages': [], 'page': 1, 'pages_total': 1}, '/messages/all')
    assert o.mail_progress()[1]


def test_fused_finish_requires_explicit_gaps(monkeypatch):
    replies = iter([{'actions': [{'a': 'finish'}]}, {'actions': [{'a': 'finish', 'missing': ['required source is unavailable']}]}])
    async def ask(*args, **kwargs):
        return next(replies)
    monkeypatch.setattr(llm, 'ask_json', ask)
    async def observe(**kwargs):
        return 'No readable evidence.'
    mission = Mission('test', 'collect facts', {}, max_steps=3, deadline=time.time()+30, progress=lambda: ('[ ] required evidence', False))
    ex = FusedExplorer(SimpleNamespace(site_id='synthetic', page=SimpleNamespace(url='https://synthetic.test'), observe=observe), mission, None)
    asyncio.run(ex.run())
    assert ex.status == 'incomplete' and ex.missing == ['required source is unavailable']

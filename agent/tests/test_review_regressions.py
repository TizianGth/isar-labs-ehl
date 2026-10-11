"""Independent synthetic regressions for reviewed inference-fusion failures."""
import asyncio
import copy
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rfq.agent.orchestrator import Orchestrator
from rfq.agent.records import Coverage, Decision, Observation
from rfq.agent.submit import SubmitExplorer
from rfq.rules.evidence import merge_materials
from rfq.rules.resolve import build
from tests.test_rules import CTX, _evidence


def _orch():
    orchestrator = Orchestrator({'expires_at': (time.time() + 1200) * 1000}, {})
    orchestrator.brief = {'tender_reference': 'SYNTHETIC-42', 'scenario_datetime_utc': '2031-07-16T11:00:00Z',
                          'director_localpart': 'director', 'authorized_records': []}
    return orchestrator


def test_unknown_action_preserves_verified_facts():
    source = 'Position 03 quantity 6 Stk'
    snapshot = Observation.capture(source, 'https://synthetic.test/quantity-record')
    reply = {'facts': [{'kind': 'positions', 'quote': source,
                         'data': {'positions': [{'position': '03', 'quantity': '6', 'unit': 'Stk'}]}}],
             'actions': [{'a': 'click', 'ref': 'e1'}, {'a': 'unsupported-finish'}]}
    decision = Decision.parse(reply, snapshot.id, {'click', 'finish'})
    orchestrator = _orch()
    orchestrator.accept_decision(decision, snapshot, 'tender')
    assert decision.actions == [('click', {'ref': 'e1'})]
    assert decision.errors and 'unsupported-finish' in decision.errors[0]
    assert orchestrator.positions()[0]['quantity'] == '6'
    with pytest.raises(ValueError):
        Decision.parse({'snapshot_id': 'other'}, snapshot.id, {'click'})


def test_frame_fact_is_attributed_to_message_source():
    orchestrator = _orch()
    frame_url = 'https://synthetic.test/message-view/approval'
    quote = 'Subject: Rabattfreigabe SYNTHETIC-42'
    snapshot = Observation.capture('Inbox\n=== frame 1: ' + frame_url + ' ===\n' + quote,
                                   'https://synthetic.test/mail-list')
    message = {'subject': 'Rabattfreigabe SYNTHETIC-42', 'sender': 'director@synthetic.example',
               'datetime_utc': '2031-07-15T10:00:00Z', 'reference': 'SYNTHETIC-42',
               'is_draft_or_preview': False, 'topic': 'discount', 'data': {'percent': '3'}}
    orchestrator.bb.add('mail_list', {'folder': 'Inbox', 'query': '', 'folders': ['Inbox'],
                                     'messages': [{'subject': message['subject'], 'url': frame_url}]}, snapshot.url)
    notes = orchestrator.accept_decision(Decision(snapshot.id, facts=[{'kind': 'mail_message', 'data': message,
                                                                       'quote': quote}]), snapshot, 'mail')
    assert not any(note.startswith('ERROR') for note in notes)
    assert orchestrator.bb.get('mail_message')[0]['url'] == frame_url
    assert 'listed but not opened yet' not in orchestrator.mail_progress()[0]


def test_identical_quotes_from_different_frames_are_rejected():
    quote = 'Position 03 quantity 6 Stk'
    snapshot = Observation.capture(quote + '\n=== frame 1: https://synthetic.test/other ===\n' + quote,
                                   'https://synthetic.test/root')
    orchestrator = _orch()
    notes = orchestrator.accept_decision(Decision(snapshot.id, facts=[{
        'kind': 'positions', 'quote': quote, 'data': {'positions': [{'position': '03', 'quantity': '6'}]}}]),
        snapshot, 'tender')
    assert not orchestrator.bb.get('positions')
    assert any('ambiguous' in note for note in notes)


def test_omitted_table_values_have_complete_region_handles():
    rows = '\n'.join(f'| [e{i}] link "Record {i}" -> /record/{i} | 2031-07-{i:02} |' for i in range(1, 8))
    snapshot = Observation.capture('| Record | Effective date |\n' + rows, 'https://synthetic.test/history')
    view = snapshot.view()
    assert '2031-07-03' in view and '2031-07-07' not in view
    marker = next(line for line in view.splitlines() if 'rows 4' in line and 'omitted' in line)
    handles = re.findall(r'\br\d+\b', marker)
    assert handles
    assert '2031-07-07' in '\n'.join(snapshot.regions[handle] for handle in handles)


def test_unknown_mail_query_does_not_prove_unfiltered_coverage():
    orchestrator = _orch()
    orchestrator.bb.add('mail_list', {'folder': 'Inbox', 'folders': ['Inbox'], 'page': 1,
                                     'pages_total': 1, 'messages': []}, 'https://synthetic.test/inbox?view=filtered')
    orchestrator.coverage['mail_folders'] = Coverage('mail_folders', ('Inbox',), True)
    assert not orchestrator.mail_progress()[1]


def test_unknown_query_remains_unknown_through_runtime_fact_storage():
    orchestrator = _orch()
    url = 'https://synthetic.test/inbox?view=filtered'
    snapshot = Observation.capture('Inbox selected records', url)
    orchestrator.accept_decision(Decision(snapshot.id, facts=[{'kind': 'mail_list', 'quote': snapshot.text,
        'data': {'folder': 'Inbox', 'folders': ['Inbox'], 'page': 1, 'pages_total': 1, 'messages': []}}]),
        snapshot, 'mail')
    orchestrator.coverage['mail_folders'] = Coverage('mail_folders', ('Inbox',), True)
    assert not orchestrator.mail_progress()[1]


def test_folder_badge_counts_do_not_prevent_complete_coverage():
    orchestrator = _orch()
    for folder in ('Inbox', 'Archive'):
        orchestrator.bb.add('mail_list', {'folder': folder, 'query': '', 'folders': ['Inbox (2)', 'Archive (8)'],
                                         'page': 1, 'pages_total': 1, 'messages': []},
                            'https://synthetic.test/' + folder.lower())
    notes = orchestrator.accept_decision(Decision('snapshot', coverage=[{'scope': 'mail_folders', 'complete': True,
        'sources': ['Inbox (2)', 'Archive (8)']}]), SimpleNamespace(id='snapshot', url='/', text=''), 'mail')
    assert not any(note.startswith('ERROR') for note in notes)
    assert orchestrator.mail_progress()[1]


def _status(status, verified=False, scope='material'):
    return {'url': 'https://synthetic.test/master/' + status,
            'data': {'materials': [{'material_number': 'SYNTHETIC-MATERIAL', 'status_meaning': status,
                                    'status_code': status.upper(), 'status_verified': verified, 'status_scope': scope}]}}


def test_missing_status_scope_keeps_material_master_status():
    material = merge_materials([_status('active', scope=None)])['SYNTHETIC-MATERIAL']
    assert material['status_meaning'] == 'active'


def test_verified_master_status_survives_later_unverified_conflict():
    material = merge_materials([_status('active'), _status('blocked'), _status('active', True),
                                _status('blocked')])['SYNTHETIC-MATERIAL']
    assert material['status_meaning'] == 'active'
    assert not material.get('_status_conflict')
    assert 'blocked' in material['_statuses']


def test_unknown_status_with_blocked_sibling_still_fills_position():
    evidence = copy.deepcopy(_evidence())
    evidence['materials'].append({'url': 'https://synthetic.test/master-inspection', 'data': {'materials': [
        {'material_number': 'M-1', 'status_meaning': 'blocked', 'status_scope': 'material'}]}})
    result = build(CTX, evidence)
    assert '1.1' not in result.skipped
    position = next(position for position in result.answer['positions'] if position['position'] == '1.1')
    assert position['material_number'] == 'M-1'
    assert any('UNCERTAIN status unknown' in line for line in result.trace)


def test_not_dispatched_submission_can_be_retried_safely(monkeypatch):
    from rfq.agent import submit
    monkeypatch.delenv('RFQ_DRY_RUN', raising=False)
    monkeypatch.setattr(submit, 'validate_review', lambda *args: {
        'blocking': False, 'mismatches': [], 'missing': [], 'verified': []})

    async def scenario():
        site = SimpleNamespace(act=AsyncMock(side_effect=['NOT_DISPATCHED: reference is absent', 'clicked']),
                               full_text=AsyncMock(return_value='Synthetic review'), accept_next_dialog=False)
        explorer = SubmitExplorer(site, SimpleNamespace(name='submit'), None)
        explorer.orchestrator = SimpleNamespace(result=SimpleNamespace(answer={}, skipped=[]), end=time.time() + 600)
        explorer._read = AsyncMock(return_value={})
        explorer._outcome = AsyncMock(return_value={'confirmed': True, 'receipt': 'synthetic-receipt'})
        first = await explorer._submit('e99')
        assert first.startswith('ERROR: NOT_DISPATCHED')
        assert explorer.state == 'not_started' and not explorer.stop
        explorer._outcome.assert_not_awaited()
        second = await explorer._submit('e3')
        assert second.startswith('CONFIRMED')
        assert explorer.state == 'confirmed' and site.act.await_count == 2
    asyncio.run(scenario())

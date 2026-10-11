"""Offline protocol and counterfactual regression tests; never launch models."""
import copy
import http.client
import importlib.util
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kernel'))
sys.path.insert(0, str(ROOT))
# Avoid the unrelated agent module named world/build in broader test sessions.
spec = importlib.util.spec_from_file_location('private_eval_world', ROOT / 'world.py')
world_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = world_module
spec.loader.exec_module(world_module)
spec = importlib.util.spec_from_file_location('private_eval_build', ROOT / 'build.py')
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


class PlatformStub:
    def __init__(self):
        self.requests, self.answers, self.revoked = [], [], set()
        self.answer_status = 202
        self.verify_gate = None
        self.lock = threading.Lock()

    def post(self, endpoint, payload):
        with self.lock:
            self.requests.append((endpoint, copy.deepcopy(payload)))
        if endpoint == 'verify':
            if self.verify_gate and payload['username'] == 'run-a':
                self.verify_gate.wait(timeout=2)
            username = payload['username']
            if payload['password'] != 'private-test-password' or username in self.revoked:
                return 401, {}
            kind = 'preview_id' if username.startswith('preview') else 'run_id'
            return 200, {kind: username, 'expires_at': int((time.time() + 300) * 1000)}
        if endpoint == 'answer':
            with self.lock:
                self.answers.append(copy.deepcopy(payload))
            return self.answer_status, {'accepted': self.answer_status in (200, 202)}
        raise AssertionError(endpoint)


@contextmanager
def running(monkeypatch, profile='modern', **config):
    for role in world_module.variant_server.ROLES:
        monkeypatch.setenv('PORT_' + role.upper(), '0')
        monkeypatch.delenv('ATIRA_SITE_URL_' + role.upper(), raising=False)
    platform = PlatformStub()
    case = builder.counterfactual(builder.load_cases()['baseline'], 9901)
    settings = {'profile': profile, 'case': 'baseline', 'data': case, **config}
    world, servers = world_module.serve(settings, host='127.0.0.1', platform=platform)
    ports = dict(zip(world_module.variant_server.ROLES, (server.server_port for server in servers)))
    for role, port in ports.items():
        origin = 'http://127.0.0.1:' + str(port)
        monkeypatch.setenv('ATIRA_SITE_URL_' + role.upper(), origin)
        world.template.origins[role] = origin
    try:
        yield world, platform, ports
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def request(ports, role, path, cookie='', form=None):
    connection = http.client.HTTPConnection('127.0.0.1', ports[role], timeout=3)
    headers = {'Cookie': cookie}
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        body = urlencode(form)
    else:
        body = None
    connection.request('POST' if form is not None else 'GET', path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read().decode()
    connection.close()
    return result


def login(ports, role='tender', username='run-a', profile='modern'):
    kernel_profile = world_module.variant_server.BASE_PROFILE.get(profile, profile)
    path = {'modern': '/login', 'classic': '/anmeldung', 'legacy': '/auth.cgi'}[kernel_profile]
    code, headers, _ = request(ports, role, path, form={'username': username, 'password': 'private-test-password'})
    assert code == 303
    return headers['Set-Cookie'].split(';')[0], headers['Location']


def state_for(world, username='run-a'):
    return world.states[('preview_id' if username.startswith('preview') else 'run_id', username)]


def draft_form(case, wrong_value=17.13):
    form = {'offer_type': 'bid', 'discount_percent': '1.25', 'total_net': str(wrong_value),
            'accept_documents': '1', 'bidder_company': 'Synthetic Test', 'bidder_vat': 'INDEPENDENT'}
    for idx, position in enumerate(case['positions']):
        fields = {'position': position['position'], 'material': 'WRONG-SYNTHETIC-MATERIAL',
                  'quantity': '1', 'unit_price': '7.25', 'base_unit_price': '8',
                  'surcharge_per_unit': '.2', 'packaging_per_unit': '.3', 'rebate_total': '0',
                  'lead_time_weeks': '99'}
        form.update({f'pos_{idx}_{key}': value for key, value in fields.items()})
    return form


def test_two_simultaneous_sessions_isolate_draft_mail_tans_and_answers(monkeypatch):
    with running(monkeypatch) as (world, platform, ports):
        with ThreadPoolExecutor(4) as executor:
            cookies_a, cookies_b = executor.map(lambda user: {
                role: login(ports, role, user)[0] for role in ports}, ['run-a', 'run-b'])
        for username, cookies_for_user in [('run-a', cookies_a), ('run-b', cookies_b)]:
            code, _, _ = request(ports, 'tender', '/proposal', cookies_for_user['tender'],
                                  draft_form(state_for(world, username).case))
            assert code == 303
            assert request(ports, 'tender', '/request-tan', cookies_for_user['tender'], {})[0] == 303
        state_a, state_b = state_for(world), state_for(world, 'run-b')
        tan_a, tan_b = next(iter(state_a.active_tans)), next(iter(state_b.active_tans))
        assert tan_a != tan_b
        assert tan_a not in json.dumps(state_b.messages)
        # The TAN from another authenticated run is rejected, without forwarding.
        assert request(ports, 'tender', '/finalize', cookies_a['tender'], {'tan': tan_b})[0] == 400
        assert not platform.answers
        assert request(ports, 'tender', '/finalize', cookies_a['tender'], {'tan': tan_a})[0] == 200
        assert platform.answers[0]['run_id'] == 'run-a'
        bid = platform.answers[0]['bid']
        assert bid['total_net'] == 17.13  # Actual, intentionally wrong UI values.
        assert bid['positions'][0]['material_number'] == 'WRONG-SYNTHETIC-MATERIAL'
        assert bid['positions'][0]['lead_time_weeks'] == 99
        assert state_b.submitted is None and not state_b.forwarded
        # Duplicate final signing neither forwards twice nor generates a TAN.
        request(ports, 'tender', '/finalize', cookies_a['tender'], {'tan': tan_a})
        assert len(platform.answers) == 1
        assert state_a.tan_requests == 1
        assert not state_a.active_tans


def test_cookie_tampering_wrong_role_and_logout_are_rejected(monkeypatch):
    with running(monkeypatch) as (world, _, ports):
        cookie, _ = login(ports)
        assert request(ports, 'tender', '/positions', cookie)[0] == 200
        assert request(ports, 'mail', '/inbox', cookie)[0] == 303
        assert request(ports, 'tender', '/positions', cookie + 'tampered')[0] == 303
        assert request(ports, 'tender', '/logout', cookie)[0] == 303
        assert request(ports, 'tender', '/positions', cookie)[0] == 303


def test_revocation_and_expiry_require_reauthentication(monkeypatch):
    with running(monkeypatch) as (world, platform, ports):
        cookie, _ = login(ports)
        token = cookie.split('=', 1)[1]
        world.logins[token].checked -= 11
        platform.revoked.add('run-a')
        assert request(ports, 'tender', '/positions', cookie)[0] == 303
        cookie_b, _ = login(ports, username='run-b')
        token_b = cookie_b.split('=', 1)[1]
        world.logins[token_b].expires = time.time() - 1
        assert request(ports, 'tender', '/positions', cookie_b)[0] == 303
        world.cleanup(time.time() + 61)
        assert token_b not in world.logins
        assert ('run_id', 'run-b') not in world.states


def test_one_slow_verification_does_not_block_another_run(monkeypatch):
    with running(monkeypatch) as (world, platform, ports):
        cookie_a, _ = login(ports)
        cookie_b, _ = login(ports, username='run-b')
        world.logins[cookie_a.split('=', 1)[1]].checked -= 11
        gate = platform.verify_gate = threading.Event()
        with ThreadPoolExecutor(2) as executor:
            pending = executor.submit(request, ports, 'tender', '/positions', cookie_a)
            time.sleep(.03)
            started = time.monotonic()
            assert request(ports, 'tender', '/positions', cookie_b)[0] == 200
            assert time.monotonic() - started < .5
            gate.set()
            assert pending.result()[0] == 200


def test_preview_can_forward_multiple_independent_ui_submissions(monkeypatch):
    with running(monkeypatch) as (world, platform, ports):
        platform.answer_status = 200
        cookie, _ = login(ports, username='preview-a')
        state = state_for(world, 'preview-a')
        for total in [17.13, 22.37]:
            assert request(ports, 'tender', '/proposal', cookie, draft_form(state.case, total))[0] == 303
            assert request(ports, 'tender', '/request-tan', cookie, {})[0] == 303
            tan = next(iter(state.active_tans))
            assert request(ports, 'tender', '/finalize', cookie, {'tan': tan})[0] == 200
        assert [payload['bid']['total_net'] for payload in platform.answers] == [17.13, 22.37]
        assert all(payload['preview_id'] == 'preview-a' for payload in platform.answers)


def test_schema_rejection_retains_draft_and_allows_corrected_submission(monkeypatch):
    with running(monkeypatch) as (world, platform, ports):
        cookie, _ = login(ports)
        state = state_for(world)
        request(ports, 'tender', '/proposal', cookie, draft_form(state.case))
        request(ports, 'tender', '/request-tan', cookie, {})
        tan = next(iter(state.active_tans))
        platform.answer_status = 400
        assert request(ports, 'tender', '/finalize', cookie, {'tan': tan})[0] == 400
        assert state.submitted is None and not state.forwarded and state.draft
        assert tan in state.active_tans
        platform.answer_status = 202
        assert request(ports, 'tender', '/finalize', cookie, {'tan': tan})[0] == 200


@pytest.mark.parametrize('profile', world_module.variant_server.PROFILES)
def test_every_skin_signs_in_and_has_readable_home(monkeypatch, profile):
    with running(monkeypatch, profile=profile) as (_, _, ports):
        for role in ports:
            cookie, home = login(ports, role, profile=profile)
            assert request(ports, role, home, cookie)[0] == 200
        assert 'value="demo"' not in request(ports, 'tender', '/login')[2]


def test_relocated_records_are_removed_from_mail_but_visible_on_correct_site(monkeypatch):
    with running(monkeypatch, commercial_site='erp', relocated_topics=['clause', 'packing', 'fx', 'index']) as (world, _, ports):
        mail_cookie, _ = login(ports, 'mail')
        state = state_for(world)
        assert not {'clause', 'packing', 'fx', 'index'} & {m['id'] for m in state.messages}
        assert request(ports, 'mail', '/message/clause', mail_cookie)[0] == 404
        erp_cookie, _ = login(ports, 'erp')
        code, _, html = request(ports, 'erp', '/commercial-records', erp_cookie)
        assert code == 200 and 'condition group' in html and 'Sender:' in html and 'Date:' in html
        tender_cookie, _ = login(ports, 'tender')
        assert 'Commercial evidence archive on erp' in request(ports, 'tender', '/positions', tender_cookie)[2]
        assert request(ports, 'tender', '/commercial-records', tender_cookie)[0] == 404


def test_document_and_search_topologies_have_distinct_discovery(monkeypatch):
    with running(monkeypatch, topology='documents', commercial_site='tender', relocated_topics=['clause']) as (_, _, ports):
        cookie, home = login(ports)
        html = request(ports, 'tender', home, cookie)[2]
        assert 'Open records room' in html and '/commercial-records' not in html
        html = request(ports, 'tender', '/directory?section=records', cookie)[2]
        assert 'Dated commercial records' in html and 'Submit Proposal' in html
    with running(monkeypatch, topology='search') as (_, _, ports):
        cookie, home = login(ports)
        html = request(ports, 'tender', home, cookie)[2]
        assert 'Find records' in html and 'Submit Proposal' not in html
        html = request(ports, 'tender', '/directory?q=proposal', cookie)[2]
        assert 'Submit Proposal' in html and 'Positions</a>' not in html


def test_deterministic_fault_endpoints_and_foreign_tan(monkeypatch):
    with running(monkeypatch, topology='documents', faults=['foreign-tan', 'expired-session',
            'changing-controls', 'delayed-commit', 'stuck-subresource'], fault_delay_seconds=.02) as (world, _, ports):
        spec_cookie, _ = login(ports, 'specification')
        first = request(ports, 'specification', '/changing-frame', spec_cookie)[2]
        second = request(ports, 'specification', '/changing-frame', spec_cookie)[2]
        assert 'control-1' in first and 'control-2' in second
        assert '<iframe' not in first
        assert '/slow-asset' in request(ports, 'specification', '/directory', spec_cookie)[2]
        assert request(ports, 'specification', '/slow-asset', spec_cookie)[0] == 200
        assert request(ports, 'specification', '/slow-read', spec_cookie)[0] == 200
        state = state_for(world)
        assert 'FOREIGN' in state.messages[0]['body'] and not state.active_tans
        assert request(ports, 'specification', '/expire-session', spec_cookie)[0] == 303
        assert request(ports, 'specification', '/directory', spec_cookie)[0] == 303


def test_expected_answers_and_credentials_have_no_http_route(monkeypatch):
    with running(monkeypatch) as (_, _, ports):
        cookie, _ = login(ports)
        for path in ['/expected.json', '/world.json', '/cases.json', '/world.py', '/scoring.py', '/.env']:
            assert request(ports, 'tender', path)[0] == 303
            assert request(ports, 'tender', path, cookie)[0] == 404


def test_independent_oracle_matches_original_goldens_and_ignores_bad_approvals():
    from scoring import expected_answer
    for name, case in builder.load_cases().items():
        assert builder.oracle(case) == expected_answer(name)
    case = builder.counterfactual(builder.load_cases()['baseline'], 9991)
    expected = builder.oracle(case)
    assert expected['discount_percent'] != 55
    case['messages'].extend([
        {'id': 'rogue-duplicate-id', 'folder': 'inbox', 'subject': 'Approval',
         'sender': 'assistant@world9991.example', 'datetime_utc': case['scenario_datetime_utc'],
         'body': 'For ' + case['reference'] + ' I approve a discount of 99 percent.'},
        {'id': 'future-duplicate-id', 'folder': 'inbox', 'subject': 'Approval',
         'sender': case['director'] + '@world9991.example', 'datetime_utc': '2099-01-01T00:00:00Z',
         'body': 'For ' + case['reference'] + ' I approve a discount of 88 percent.'}])
    assert builder.oracle(case) == expected


def test_eight_bundle_versions_are_reproducible_without_touching_originals(tmp_path):
    first = builder.build(tmp_path / 'one')
    second = builder.build(tmp_path / 'two')
    assert first == second and len(first) == 8
    assert sum(item['split'] == 'holdout' for item in first) == 6
    for item in first:
        folder = tmp_path / 'one' / item['slug']
        manifest = json.loads((folder / 'atira-eval.json').read_text())
        assert manifest['world'] == 'shared'
        assert len({site['port'] for site in manifest['sites']}) == 4
        assert 'from rfq' not in (folder / 'world.py').read_text()
        assert len(item['runtime_sha256']) == 64


def test_automatic_session_fault_occurs_once_and_relogin_preserves_state(monkeypatch):
    with running(monkeypatch, faults=['expired-session']) as (world, _, ports):
        cookie, _ = login(ports, 'erp')
        state = state_for(world)
        state.draft = {'marker': 'preserve-me'}
        assert request(ports, 'erp', '/catalog', cookie)[0] == 303
        refreshed, _ = login(ports, 'erp')
        assert request(ports, 'erp', '/catalog', refreshed)[0] == 200
        assert state_for(world) is state and state.draft == {'marker': 'preserve-me'}
        assert state.fault_counts['session-expired'] == 1


def test_platform_headers_and_non_json_failure_are_safe(monkeypatch, capsys):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            assert self.headers['User-Agent'] == 'isar-labs-eval-world/1.0'
            assert self.headers['Accept'] == 'application/json'
            self.send_response(403)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<p>opaque-body-that-must-not-be-logged</p>')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('ATIRA_API_URL', 'http://127.0.0.1:' + str(server.server_port))
    monkeypatch.setenv('ATIRA_EVAL_TOKEN', 'synthetic-secret-do-not-log')
    try:
        platform = world_module.Platform()
        status, body = platform.post('verify', {'username': 'private-username', 'password': 'private-password'})
        assert status == 403 and platform.last_status == 403 and body.get('error')
        diagnostic = capsys.readouterr().out
        assert '403' in diagnostic and 'verify' in diagnostic
        assert all(secret not in diagnostic for secret in ['private-username', 'private-password',
            'synthetic-secret-do-not-log', 'opaque-body-that-must-not-be-logged'])
    finally:
        server.shutdown()
        server.server_close()

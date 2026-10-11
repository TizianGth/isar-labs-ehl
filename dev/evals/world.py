"""Atira shared-world adapter for the independent synthetic portal kernel.

Only authenticated pages are served. Each platform identity owns a separate
kernel, draft, mailbox and TAN state; evaluator data has no HTTP route.
"""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from html import escape
from http import cookies
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import variant_server


class Platform:
    def __init__(self):
        self.origin = os.environ['ATIRA_API_URL'].rstrip('/')
        self.token = os.environ['ATIRA_EVAL_TOKEN']
        self.local = threading.local()

    @property
    def last_status(self):
        return getattr(self.local, 'status', None)

    def post(self, endpoint, payload):
        request = Request(self.origin + '/v1/eval-worlds/' + endpoint,
                          json.dumps(payload).encode(), method='POST',
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'x-api-key': self.token,
                                   'Content-Type': 'application/json',
                                   'Accept': 'application/json',
                                   'User-Agent': 'isar-labs-eval-world/1.0'})
        try:
            try:
                response = urlopen(request, timeout=8)
            except HTTPError as error:
                response = error
            with response:
                status = response.status
                raw = response.read(1048576)
        except Exception as error:
            self.local.status = 'transport-' + type(error).__name__
            print(json.dumps({'event': 'eval_platform_failure', 'endpoint': endpoint,
                              'status': self.local.status}), flush=True)
            raise
        self.local.status = status
        self.local.category = None
        self.local.error_terms = []
        self.local.reference_status = None
        if status == 401 and endpoint == 'verify':
            # Read-only diagnostic against the official Node-fetch transport.
            # Never replay the answer endpoint after an ambiguous response.
            reference = r'''const input = JSON.parse(await new Promise(resolve => {
                let text=''; process.stdin.on('data', data => text+=data);
                process.stdin.on('end', () => resolve(text)); }));
                const response = await fetch(process.env.ATIRA_API_URL.replace(/\/+$/, '') + '/v1/eval-worlds/verify', {
                    method:'POST', headers:{authorization:'Bearer ' + process.env.ATIRA_EVAL_TOKEN,
                    'content-type':'application/json'}, body:JSON.stringify(input), signal:AbortSignal.timeout(8000)});
                process.stdout.write(JSON.stringify({status:response.status, body:await response.json().catch(()=>({}))}));'''
            try:
                completed = subprocess.run(['node', '--input-type=module', '-e', reference],
                    input=json.dumps(payload), capture_output=True, text=True, timeout=10, check=True)
                result = json.loads(completed.stdout)
                self.local.reference_status = result['status']
                if result['status'] == 200:
                    status, raw = 200, json.dumps(result['body']).encode()
                    self.local.status = 200
            except Exception:
                self.local.reference_status = 'transport-failure'
        try:
            body = json.loads(raw or b'{}')
            valid_json = isinstance(body, dict)
        except (ValueError, UnicodeError):
            body, valid_json = {}, False
        if not valid_json:
            body = {'error': 'Platform returned a non-object JSON or non-JSON response'}
        if status == 401:
            error = body.get('error', {})
            message = str(error.get('message', '')) if isinstance(error, dict) else str(error)
            lowered = message.casefold()
            self.local.error_terms = [term for term in ['token', 'bearer', 'world', 'eval', 'credential',
                'login', 'site', 'expired', 'version', 'preview', 'run', 'secret', 'authentication'] if term in lowered]
            self.local.category = ('world-token' if any(term in lowered for term in ('token', 'bearer', 'world')) else
                                   'login-credentials' if any(word in lowered for word in ('credential', 'password', 'username', 'login')) else
                                   'unknown-authentication')
        if status not in (200, 202) or not valid_json:
            # Never log request payloads, response bodies, credentials or bearer.
            print(json.dumps({'event': 'eval_platform_failure', 'endpoint': endpoint,
                              'status': status, 'valid_json_object': valid_json,
                              'category': self.local.category, 'error_terms': self.local.error_terms,
                              'node_reference_status': self.local.reference_status}), flush=True)
        return status, body


@dataclass
class Login:
    identity: dict
    username: str
    password: str
    site: str
    checked: float
    expires: float
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class World:
    def __init__(self, config, platform=None):
        self.config, self.platform = config, platform or Platform()
        self.secret = secrets.token_bytes(32)
        self.registry_lock = threading.RLock()
        self.local = threading.local()
        self.states, self.logins = {}, {}
        self.last_cleanup = 0
        self.profile = config['profile']
        self.template = self._new_state()

    def _new_state(self):
        state = variant_server.PortalCluster(self.config['case'], self.profile)
        if self.config.get('data'):
            state.case = json.loads(json.dumps(self.config['data']))
            state.messages = json.loads(json.dumps(state.case.get('messages', [])))
        state.origins = {role: os.environ.get('ATIRA_SITE_URL_' + role.upper(), '')
                         for role in variant_server.ROLES}
        relocated = set(self.config.get('relocated_topics', []))
        state.messages = [m for m in state.messages if m.get('id') not in relocated]
        if 'foreign-tan' in self.config.get('faults', []):
            state.messages.insert(0, {
                'id': 'foreign-signature', 'folder': 'inbox',
                'subject': 'Signatur-TAN für ' + state.case['reference'] + '-FOREIGN (Anforderung #1)',
                'sender': 'security@' + state.case['mailbox'].split('@')[-1],
                'datetime_utc': state.case['scenario_datetime_utc'],
                'body': 'Ausschreibung: ' + state.case['reference'] + '-FOREIGN\nAnforderungsnummer: #1\n'
                        'Ihr persönlicher Signatur-Code (TAN) lautet: TAN-FOREIGN-INVALID'})
        state.forwarded, state.fault_counts = False, {}
        return state

    def __getattr__(self, name):
        # The kernel closes over this proxy. Request threads select their own
        # state; unauthenticated login rendering uses only the template.
        return getattr(getattr(self.local, 'state', self.template), name)

    def __setattr__(self, name, value):
        if name in {'draft', 'submitted', 'receipt', 'tan_requests'}:
            setattr(self.local.state, name, value)
        else:
            object.__setattr__(self, name, value)

    def sign(self, nonce):
        signature = hmac.new(self.secret, nonce.encode(), hashlib.sha256).hexdigest()
        return nonce + '.' + signature

    @staticmethod
    def identity_key(identity):
        # Namespace previews and runs even if their opaque identifiers collide.
        kind = 'run_id' if identity.get('run_id') else 'preview_id'
        return kind, identity[kind]

    @staticmethod
    def valid_identity(identity):
        return (isinstance(identity, dict) and
                bool(identity.get('run_id')) != bool(identity.get('preview_id')) and
                isinstance(identity.get('expires_at'), (int, float)) and
                identity['expires_at'] / 1000 > time.time())

    def cleanup(self, now):
        # Amortized, once per minute; never retain expired evaluator credentials.
        if now - self.last_cleanup < 60:
            return
        self.last_cleanup = now
        for token, login in list(self.logins.items()):
            if login.expires <= now:
                self.logins.pop(token, None)
        live = {self.identity_key(login.identity) for login in self.logins.values()}
        for key in list(self.states):
            if key not in live:
                self.states.pop(key, None)

    def login(self, role, username, password):
        status, identity = self.platform.post('verify', {'site': role, 'username': username, 'password': password})
        if status != 200 or not self.valid_identity(identity):
            return None
        now = time.time()
        login = Login(identity, username, password, role, now, identity['expires_at'] / 1000)
        signed = self.sign(secrets.token_urlsafe(24))
        key = self.identity_key(identity)
        with self.registry_lock:
            self.cleanup(now)
            if key not in self.states:
                self.states[key] = self._new_state()
            self.logins[signed] = login
            self.states[key].sessions[role].add(signed)
        return signed

    def select(self, role, cookie):
        parsed = cookies.SimpleCookie()
        try:
            parsed.load(cookie or '')
            token = parsed['rfq_session_' + role].value
            nonce, signature = token.rsplit('.', 1)
        except (KeyError, ValueError, cookies.CookieError):
            return None
        if not hmac.compare_digest(self.sign(nonce), token):
            return None
        with self.registry_lock:
            self.cleanup(time.time())
            login = self.logins.get(token)
            if login is None or login.site != role or login.expires <= time.time():
                return None
            state = self.states.get(self.identity_key(login.identity))
            if state is None or token not in state.sessions[role]:
                return None
        # Verification can block for eight seconds. It must not hold the shared
        # registry lock and delay every unrelated run on this permanent VM.
        with login.lock:
            if time.time() - login.checked >= 10:
                try:
                    status, identity = self.platform.post('verify', {
                        'site': role, 'username': login.username, 'password': login.password})
                except Exception:
                    return None
                login.checked = time.time()
                if status != 200 or not self.valid_identity(identity) or \
                        self.identity_key(identity) != self.identity_key(login.identity):
                    with self.registry_lock:
                        self.logins.pop(token, None)
                    return None
                login.expires = identity['expires_at'] / 1000
        self.local.state, self.local.login = state, login
        return login

    def forward(self):
        state, login = self.local.state, self.local.login
        with state.lock:
            if state.forwarded and login.identity.get('run_id'):
                return 409, {}
            payload = {k: login.identity[k] for k in ('run_id', 'preview_id') if login.identity.get(k)}
            payload['bid'] = state.submitted
            status, body = self.platform.post('answer', payload)
            if status in (200, 202):
                state.forwarded = True
                state.active_tans.clear()
            else:
                state.submitted, state.receipt = None, None
            return status, body


def make_handler(world, role):
    base = variant_server.make_variant_handler(world, role)

    class Handler(base):
        def _get_nav_links(self):
            if world.config.get('topology'):
                return [('Records directory', '/directory'), ('Sign out', '/logout')]
            links = super()._get_nav_links()
            if role == world.config.get('commercial_site'):
                links += [('Dated commercial records', '/commercial-records')]
            elif world.config.get('commercial_site') and role in {'tender', 'mail'}:
                target = world.origins[world.config['commercial_site']] + '/commercial-records'
                links += [('Commercial evidence archive on ' + world.config['commercial_site'], target)]
            return links

        def _home_path(self):
            return '/directory' if world.config.get('topology') else super()._home_path()

        def _directory(self):
            resources = base._get_nav_links(self)
            if role == world.config.get('commercial_site'):
                resources += [('Dated commercial records', '/commercial-records')]
            topology = world.config.get('topology')
            query = parse_qs(urlparse(self.path).query)
            if topology == 'search':
                words = query.get('q', [''])[0].casefold().split()
                form = '<form><label>Find records <input name="q"></label><button>Search</button></form>'
                matches = [(label, url) for label, url in resources if words and all(
                    word in (label + ' ' + self.case['reference']).casefold() for word in words)]
                body = form + '<p>Search by document type or tender reference. Results:</p>'
            else:
                matches = resources if query.get('section') else []
                body = ('<h2>Document rooms</h2><a href="/directory?section=records">Open records room</a>'
                        '<p>Commercial evidence is also filed in the tender records room.</p>')
            body += ''.join('<p><a href="' + escape(url, quote=True) + '">' + escape(label) + '</a></p>'
                            for label, url in matches if 'logout' not in url)
            if 'delayed-commit' in world.config.get('faults', []):
                body += '<p><a href="/slow-read">Legacy viewer (alternate: records room)</a></p>'
            if role != world.config.get('commercial_site') and world.config.get('commercial_site'):
                body += '<p>Commercial evidence is filed in <a href="' + escape(
                    world.origins[world.config['commercial_site']] + '/commercial-records', quote=True) + '">the ' + escape(world.config['commercial_site']) + ' archive</a>.</p>'
            if 'stuck-subresource' in world.config.get('faults', []):
                body += '<img src="/slow-asset" alt="Decorative loading badge">'
            return self._send_html(200, '<h1>' + escape(self.case['reference']) + '</h1>' + body)

        def _render_login(self, error=None):
            return super()._render_login(error).replace('value="demo"', 'value=""').replace(
                'value="synthetic-only"', 'value=""')

        def _is_authenticated(self):
            return world.select(role, self.headers.get('Cookie')) is not None

        def _redirect(self, location, status=303, set_cookies=None):
            state = getattr(world.local, 'state', None)
            if role == 'tender' and state and state.submitted and not state.forwarded:
                try:
                    code, result = world.forward()
                except Exception:
                    state.submitted, state.receipt = None, None
                    return self._send_html(503, '<h2>Submission service unavailable; retry the review step.</h2>')
                if code not in (200, 202):
                    return self._send_html(code, '<h2>Bid rejected</h2><pre>' + escape(json.dumps(result)) + '</pre>')
                # A successful answer ends a run immediately: return the receipt
                # in this response rather than redirecting to an expired login.
                return self._send_html(200, '<h2>Bid submitted and received</h2><p>Receipt: ' +
                                       escape(state.receipt) + '</p>')
            return super()._redirect(location, status, set_cookies)

        def do_POST(self):
            world.local.state = world.template
            world.local.login = None
            path = urlparse(variant_server.decode_url(self.path, world.profile, role)).path
            if path in {'/login', '/anmeldung', '/auth.cgi'}:
                try:
                    size = int(self.headers.get('Content-Length', 0))
                except ValueError:
                    return self._send_html(400, '<h2>Invalid form length</h2>')
                if size < 0 or size > 65536:
                    return self._send_html(413, '<h2>Form too large</h2>')
                form = parse_qs(self.rfile.read(size).decode(errors='replace'), keep_blank_values=True)
                try:
                    token = world.login(role, form.get('username', [''])[0], form.get('password', [''])[0])
                except Exception:
                    token = None
                if not token:
                    upstream = getattr(world.platform, 'last_status', None)
                    diagnostic = ' (upstream status ' + str(upstream) + ')' if upstream is not None else ''
                    category = getattr(getattr(world.platform, 'local', None), 'category', None)
                    if category:
                        diagnostic += ' [' + category + ']'
                        platform_local = getattr(world.platform, 'local', None)
                        terms = getattr(platform_local, 'error_terms', [])
                        diagnostic += ' [terms:' + ','.join(terms) + ']'
                        reference = getattr(platform_local, 'reference_status', None)
                        if reference is not None:
                            diagnostic += ' [node-status:' + str(reference) + ']'
                    return self._send_html(200, self._render_login('Invalid or expired credentials' + diagnostic))
                world.select(role, 'rfq_session_' + role + '=' + token)
                secure = '; Secure' if world.origins.get(role, '').startswith('https:') else ''
                return self._redirect(self._home_path(), set_cookies=[
                    'rfq_session_' + role + '=' + token + '; Path=/; HttpOnly; SameSite=Lax' + secure])
            if not self._is_authenticated():
                return self._redirect(self._login_url())
            try:
                size = int(self.headers.get('Content-Length', 0))
            except ValueError:
                return self._send_html(400, '<h2>Invalid form length</h2>')
            if size < 0 or size > 262144:
                return self._send_html(413, '<h2>Form too large</h2>')
            state, login = world.local.state, world.local.login
            proposal_paths = {'/angebot-einreichen', '/proposal', '/portal/rfq_bid.html'}
            if role == 'tender' and path in proposal_paths and login.identity.get('preview_id'):
                with state.lock:
                    state.submitted, state.receipt, state.forwarded = None, None, False
            return super().do_POST()

        def do_GET(self):
            world.local.state = world.template
            world.local.login = None
            path = urlparse(variant_server.decode_url(self.path, world.profile, role)).path
            if path not in {'/login', '/anmeldung', '/auth.cgi'} and not self._is_authenticated():
                return self._redirect(self._login_url())
            if 'expired-session' in world.config.get('faults', []) and path == '/expire-session':
                state = world.local.state
                with state.lock:
                    state.sessions[role].clear()
                return self._redirect(self._login_url(), set_cookies=[
                    'rfq_session_' + role + '=; Path=/; Max-Age=0; HttpOnly'])
            if 'expired-session' in world.config.get('faults', []) and role == 'erp' and path in {
                    '/catalog', '/katalog', '/erp_app/find.html'}:
                state = world.local.state
                with state.lock:
                    if not state.fault_counts.get('session-expired'):
                        state.fault_counts['session-expired'] = 1
                        state.sessions[role].clear()
                        return self._redirect(self._login_url(), set_cookies=[
                            'rfq_session_' + role + '=; Path=/; Max-Age=0; HttpOnly'])
            if path == '/directory' and world.config.get('topology'):
                return self._directory()
            if path == '/commercial-records' and role == world.config.get('commercial_site'):
                records = [m for m in self.case['messages'] if m['id'] in world.config.get('relocated_topics', [])]
                body = ''.join('<article><h2>' + escape(m['subject']) + '</h2><p>Sender: ' +
                               escape(m['sender']) + '</p><p>Date: ' + escape(m['datetime_utc']) +
                               '</p><p>' + escape(m['body']) + '</p></article>' for m in records)
                return self._send_html(200, '<h1>Commercial records</h1>' + body)
            if 'stuck-subresource' in world.config.get('faults', []) and path == '/slow-asset':
                # The containing document is readable and actionable immediately.
                time.sleep(world.config.get('fault_delay_seconds', 20))
                return self._send_html(200, '')
            if 'changing-controls' in world.config.get('faults', []) and path == '/changing-frame':
                with world.lock:
                    count = world.fault_counts.get('frame', 0) + 1
                    world.fault_counts['frame'] = count
                return self._send_html(200, '<h2>Archived technical viewer</h2>'
                    '<button id="control-' + str(count) + '" onclick="location.reload()">Refresh viewer</button>'
                    '<a href="' + escape(base._get_nav_links(self)[0][1], quote=True) + '">Open stable records</a>'
                    '<script>setTimeout(() => {const old = document.querySelector("button");'
                    'const fresh = old.cloneNode(true); fresh.id += "-replacement"; old.replaceWith(fresh)}, 75)</script>')
            if 'delayed-commit' in world.config.get('faults', []) and path == '/slow-read':
                time.sleep(world.config.get('fault_delay_seconds', 20))
                return self._send_html(200, '<p>Use the alternate document link in the directory.</p>')
            return super().do_GET()

        def _send_html(self, status, content):
            faults = world.config.get('faults', [])
            authenticated = getattr(world.local, 'login', None) is not None
            if authenticated and role == 'specification' and 'changing-controls' in faults:
                current = urlparse(variant_server.decode_url(self.path, world.profile, role)).path
                if current != '/changing-frame':
                    content += '<iframe title="Archived viewer with replaceable controls" src="/changing-frame"></iframe>'
            if authenticated and 'expired-session' in faults and urlparse(self.path).path not in {'/expire-session'}:
                content += '<p><a href="/expire-session">Reconnect expired legacy session</a></p>'
            return super()._send_html(status, content)

    return Handler


def serve(config, host='0.0.0.0', platform=None):
    world, servers = World(config, platform), []
    try:
        for i, role in enumerate(variant_server.ROLES):
            port = int(os.environ.get('PORT_' + role.upper(), str(3100 + i)))
            server = ThreadingHTTPServer((host, port), make_handler(world, role))
            server.daemon_threads = True
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
    except BaseException:
        for server in servers:
            server.shutdown()
            server.server_close()
        raise
    return world, servers


if __name__ == '__main__':
    world, servers = serve(json.loads(Path('world.json').read_text()))
    print(json.dumps({'ready': True, 'sites': list(variant_server.ROLES)}), flush=True)
    try:
        threading.Event().wait()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()

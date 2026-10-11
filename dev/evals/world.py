"""Atira shared-world adapter for the independent synthetic portal kernel.

Only authenticated pages are served. Each platform identity owns a separate
kernel, draft, mailbox and TAN state; evaluator data has no HTTP route.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
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

    def post(self, endpoint, payload):
        request = Request(self.origin + '/v1/eval-worlds/' + endpoint,
                          json.dumps(payload).encode(), method='POST',
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=8) as response:
                return response.status, json.loads(response.read() or b'{}')
        except HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')


@dataclass
class Login:
    identity: dict
    username: str
    password: str
    site: str
    checked: float
    expires: float


class World:
    def __init__(self, config, platform=None):
        self.config, self.platform = config, platform or Platform()
        self.secret = secrets.token_bytes(32)
        self.registry_lock = threading.RLock()
        self.local = threading.local()
        self.states, self.logins = {}, {}
        self.profile = config['profile']
        self.template = self._new_state()

    def _new_state(self):
        state = variant_server.PortalCluster(self.config['case'], self.profile)
        if self.config.get('data'):
            state.case = json.loads(json.dumps(self.config['data']))
            state.messages = json.loads(json.dumps(state.case.get('messages', [])))
        state.origins = {role: os.environ.get('ATIRA_SITE_URL_' + role.upper(), '')
                         for role in variant_server.ROLES}
        state.forwarded = False
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

    def login(self, role, username, password):
        status, identity = self.platform.post('verify', {'site': role, 'username': username, 'password': password})
        if status != 200 or not any(identity.get(k) for k in ('run_id', 'preview_id')):
            return None
        now = time.time()
        login = Login(identity, username, password, role, now, identity['expires_at'] / 1000)
        nonce = secrets.token_urlsafe(24)
        signed = self.sign(nonce)
        key = identity.get('run_id') or identity['preview_id']
        with self.registry_lock:
            self.logins[signed] = login
            state = self.states.setdefault(key, self._new_state())
            state.sessions[role].add(signed)
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
            login = self.logins.get(token)
            if login is None or login.site != role or login.expires <= time.time():
                return None
            if time.time() - login.checked >= 10:
                status, identity = self.platform.post('verify', {
                    'site': role, 'username': login.username, 'password': login.password})
                login.checked = time.time()
                if status != 200 or identity.get('run_id', identity.get('preview_id')) != \
                        login.identity.get('run_id', login.identity.get('preview_id')):
                    self.logins.pop(token, None)
                    return None
                login.expires = identity['expires_at'] / 1000
            key = login.identity.get('run_id') or login.identity['preview_id']
            self.local.state, self.local.login = self.states[key], login
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
            return super()._get_nav_links()

        def _home_path(self):
            return '/directory' if world.config.get('topology') else super()._home_path()

        def _directory(self):
            resources = base._get_nav_links(self)
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
            if role == 'tender' and topology == 'documents' and query.get('section'):
                body += '<p><a href="/commercial-records">Dated commercial records</a></p>'
            if world.config.get('fault') == 'delayed-commit':
                body += '<p><a href="/slow-read">Legacy viewer (alternate: records room)</a></p>'
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
            observed = self.path
            path = urlparse(variant_server.decode_url(observed, world.profile, role)).path
            if path in {'/login', '/anmeldung', '/auth.cgi'}:
                size = int(self.headers.get('Content-Length', 0))
                if size > 65536:
                    return self._send_html(413, '<h2>Form too large</h2>')
                form = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True)
                try:
                    token = world.login(role, form.get('username', [''])[0], form.get('password', [''])[0])
                except Exception:
                    token = None
                if not token:
                    return self._send_html(200, self._render_login('Invalid or expired credentials'))
                world.select(role, 'rfq_session_' + role + '=' + token)
                secure = '; Secure' if world.origins.get(role, '').startswith('https:') else ''
                return self._redirect(self._home_path(), set_cookies=[
                    'rfq_session_' + role + '=' + token + '; Path=/; HttpOnly; SameSite=Lax' + secure])
            if not self._is_authenticated():
                return self._redirect(self._login_url())
            return super().do_POST()

        def do_GET(self):
            world.local.state = world.template
            path = urlparse(variant_server.decode_url(self.path, world.profile, role)).path
            if path not in {'/login', '/anmeldung', '/auth.cgi'} and not self._is_authenticated():
                return self._redirect(self._login_url())
            if path == '/directory' and world.config.get('topology'):
                return self._directory()
            if path == '/commercial-records' and role == 'tender':
                records = [m for m in self.case['messages'] if m['id'] in {'clause', 'packing', 'fx', 'index'}]
                body = ''.join('<article><h2>' + escape(m['subject']) + '</h2><p>Sender: ' +
                               escape(m['sender']) + '</p><p>Date: ' + escape(m['datetime_utc']) +
                               '</p><p>' + escape(m['body']) + '</p></article>' for m in records)
                return self._send_html(200, '<h1>Commercial records</h1>' + body)
            if world.config.get('fault') == 'delayed-commit' and path == '/slow-read':
                time.sleep(20)
                return self._send_html(200, '<p>Use the alternate document link in the directory.</p>')
            return super().do_GET()

    return Handler


def serve(config, host='0.0.0.0', platform=None):
    world, servers = World(config, platform), []
    for i, role in enumerate(variant_server.ROLES):
        port = int(os.environ.get('PORT_' + role.upper(), str(3100 + i)))
        server = ThreadingHTTPServer((host, port), make_handler(world, role))
        server.daemon_threads = True
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
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

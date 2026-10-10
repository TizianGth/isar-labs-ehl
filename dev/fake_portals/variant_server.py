"""Isolated UI variants around the original four-portal business kernel.

There is no answer/scorer import. Opaque routes remove familiar path hints;
links and redirects remain discoverable in the rendered UI. Each cluster owns
four independent loopback servers and fresh authentication/submission state.
"""
import argparse
import base64
import copy
import importlib
import json
import re
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit

import server

PROFILES = ('classic', 'modern', 'legacy', 'workbench', 'scanned', 'busy-office')
BASE_PROFILE = {'workbench': 'modern', 'scanned': 'modern', 'busy-office': 'legacy'}
MODULES = {'workbench': 'profile_workbench', 'scanned': 'profile_scanned',
           'busy-office': 'profile_busy_office'}
ROLES = ('tender', 'specification', 'erp', 'mail')


def load_cases():
    # extra_cases contains only invented input facts; server never reads goldens.
    try:
        from extra_cases import load_cases as extra_load
    except ModuleNotFoundError as exc:
        if exc.name != 'extra_cases':
            raise
        return server.load_fixture_cases()
    return extra_load()


def route_url(location, profile, role):
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/'):
        return location
    prefix = f'/ui/{profile}/{role}/'
    if parsed.path.startswith(prefix):
        return location
    encoded = base64.urlsafe_b64encode(parsed.path.encode()).decode().rstrip('=')
    return urlunsplit(('', '', prefix + encoded, parsed.query, parsed.fragment))


def decode_url(location, profile, role):
    parsed = urlsplit(location)
    prefix = f'/ui/{profile}/{role}/'
    if not parsed.path.startswith(prefix):
        return location
    encoded = parsed.path[len(prefix):]
    if not re.fullmatch(r'[A-Za-z0-9_-]+', encoded):
        return '/__invalid_variant_route__'
    try:
        path = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_',
                                validate=True).decode()
    except (ValueError, UnicodeError):
        return '/__invalid_variant_route__'
    if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
        return '/__invalid_variant_route__'
    return urlunsplit(('', '', path, parsed.query, parsed.fragment))


def rewrite_html(html, profile, role):
    def replace(match):
        return match[1] + match[2] + route_url(match[3], profile, role) + match[2]
    return re.sub(r'((?:href|action|src)\s*=\s*)([\"\'])(.*?)\2', replace, html,
                  flags=re.IGNORECASE)


def make_variant_handler(cluster, role):
    base = server.make_handler_class(cluster, role)
    kernel = BASE_PROFILE.get(cluster.profile, cluster.profile)
    module = importlib.import_module(MODULES[cluster.profile]) if cluster.profile in MODULES else None

    class Handler(base):
        @property
        def profile(self):
            return kernel

        def _send_html(self, status, content):
            if module is not None:
                content = module.transform_html(rewrite_html(content, cluster.profile, role), role)
            return super()._send_html(status, content)

        def _redirect(self, location, status=303, set_cookies=None):
            if module is not None:
                location = route_url(location, cluster.profile, role)
            return super()._redirect(location, status, set_cookies)

        def _serve(self, method):
            observed = self.path
            if module is not None:
                self.path = decode_url(self.path, cluster.profile, role)
            try:
                return getattr(super(), method)()
            finally:
                self.path = observed

        def do_GET(self):
            return self._serve('do_GET')

        def do_POST(self):
            return self._serve('do_POST')

    return Handler


class PortalCluster(server.PortalCluster):
    def __init__(self, case_name='baseline', profile='classic', base_port=0):
        if profile not in PROFILES:
            raise ValueError(f'Unknown profile: {profile}')
        cases = load_cases()
        if case_name not in cases:
            raise ValueError(f'Unknown case: {case_name}')
        super().__init__('baseline', BASE_PROFILE.get(profile, profile), base_port)
        self.case_name, self.profile = case_name, profile
        self.case = copy.deepcopy(cases[case_name])
        self.messages = copy.deepcopy(self.case.get('messages', []))

    def start(self):
        with self.lock:
            if self._started or self._stopped:
                raise RuntimeError('Cluster cannot be started twice')
            try:
                for idx, role in enumerate(ROLES):
                    port = self.base_port + idx if self.base_port else 0
                    http = ThreadingHTTPServer(('127.0.0.1', port), make_variant_handler(self, role))
                    http.daemon_threads = True
                    self.servers[role] = http
                    self.origins[role] = f'http://127.0.0.1:{http.server_port}'
                    thread = threading.Thread(target=http.serve_forever, daemon=True)
                    self.threads[role] = thread
                    thread.start()
                self._started = True
            except BaseException:
                self._started = True
                # Cleanup happens outside the lock so handlers cannot deadlock.
                failure = True
            else:
                failure = False
        if failure:
            self.stop()
            raise RuntimeError('Could not bind all four loopback ports; cluster cleaned up')
        return self


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, default='workbench')
    parser.add_argument('--case', choices=tuple(load_cases()), default='baseline')
    parser.add_argument('--port', type=int, default=0, help='first of four consecutive ports; zero chooses free ports')
    args = parser.parse_args()
    with PortalCluster(args.case, args.profile, args.port) as cluster:
        print(json.dumps({'profile': cluster.profile, 'case': cluster.case_name,
                          'origins': cluster.origins}), flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()

"""Adapter boundaries, extra-case grades and all 30 fresh local clusters."""
import concurrent.futures
import http.cookiejar
import json
import socket
import unittest
import urllib.error
import urllib.request
import urllib.parse

from scoring import evaluate_answer, expected_answer
from variant_server import PortalCluster, PROFILES, decode_url, load_cases, rewrite_html, route_url


class VariantTests(unittest.TestCase):
    def test_opaque_routes_round_trip_query_and_fragment(self):
        for profile in PROFILES[3:]:
            for role in ('tender', 'specification', 'erp', 'mail'):
                path = '/deep/document.cgi?page=2&group=A#current'
                routed = route_url(path, profile, role)
                self.assertNotIn('/deep/document.cgi', routed)
                self.assertEqual(decode_url(routed, profile, role), path)
                self.assertEqual(route_url(routed, profile, role), routed)
        for url in ('https://example.invalid/a', '#tab', 'mailto:test@example.invalid', 'relative'):
            self.assertEqual(route_url(url, 'scanned', 'erp'), url)

    def test_rewrite_preserves_attribute_quotes_html_entities_and_external_links(self):
        html = '<a href="/qa?page=2&amp;view=all">Next</a><form action=\'/sign\'></form><a href="https://other.invalid/">Other</a>'
        rewritten = rewrite_html(html, 'workbench', 'tender')
        self.assertIn('page=2&amp;view=all', rewritten)
        self.assertIn('href="https://other.invalid/"', rewritten)
        self.assertNotIn("action='/sign'", rewritten)
        self.assertEqual(rewrite_html(rewritten, 'workbench', 'tender'), rewritten)

    def test_invalid_encoded_routes_cannot_select_file_or_authority(self):
        for path in ('/ui/scanned/erp/%%%', '/ui/scanned/erp/Ly9ldmls', '/ui/scanned/erp/ZmlsZTovLy9ldGMvcGFzc3dk'):
            self.assertEqual(decode_url(path, 'scanned', 'erp'), '/__invalid_variant_route__')

    def test_new_goldens_score_perfect_and_do_not_hide_threshold_errors(self):
        for name in ('locale-boundary', 'shared-stock'):
            answer = expected_answer(name)
            self.assertTrue(evaluate_answer(answer, name)['perfect'])
            answer['positions'][0]['quantity'] -= 1
            self.assertFalse(evaluate_answer(answer, name)['perfect'])

    def test_partial_bind_failure_closes_previously_bound_servers(self):
        with socket.socket() as blocked:
            blocked.bind(('127.0.0.1', 0))
            blocked.listen()
            port = blocked.getsockname()[1]
            if port <= 1025:
                self.skipTest('Ephemeral port unsuitable for consecutive bind test')
            # Ensure first port is free before exercising failure on second.
            with socket.socket() as first:
                try:
                    first.bind(('127.0.0.1', port - 1))
                except OSError:
                    self.skipTest('Adjacent ephemeral port already in use')
            cluster = PortalCluster('baseline', 'workbench', port - 1)
            with self.assertRaises(RuntimeError):
                cluster.start()
            self.assertFalse(cluster.servers)
            with socket.socket() as released:
                released.bind(('127.0.0.1', port - 1))

    def test_all_profile_case_combinations_have_four_isolated_logins(self):
        def check(pair):
            profile, name = pair
            with PortalCluster(name, profile) as cluster:
                self.assertEqual(len(set(cluster.origins.values())), 4)
                for role, origin in cluster.origins.items():
                    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                    with client.open(origin, timeout=3) as response:
                        body = response.read().decode()
                        login_url = response.url
                        self.assertIn('name="password"', body)
                    credentials = urllib.parse.urlencode({'username': 'demo', 'password': 'synthetic-only'}).encode()
                    with client.open(login_url, credentials, timeout=3) as response:
                        self.assertNotIn('name="password"', response.read().decode())
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        client.open(origin + '/cases.json', timeout=3)
                    # Check after authentication: unauthenticated users are
                    # correctly redirected to login even for unknown paths.
                    self.assertEqual(error.exception.code, 404)
                self.assertIsNone(cluster.snapshot()['submitted'])
                self.assertIsNone(cluster.snapshot()['receipt'])
                self.assertNotIn('total_net', cluster.case)
                self.assertNotIn('expected_answer', json.dumps(cluster.case))
            return profile, name
        pairs = [(profile, name) for profile in PROFILES for name in load_cases()]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(len(list(pool.map(check, pairs))), 30)


if __name__ == '__main__':
    unittest.main()

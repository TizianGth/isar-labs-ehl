"""Independent checks for fixture truthfulness and absence of answer endpoints."""
import unittest
from server import PortalCluster, parse_flexible_decimal
from test_server import HTTPClient


class ServerSafetyTests(unittest.TestCase):
    def test_nonfinite_numbers_are_rejected(self):
        for value in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_flexible_decimal(value)

    def test_no_answer_file_or_debug_endpoints(self):
        with PortalCluster() as cluster:
            for role, origin in cluster.origins.items():
                client = HTTPClient(); client.login(origin, '/anmeldung')
                for path in ('/scoring.py', '/cases.json', '/server.py', '/debug', '/api/answer', '/../../scoring.py'):
                    with self.subTest(role=role, path=path):
                        status, _ = client.get(origin + path)
                        self.assertEqual(status, 404)

    def test_confirmation_without_submission_is_not_success(self):
        with PortalCluster() as cluster:
            client = HTTPClient(); client.login(cluster.origins['tender'], '/anmeldung')
            status, body = client.get(cluster.origins['tender'] + '/bestaetigung')
            self.assertEqual(status, 200)
            self.assertIn('No proposal received', body)
            self.assertNotIn('erfolgreich', body)
            self.assertIsNone(cluster.snapshot()['receipt'])

    def test_cases_are_independent_and_have_distinct_references(self):
        baseline = PortalCluster('baseline'); blocked = PortalCluster('blocked')
        future = PortalCluster('future-withdrawal')
        self.assertEqual(len({c.case['reference'] for c in (baseline, blocked, future)}), 3)
        self.assertTrue(all(m['status_code'] == 'B' for m in blocked.case['materials'][:2]))
        self.assertGreater(future.case['qa'][-1]['datetime_utc'], future.case['scenario_datetime_utc'])
        baseline.case['positions'][0]['quantity'] = '999'
        self.assertNotEqual(blocked.case['positions'][0]['quantity'], '999')


if __name__ == '__main__':
    unittest.main()

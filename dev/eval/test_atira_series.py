"""A failed or missing run can never disappear from the release gate."""
import unittest

from atira_series import stats


class GateTests(unittest.TestCase):
    def rows(self):
        return [{'run_id': f'synthetic-{i}', 'seconds': 280, 'cost_usd': .1,
                 'cost_time_usd_seconds': 28, 'quality_ok': True} for i in range(10)]

    def test_all_ten_required(self):
        self.assertTrue(stats(self.rows(), 10)['release_gate_passed'])
        self.assertFalse(stats(self.rows()[:9], 10)['release_gate_passed'])
        self.assertFalse(stats(self.rows()[:3], 3)['release_gate_passed'])

    def test_failure_remains_in_statistics(self):
        rows = self.rows()
        rows[0].update(quality_ok=False, seconds=900)
        result = stats(rows, 10)
        self.assertFalse(result['release_gate_passed'])
        self.assertEqual(result['mean_seconds'], 342)
        self.assertEqual(result['worst_seconds'], 900)
        self.assertEqual(result['failures'], ['synthetic-0'])

    def test_missing_duration_is_not_omitted(self):
        rows = self.rows()
        rows[0]['seconds'] = None
        result = stats(rows, 10)
        self.assertIsNone(result['mean_seconds'])
        self.assertFalse(result['release_gate_passed'])


if __name__ == '__main__':
    unittest.main()

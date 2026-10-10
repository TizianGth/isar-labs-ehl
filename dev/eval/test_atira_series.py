"""A failed or missing run can never disappear from the release gate."""
import unittest
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import Mock, patch

from atira_series import live_logs, stats


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


class CaptureTests(unittest.TestCase):
    def test_log_client_closes_on_failure_without_cancelling_platform(self):
        process = Mock(returncode=-15)
        process.wait.side_effect = [subprocess.TimeoutExpired('local logs client', 5), None]
        with tempfile.TemporaryDirectory() as folder, patch('atira_series.subprocess.Popen', return_value=process) as start:
            with self.assertRaisesRegex(RuntimeError, 'test failure'):
                with live_logs('run_synthetic', Path(folder)):
                    raise RuntimeError('test failure')
            self.assertEqual(start.call_args.args[0], ['atira', 'logs', 'run_synthetic', '--follow', '--json'])
            process.terminate.assert_called_once()
            self.assertFalse(json.loads((Path(folder) / 'live_log_capture.json').read_text())['complete'])

    def test_finished_capture_is_reported_separately_from_score(self):
        process = Mock(returncode=0)
        with tempfile.TemporaryDirectory() as folder, patch('atira_series.subprocess.Popen', return_value=process):
            with live_logs('run_synthetic', Path(folder)):
                pass
            process.terminate.assert_not_called()
            self.assertTrue(json.loads((Path(folder) / 'live_log_capture.json').read_text())['complete'])


if __name__ == '__main__':
    unittest.main()

"""A failed or missing run can never disappear from the release gate."""
import unittest
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import Mock, patch

from atira_series import accepted_by_platform, live_logs, read_status, require_test_identity, stats


class GateTests(unittest.TestCase):
    def test_unexpected_scored_identity_stops_more_uploads(self):
        require_test_identity({'kind': 'rehearsal', 'submission_id': None})
        for view in ({'kind': 'scored'}, {}, {'kind': 'rehearsal', 'submission_id': 'sub_synthetic'}):
            with self.assertRaisesRegex(RuntimeError, 'stop uploads'):
                require_test_identity(view)

    def test_terminal_platform_statuses_require_done_and_eligible(self):
        for status in ('submitted', 'scored'):
            view = {'phase': 'done', 'status': status, 'score': {'eligible': True, 'quality': 100}}
            self.assertTrue(accepted_by_platform(view))
            self.assertFalse(accepted_by_platform({**view, 'phase': 'running'}))
            self.assertFalse(accepted_by_platform({**view, 'score': {'quality': 100, 'eligible': False}}))
        self.assertFalse(accepted_by_platform({'phase': 'done', 'status': 'failed', 'score': {'eligible': True}}))

    def rows(self):
        return [{'run_id': f'synthetic-{i}', 'seconds': 280, 'cost_usd': .1,
                 'cost_time_usd_seconds': 28, 'quality_ok': True, 'kind': 'rehearsal'} for i in range(10)]

    def test_promoted_run_cannot_pass_test_series_gate(self):
        rows = self.rows()
        rows[0].update(kind='scored', submission_id='sub_synthetic')
        self.assertFalse(stats(rows, 10)['release_gate_passed'])

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
    def test_status_fallback_keeps_score_and_omits_unrequested_secrets(self):
        cli = Mock(returncode=1, stdout='{"error":"transport unavailable"}')
        feed = {'phase': 'done', 'status': 'scored', 'calls': 12, 'access_secret': 'never-save-this'}
        detail = {'run_id': 'run_synthetic', 'kind': 'rehearsal', 'score': {'quality': 100, 'eligible': True}}
        with patch('atira_series.subprocess.run', return_value=cli), patch('atira_series.readonly_api', side_effect=[feed, detail]) as read:
            result = read_status('run_synthetic')
        self.assertTrue(accepted_by_platform(result))
        self.assertNotIn('access_secret', result)
        self.assertEqual([c.args[0] for c in read.call_args_list],
                         ['/v1/runs/run_synthetic/feed?frames=0', '/v1/runs/run_synthetic'])

    def test_status_fallback_rejects_a_mismatched_run(self):
        with patch('atira_series.subprocess.run', return_value=Mock(returncode=1, stdout='{}')):
            with patch('atira_series.readonly_api', side_effect=[{}, {'run_id': 'run_other'}]):
                with self.assertRaisesRegex(ValueError, 'different run'):
                    read_status('run_synthetic')

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

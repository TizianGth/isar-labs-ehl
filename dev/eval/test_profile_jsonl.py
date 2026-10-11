"""CLI chunks, terminal metadata and overlapping timings must not corrupt measurement."""
import json
from pathlib import Path
import tempfile
import unittest

from profile_jsonl import analyze, read_log_text


class ProfileTests(unittest.TestCase):
    def test_chunk_boundaries_and_terminal_status_do_not_drop_stdout(self):
        first = json.dumps({'t': 1, 'event': 'llm_request', 'request_class': 'control', 'request_s': 4}) + '\n'
        second = json.dumps({'t': 2, 'event': 'llm_request', 'request_class': 'extract', 'request_s': 5}) + '\n'
        records = [{'stream': 'stdout', 'text': first[:15]}, {'stream': 'stdout', 'text': first[15:] + second},
                   {'stream': 'stderr', 'text': 'a diagnostic'}, {'run_id': 'run_synthetic', 'status': 'submitted'}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'live_log_chunks.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
            result = analyze(read_log_text(path))
        self.assertEqual(result['records'], 2)
        self.assertEqual(result['invalid_json_records'], 0)
        self.assertEqual(result['first_observed_seconds'], 1)
        self.assertEqual(result['observed_wall_seconds'], 2)
        self.assertEqual(result['inclusive_metrics']['control.request_s']['inclusive_seconds'], 4)
        self.assertEqual(result['inclusive_metrics']['extract.request_s']['inclusive_seconds'], 5)

    def test_tool_truncation_is_reported_as_incomplete_evidence(self):
        result = analyze('[00:01] stdout: {"t":1,"event":"action","result":"cut off')
        self.assertEqual(result['invalid_json_records'], 1)
        self.assertEqual(result['records'], 0)
        self.assertIsNone(result['first_observed_seconds'])


if __name__ == '__main__':
    unittest.main()

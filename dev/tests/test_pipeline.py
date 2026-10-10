import sys
import json
import os
import unittest
import shutil
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from argparse import Namespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline

class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # repo root -> tmp
        p = patch.object(pipeline, 'repo_root', return_value=self.tmp)
        p.start()
        self.addCleanup(p.stop)
        # git info
        p = patch.object(pipeline, '_git_info', return_value=('a' * 40, 'feat/test', True))
        p.start()
        self.addCleanup(p.stop)
        # setup structure
        (self.tmp / 'agent').mkdir()
        (self.tmp / 'agent' / 'agent.json').write_text('{"name": "agent", "run": ["python", "main.py"]}')
        (self.tmp / 'agent' / 'main.py').write_text('print("hi")')
        (self.tmp / 'agent' / 'tests').mkdir()
        (self.tmp / 'agent' / 'tests' / 'test_example.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test_pass(self):\n        pass')
        # oracle
        (self.tmp / 'dev' / 'oracle').mkdir(parents=True)
        (self.tmp / 'dev' / 'oracle' / 'public-1.json').write_text(json.dumps({
            "tender_reference": "TENDER-XYZ",
            "positions": [{"material_number": "MN-42"}]
        }))
        # docs
        (self.tmp / 'docs').mkdir()
        (self.tmp / 'docs' / '03-site-recon.md').write_text('Known key VP-1234.')

    def _run_check(self, skip_tests=False, pkg='agent', paths=['agent.json', 'main.py'], files=2, bytes=100):
        if paths:
            manifest = {'paths': paths, 'files': files, 'bytes': bytes}
        else:
            manifest = {}
        with patch.object(pipeline, '_run_atira') as mock:
            mock.return_value = MagicMock(returncode=0, stdout=json.dumps(manifest))
            args = Namespace(package=pkg, atira='dummy', skip_tests=skip_tests)
            code = pipeline.cmd_check(args)
        return code

    def test_success_skip(self):
        code = self._run_check(skip_tests=True)
        self.assertEqual(code, 0)

    def test_success_actual(self):
        code = self._run_check(skip_tests=False)
        self.assertEqual(code, 0)

    def test_missing_pkg(self):
        args = Namespace(package='none', atira='dummy', skip_tests=True)
        self.assertEqual(pipeline.cmd_check(args), 1)

    def test_missing_tests_dir(self):
        shutil.rmtree(self.tmp / 'agent' / 'tests')
        self.assertEqual(self._run_check(skip_tests=False), 1)

    def test_empty_tests_dir(self):
        for f in (self.tmp / 'agent' / 'tests').iterdir():
            if f.is_file(): f.unlink()
        self.assertEqual(self._run_check(skip_tests=False), 1)

    def test_failing_tests(self):
        (self.tmp / 'agent' / 'tests' / 'test_example.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test_fail(self):\n        self.assertTrue(False)')
        self.assertEqual(self._run_check(skip_tests=False), 1)

    def test_malformed_manifest_array(self):
        self.assertEqual(self._run_check(paths=[1, 2]), 1)

    def test_nonstr_path(self):
        self.assertEqual(self._run_check(paths=['agent.json', 1]), 1)

    def test_bool_metadata(self):
        self.assertEqual(self._run_check(paths=['agent.json'], files=True), 1)

    def test_mismatched_count(self):
        self.assertEqual(self._run_check(paths=['agent.json', 'main.py'], files=1), 1)

    def test_missing_agent_json(self):
        self.assertEqual(self._run_check(paths=['main.py'], files=1), 1)

    def test_unsafe_paths(self):
        for bad in ['', '.', './main.py', '../main.py', '/abs', 'main.py//x']:
            with self.subTest(path=bad):
                self.assertEqual(self._run_check(paths=['agent.json', bad], files=2), 1)

    def test_duplicate_paths(self):
        self.assertEqual(self._run_check(paths=['agent.json', 'agent.json'], files=2), 1)

    def test_symlink_inside(self):
        (self.tmp / 'agent' / 'link').symlink_to('main.py')
        self.assertEqual(self._run_check(paths=['agent.json', 'link'], files=2), 1)

    def test_symlink_outside(self):
        ext = self.tmp / 'ext.txt'
        ext.write_text('x')
        (self.tmp / 'agent' / 'link').symlink_to(ext)
        self.assertEqual(self._run_check(paths=['agent.json', 'link'], files=2), 1)

    def test_blocked_fixtures(self):
        (self.tmp / 'agent' / 'fixtures').mkdir()
        (self.tmp / 'agent' / 'fixtures' / 'f.txt').write_text('x')
        self.assertEqual(self._run_check(paths=['agent.json', 'fixtures/f.txt'], files=2), 1)

    def test_blocked_oracle(self):
        (self.tmp / 'agent' / 'oracle').mkdir()
        (self.tmp / 'agent' / 'oracle' / 'o.txt').write_text('x')
        self.assertEqual(self._run_check(paths=['agent.json', 'oracle/o.txt'], files=2), 1)

    def test_blocked_dev(self):
        (self.tmp / 'agent' / 'dev').mkdir()
        (self.tmp / 'agent' / 'dev' / 'd.txt').write_text('x')
        self.assertEqual(self._run_check(paths=['agent.json', 'dev/d.txt'], files=2), 1)

    def test_leak_known_ref(self):
        (self.tmp / 'agent' / 'main.py').write_text('TENDER-XYZ')
        self.assertEqual(self._run_check(), 1)

    def test_leak_unknown_key(self):
        (self.tmp / 'agent' / 'main.py').write_text('VP-5678')
        self.assertEqual(self._run_check(), 0)

    def test_leak_known_key(self):
        (self.tmp / 'agent' / 'main.py').write_text('VP-1234')
        self.assertEqual(self._run_check(), 1)

    def test_leak_zero(self):
        (self.tmp / 'agent' / 'main.py').write_text('T-0')
        self.assertEqual(self._run_check(), 0)

    def test_missing_oracle(self):
        (self.tmp / 'dev' / 'oracle' / 'public-1.json').unlink()
        self.assertEqual(self._run_check(), 1)

    def test_malformed_oracle(self):
        (self.tmp / 'dev' / 'oracle' / 'public-1.json').write_text('{}')
        self.assertEqual(self._run_check(), 1)

    def test_oracle_no_positions(self):
        (self.tmp / 'dev' / 'oracle' / 'public-1.json').write_text(json.dumps({"tender_reference": "T"}))
        self.assertEqual(self._run_check(), 1)

    def test_record_success(self):
        args = Namespace(run_id='id1', status='ok', quality=None, eligible=None, seconds=None, cost_usd=None, note=None, package='agent')
        code = pipeline.cmd_record(args)
        self.assertEqual(code, 0)
        files = list((self.tmp / 'dev' / 'runs').iterdir())
        self.assertEqual(len(files), 1)
        stat = files[0].stat()
        self.assertEqual(stat.st_mode & 0o777, 0o600)
        rec = json.loads(files[0].read_text())
        self.assertEqual(rec['run_id'], 'id1')
        self.assertEqual(rec['git']['head'], 'a' * 40)

    def test_record_invalid_run_id(self):
        args = Namespace(run_id='bad id', status='ok', quality=None, eligible=None, seconds=None, cost_usd=None, note=None, package=None)
        self.assertEqual(pipeline.cmd_record(args), 1)

    def test_record_metrics_valid(self):
        args = Namespace(run_id='id', status='ok', quality='90.5', eligible=None, seconds='1.2', cost_usd='2.0', note=None, package=None)
        self.assertEqual(pipeline.cmd_record(args), 0)

    def test_record_metrics_invalid(self):
        args = Namespace(run_id='id', status='ok', quality='NaN', eligible=None, seconds=None, cost_usd=None, note=None, package=None)
        self.assertEqual(pipeline.cmd_record(args), 1)
        self.assertFalse((self.tmp / 'dev' / 'runs').exists())

    def test_record_metrics_neg(self):
        args = Namespace(run_id='id', status='ok', quality='-1', eligible=None, seconds=None, cost_usd=None, note=None, package=None)
        self.assertEqual(pipeline.cmd_record(args), 1)

    def test_record_metrics_high(self):
        args = Namespace(run_id='id', status='ok', quality='101', eligible=None, seconds=None, cost_usd=None, note=None, package=None)
        self.assertEqual(pipeline.cmd_record(args), 1)

    def test_record_status_invalid(self):
        args = Namespace(run_id='id', status='  ', quality=None, eligible=None, seconds=None, cost_usd=None, note=None, package=None)
        self.assertEqual(pipeline.cmd_record(args), 1)

    def test_atira_args(self):
        with patch.object(subprocess, 'run') as mock:
            pipeline._run_atira('node', Path('/p'), 1)
            args = mock.call_args[0][0]
            self.assertTrue(args[-1] == '--json')
            self.assertEqual(args[-2], '--dry-run')
            self.assertFalse(mock.call_args[1]['shell'])

    def test_atira_cli_fail(self):
        with patch.object(pipeline, '_run_atira') as mock:
            mock.return_value = MagicMock(returncode=1)
            args = Namespace(package='agent', atira='x', skip_tests=True)
            self.assertEqual(pipeline.cmd_check(args), 1)

    def test_atira_timeout(self):
        with patch.object(pipeline, '_run_atira') as mock:
            mock.side_effect = subprocess.TimeoutExpired('c', 1)
            args = Namespace(package='agent', atira='x', skip_tests=True)
            self.assertEqual(pipeline.cmd_check(args), 1)

if __name__ == '__main__':
    unittest.main()
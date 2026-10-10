from contextlib import redirect_stderr
import sys
import json
import os
import unittest
import shutil
import tempfile
import subprocess
import hashlib
import tarfile
import io
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

    def _run_check(self, skip_tests=False, pkg='agent', paths=['agent.json', 'main.py'], files=2, bytes=100, runner='pytest'):
        if paths:
            manifest = {'paths': paths, 'files': files, 'bytes': bytes}
        else:
            manifest = {}
        with patch.object(pipeline, '_run_atira') as mock:
            mock.return_value = MagicMock(returncode=0, stdout=json.dumps(manifest))
            args = Namespace(package=pkg, atira='dummy', skip_tests=skip_tests, test_runner=runner)
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

    def test_pytest_bare_function_fails(self):

        test_content = (
            'import unittest\n'
            'def test_bare():\n'
            '    assert False\n'
            'class T(unittest.TestCase):\n'
            '    def test_pass(self):\n'
            '        pass\n'
        )
        (self.tmp / 'agent' / 'tests' / 'test_example.py').write_text(test_content)
        code = self._run_check(skip_tests=False, runner='pytest')
        self.assertEqual(code, 1)

    def test_pytest_empty_collection(self):

        (self.tmp / 'agent' / 'tests' / 'test_example.py').write_text('x = 1\n')
        code = self._run_check(skip_tests=False, runner='pytest')
        self.assertEqual(code, 1)

    def test_unittest_zero_tests(self):

        (self.tmp / 'agent' / 'tests' / 'test_example.py').write_text('x = 1\n')
        code = self._run_check(skip_tests=False, runner='unittest')
        self.assertEqual(code, 1)

    def test_missing_pytest(self):
        errors = io.StringIO()
        with patch.object(subprocess, 'run', return_value=MagicMock(returncode=1)) as invoked, redirect_stderr(errors):
            code = pipeline._run_tests(self.tmp / 'agent', 'pytest')
        self.assertEqual(code, 1)
        self.assertIn('uv sync', errors.getvalue())
        invoked.assert_called_once()
        self.assertEqual(invoked.call_args.args[0][1:], ['-m', 'pytest', '--version'])

    def test_included_cache_paths(self):
        for bad in ['__pycache__/x.py', 'rfq/__pycache__/x.pyc', '.pytest_cache/x', '.venv/x', '.git/x', 'node_modules/x', 'main.pyc', 'main.pyo']:
            with self.subTest(path=bad):
                path = self.tmp / 'agent' / bad
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic cache')
                errors = io.StringIO()
                with redirect_stderr(errors):
                    code = self._run_check(paths=['agent.json', bad], files=2, skip_tests=True)
                self.assertEqual(code, 1)
                self.assertIn('BLOCKED paths', errors.getvalue())

    def test_package_changed_during_tests(self):

        manifest1 = {'paths': ['agent.json', 'main.py'], 'files': 2, 'bytes': 100}
        manifest2 = {'paths': ['agent.json', 'main.py', 'extra.py'], 'files': 3, 'bytes': 150}

        def mock_run_tests(*args, **kwargs):

            extra_file = self.tmp / 'agent' / 'extra.py'
            extra_file.write_text('print("extra")')
            return 0

        with patch.object(pipeline, '_run_atira') as mock_atira:
            mock_atira.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps(manifest1)),
                MagicMock(returncode=0, stdout=json.dumps(manifest2)),
            ]

            with patch.object(pipeline, '_run_tests', side_effect=mock_run_tests):
                args = Namespace(package='agent', atira='dummy', skip_tests=False, test_runner='pytest')
                code = pipeline.cmd_check(args)

        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / 'dev' / 'runs').exists())

    def test_successful_receipt_with_hashes_and_private_mode(self):
        code = self._run_check(skip_tests=True)
        self.assertEqual(code, 0)
        files = list((self.tmp / 'dev' / 'runs').iterdir())
        self.assertEqual(len(files), 1)
        stat = files[0].stat()
        self.assertEqual(stat.st_mode & 0o777, 0o600)
        rec = json.loads(files[0].read_text())
        self.assertEqual(rec['schema'], 'dev-preflight-1')
        self.assertEqual(rec['runner'], 'skipped')
        self.assertTrue(rec['skipped'])
        self.assertEqual(rec['package'], 'agent')
        self.assertEqual(rec['compressed_bytes'], 100)
        self.assertEqual(rec['git']['head'], 'a' * 40)
        paths = {e['path'] for e in rec['included']}
        self.assertEqual(paths, {'agent.json', 'main.py'})
        for e in rec['included']:
            self.assertEqual(len(e['sha256']), 64)
            self.assertIsInstance(e['size'], int)

            real_path = self.tmp / 'agent' / e['path']
            real_hash = hashlib.sha256(real_path.read_bytes()).hexdigest()
            self.assertEqual(e['sha256'], real_hash)

    def _make_receipt(self, entries):
        runs = self.tmp / 'dev' / 'runs'
        runs.mkdir(parents=True, exist_ok=True)
        rec = {
            'schema': 'dev-preflight-1',
            'package': 'agent',
            'runner': 'skipped',
            'skipped': True,
            'included': entries,
            'compressed_bytes': 100,
            'git': {'head': 'a' * 40, 'branch': 'feat/test', 'dirty': True},
            'created_at': '2024-01-01T00:00:00+00:00',
        }
        path = runs / 'receipt.json'
        path.write_text(json.dumps(rec))
        return path

    def _make_tar(self, tmpdir, files):

        path = Path(tmpdir) / 'team-run.tar.gz'
        with tarfile.open(str(path), 'w:gz') as tf:
            for name, content in files.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return path, sha

    def _verify(self, receipt_path, run_id='run1', atira='atira'):
        args = Namespace(run_id=run_id, receipt=str(receipt_path), atira=atira)
        return pipeline.cmd_verify_run(args)

    def test_verify_matching_synthetic_tar(self):
        content = b'hello world'
        sha = hashlib.sha256(content).hexdigest()
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content), 'sha256': sha}])

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason=None, relocate=True)

        self.assertEqual(code, 0)
        files = list((self.tmp / 'dev' / 'runs').iterdir())

        self.assertEqual(len(files), 2)

        verified_rec = None
        for f in files:
            rec = json.loads(f.read_text())
            if rec.get('schema') == 'dev-verified-package-1':
                verified_rec = rec
                break

        self.assertIsNotNone(verified_rec)
        self.assertEqual(verified_rec['run_id'], 'run1')
        self.assertEqual(verified_rec['archive_sha256'], comp_sha)
        self.assertEqual(verified_rec['receipt'], 'receipt.json')
        self.assertEqual(verified_rec['receipt_git']['head'], 'a' * 40)

        stat = f.stat()

        for f in files:
            rec = json.loads(f.read_text())
            if rec.get('schema') == 'dev-verified-package-1':
                stat = f.stat()
                break
        self.assertEqual(stat.st_mode & 0o777, 0o600)

    def test_verify_modified_file(self):

            content_receipt = b'hello world'
            sha_receipt = hashlib.sha256(content_receipt).hexdigest()

            content_archive = b'HELLO WORLD'

            receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content_receipt), 'sha256': sha_receipt}])

            with tempfile.TemporaryDirectory() as tmp:
                tar_path, comp_sha = self._make_tar(tmp, {'main.py': content_archive})
                payload = {
                    'id': 'run1',
                    'sha256': comp_sha,
                    'files': [{'path': 'main.py', 'bytes': len(content_archive)}],
                    'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
                }

                code = self._verify_payload(receipt_path, payload, reason='sha256', relocate=True)

            self.assertEqual(code, 1)

            files = list((self.tmp / 'dev' / 'runs').iterdir())
            self.assertEqual(len(files), 1)

    def test_verify_missing_file(self):
        content = b'hello world'
        sha = hashlib.sha256(content).hexdigest()
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content), 'sha256': sha}])

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'other.py': b'x'})
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'other.py', 'bytes': 1}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='missing', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_extra_file(self):
        content = b'hello world'
        sha = hashlib.sha256(content).hexdigest()
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content), 'sha256': sha}])

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content, 'extra.py': b'y'})
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}, {'path': 'extra.py', 'bytes': 1}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='extra', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_archive_checksum_mismatch(self):
        content = b'hello world'
        sha = hashlib.sha256(content).hexdigest()
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content), 'sha256': sha}])

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})

            payload = {
                'id': 'run1',
                'sha256': 'b' * 64,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': 'b' * 64, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='checksum', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_unsafe_path(self):
        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content), 'sha256': sha}])

        with tempfile.TemporaryDirectory() as tmp:
            tar_path = Path(tmp) / 'team-run.tar.gz'
            with tarfile.open(str(tar_path), 'w:gz') as tf:
                info = tarfile.TarInfo(name='../evil.py')
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
            comp_sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': '../evil.py', 'bytes': 1}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='unsafe', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_symlink_member(self):
        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content), 'sha256': sha}])

        with tempfile.TemporaryDirectory() as tmp:
            tar_path = Path(tmp) / 'team-run.tar.gz'
            with tarfile.open(str(tar_path), 'w:gz') as tf:
                info = tarfile.TarInfo(name='link.py')
                info.type = tarfile.SYMTYPE
                info.linkname = 'main.py'
                tf.addfile(info)
            comp_sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'link.py', 'bytes': 0}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='non-regular', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_receipt_outside_runs(self):
        outside = self.tmp / 'outside.json'
        outside.write_text(json.dumps({'schema': 'dev-preflight-1', 'included': [], 'git': {'head': 'a' * 40}}))
        self.assertEqual(self._verify(outside), 1)

    def test_verify_receipt_bad_schema(self):
        receipt_path = self._make_receipt([])
        rec = json.loads(receipt_path.read_text())
        rec['schema'] = 'wrong'
        receipt_path.write_text(json.dumps(rec))
        self.assertEqual(self._verify(receipt_path), 1)

    def test_verify_receipt_bad_hash(self):
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': 1, 'sha256': 'zz'}])
        self.assertEqual(self._verify(receipt_path), 1)

    def test_verify_corrupt_archive(self):
        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content), 'sha256': sha}])

        with tempfile.TemporaryDirectory() as tmp:
            tar_path = Path(tmp) / 'team-run.tar.gz'
            tar_path.write_bytes(b'not a real tar')
            comp_sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': 1}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='read failed', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_cli_failure(self):
        receipt_path = self._make_receipt([])
        with patch.object(subprocess, 'run', return_value=MagicMock(returncode=1, stdout='')):
            code = self._verify(receipt_path)
        self.assertEqual(code, 1)

    def _verify_payload(self, receipt_path, payload, reason=None, relocate=True):
        def fake_cli(argv, **kwargs):
            self.assertEqual(argv[1:3], ['package', 'run1'])
            self.assertEqual(argv[-1], '--json')
            self.assertFalse(kwargs['shell'])
            reply = json.loads(json.dumps(payload))
            if relocate:
                source = Path(reply['saved']['path'])
                target = Path(argv[argv.index('--out') + 1]) / source.name
                shutil.copyfile(source, target)
                reply['saved']['path'] = str(target)
            return MagicMock(returncode=0, stdout=json.dumps(reply))
        errors = io.StringIO()
        with patch.object(subprocess, 'run', side_effect=fake_cli), redirect_stderr(errors):
            code = self._verify(receipt_path)
        if reason:
            self.assertIn(reason, errors.getvalue())
        return code

    def test_package_changed_during_tests_content_mutation(self):

        manifest1 = {'paths': ['agent.json', 'main.py'], 'files': 2, 'bytes': 100}
        manifest2 = {'paths': ['agent.json', 'main.py'], 'files': 2, 'bytes': 150}

        def mock_run_tests(*args, **kwargs):

            main_file = self.tmp / 'agent' / 'main.py'
            main_file.write_text('print("mutated content that is longer")')
            return 0

        with patch.object(pipeline, '_run_atira') as mock_atira:
            mock_atira.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps(manifest1)),
                MagicMock(returncode=0, stdout=json.dumps(manifest2)),
            ]

            with patch.object(pipeline, '_run_tests', side_effect=mock_run_tests):
                args = Namespace(package='agent', atira='dummy', skip_tests=False, test_runner='pytest')
                code = pipeline.cmd_check(args)

        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / 'dev' / 'runs').exists())

    def test_verify_saved_path_escape(self):

        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        receipt_path = self._make_receipt([{'path': 'main.py', 'size': len(content), 'sha256': sha}])

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='escape', relocate=False)

        self.assertEqual(code, 1)

    def test_verify_runs_directory_symlink(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        runs = self.tmp / 'dev' / 'runs'
        runs.symlink_to(outside)
        receipt = self._make_receipt([])
        errors = io.StringIO()
        with patch.object(subprocess, 'run') as invoked, redirect_stderr(errors):
            code = self._verify(receipt)
        self.assertEqual(code, 1)
        self.assertIn('inside repo', errors.getvalue())
        invoked.assert_not_called()

    def test_verify_receipt_empty_list(self):

            receipt_path = self._make_receipt([])

            rec = json.loads(receipt_path.read_text())
            rec['included'] = []
            receipt_path.write_text(json.dumps(rec))

            with tempfile.TemporaryDirectory() as tmp:
                tar_path, comp_sha = self._make_tar(tmp, {})
                payload = {
                    'id': 'run1',
                    'sha256': comp_sha,
                    'files': [],
                    'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
                }

                code = self._verify_payload(receipt_path, payload, reason='included', relocate=True)

            self.assertEqual(code, 1)

    def test_verify_receipt_duplicate_entries(self):

        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        entries = [
            {'path': 'main.py', 'size': len(content), 'sha256': sha},
            {'path': 'main.py', 'size': len(content), 'sha256': sha}
        ]
        receipt_path = self._make_receipt(entries)

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='duplicate', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_receipt_bad_sizes(self):

        content = b'x'
        sha = hashlib.sha256(content).hexdigest()

        entries = [{'path': 'main.py', 'size': 100, 'sha256': sha}]
        receipt_path = self._make_receipt(entries)

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='size', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_receipt_hash_with_newline(self):

            content = b'x'
            sha = hashlib.sha256(content).hexdigest() + '\n'
            entries = [{'path': 'main.py', 'size': len(content), 'sha256': sha}]
            receipt_path = self._make_receipt(entries)

            with tempfile.TemporaryDirectory() as tmp:
                tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})
                payload = {
                    'id': 'run1',
                    'sha256': comp_sha,
                    'files': [{'path': 'main.py', 'bytes': len(content)}],
                    'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
                }

                code = self._verify_payload(receipt_path, payload, reason='sha256', relocate=True)

            self.assertEqual(code, 1)

    def test_verify_receipt_bad_git_metadata(self):

        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        entries = [{'path': 'main.py', 'size': len(content), 'sha256': sha}]
        receipt_path = self._make_receipt(entries)

        rec = json.loads(receipt_path.read_text())
        rec['git']['head'] = 'invalid'
        receipt_path.write_text(json.dumps(rec))

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='git', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_wrong_payload_id(self):

        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        entries = [{'path': 'main.py', 'size': len(content), 'sha256': sha}]
        receipt_path = self._make_receipt(entries)

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})
            payload = {
                'id': 'wrong_id',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='id', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_contradictory_saved_sha256(self):

        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        entries = [{'path': 'main.py', 'size': len(content), 'sha256': sha}]
        receipt_path = self._make_receipt(entries)

        with tempfile.TemporaryDirectory() as tmp:
            tar_path, comp_sha = self._make_tar(tmp, {'main.py': content})

            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': 'a' * 64, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='sha256', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_duplicate_archive_entries(self):

        content = b'x'
        sha = hashlib.sha256(content).hexdigest()
        entries = [{'path': 'main.py', 'size': len(content), 'sha256': sha}]
        receipt_path = self._make_receipt(entries)

        with tempfile.TemporaryDirectory() as tmp:
            tar_path = Path(tmp) / 'team-run.tar.gz'
            with tarfile.open(str(tar_path), 'w:gz') as tf:
                info1 = tarfile.TarInfo(name='main.py')
                info1.size = len(content)
                tf.addfile(info1, io.BytesIO(content))

                info2 = tarfile.TarInfo(name='main.py')
                info2.size = len(content)
                tf.addfile(info2, io.BytesIO(content))

            comp_sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
            payload = {
                'id': 'run1',
                'sha256': comp_sha,
                'files': [{'path': 'main.py', 'bytes': len(content)}],
                'saved': {'path': str(tar_path), 'sha256': comp_sha, 'bytes': tar_path.stat().st_size}
            }

            code = self._verify_payload(receipt_path, payload, reason='duplicate', relocate=True)

        self.assertEqual(code, 1)

    def test_verify_normal_directory_support(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'directories.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                directory = tarfile.TarInfo('dir/')
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
                entry = tarfile.TarInfo('dir/main.py')
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b'x'))
            members = pipeline._read_archive_members(path)
        self.assertEqual(members, {'dir/main.py': (1, hashlib.sha256(b'x').hexdigest())})

    def test_verify_oversized_tar_member(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self._make_tar(folder, {'main.py': b'x' * 1000})
            with patch.object(pipeline, 'MAX_ARCHIVE_READ', 100), patch.object(tarfile.TarFile, 'extractfile') as read:
                with self.assertRaisesRegex(ValueError, 'exceeds cap'):
                    pipeline._read_archive_members(path)
                read.assert_not_called()

    def test_check_invalid_postmanifest_metadata(self):
        initial = {'paths': ['agent.json', 'main.py'], 'files': 2, 'bytes': 100}
        for override, reason in [({'files': 3}, 'files count mismatch'), ({'bytes': True}, 'bytes invalid')]:
            with self.subTest(metadata=override):
                errors = io.StringIO()
                with patch.object(pipeline, '_run_atira', side_effect=[MagicMock(returncode=0, stdout=json.dumps(initial)), MagicMock(returncode=0, stdout=json.dumps({**initial, **override}))]), redirect_stderr(errors):
                    code = pipeline.cmd_check(Namespace(package='agent', atira='dummy', skip_tests=True))
                self.assertEqual(code, 1)
                self.assertIn(reason, errors.getvalue())
                self.assertFalse((self.tmp / 'dev' / 'runs').exists())

    def test_unittest_success_actual(self):
        self.assertEqual(self._run_check(skip_tests=False, runner='unittest'), 0)

    def test_git_head_changed_during_check(self):
        errors = io.StringIO()
        with patch.object(pipeline, '_git_info', side_effect=[('a' * 40, 'test', False), ('b' * 40, 'test', False)]), redirect_stderr(errors):
            code = self._run_check(skip_tests=True)
        self.assertEqual(code, 1)
        self.assertIn('HEAD/branch changed', errors.getvalue())
        self.assertFalse((self.tmp / 'dev' / 'runs').exists())

if __name__ == '__main__':
    unittest.main()

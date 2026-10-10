"""Isolation and provenance tests for the local agent runner."""
import json
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_agent import child_environment, execute, make_contract, snapshot_agent
from variant_server import PortalCluster


class RunnerTests(unittest.TestCase):
    def test_contract_maps_shuffled_sites_without_expected_answers(self):
        case = json.loads((Path(__file__).parent / 'cases.json').read_text())['baseline']
        cluster = SimpleNamespace(case=case, profile='modern', origins={
            role: f'http://127.0.0.1:{8000+i}/' for i, role in enumerate(('tender', 'specification', 'erp', 'mail'))})
        contract = make_contract(cluster, 480)
        self.assertEqual(contract['access'][0]['siteId'], 'desk-q')
        for site in contract['access']:
            self.assertIn(site['siteId'], contract['brief'])
            self.assertIn(site['url'], contract['brief'])
        self.assertNotIn('1540.74', json.dumps(contract))
        self.assertNotIn('87.08', json.dumps(contract))
        self.assertNotIn('SYN-MAT-AX', json.dumps(contract))

    def test_real_run_credentials_and_cdp_are_removed(self):
        args = SimpleNamespace(model_base='http://127.0.0.1:8765/qwen/v1', model_key='local',
                               budget=480, submit_reserve=180, parallel=3, visible=False, browsers_path=None)
        inherited = {'CHALLENGE_RUN_TOKEN': 'live-token', 'CHALLENGE_API_URL': 'https://example.invalid',
                     'CHALLENGE_SCOUTING': 'live-contract', 'CDP_URL': 'live-browser', 'CDP_URLS': '[]',
                     'RFQ_DRY_RUN': '1', 'QWEN_API_KEY': 'live-key', 'RFQ_MODEL': 'other',
                     'CLEF_BASE_URL': 'https://live.invalid', 'CLEF_API_KEY': 'live-clef-key',
                     'CHALLENGE_SITE_ACCESS': 'live-access'}
        with patch.dict('os.environ', inherited, clear=True):
            env = child_environment({'run_id': 'fake'}, args)
        for key in ('CHALLENGE_RUN_TOKEN', 'CHALLENGE_API_URL', 'CHALLENGE_SITE_ACCESS', 'CDP_URL', 'CDP_URLS',
                    'RFQ_DRY_RUN', 'RFQ_MODEL', 'CLEF_BASE_URL', 'CLEF_API_KEY'):
            self.assertNotIn(key, env)
        self.assertEqual(env['QWEN_API_KEY'], 'local')
        self.assertEqual(json.loads(env['CHALLENGE_SCOUTING']), {'run_id': 'fake'})

    def test_snapshot_omits_oracle_tests_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'; source.mkdir()
            for name in ('main.py', 'rfq/core.py', 'tests/oracle.py', 'dev/answers.json', '.venv/secret.json'):
                target = source / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(name)
            (source / 'rfq/link.py').symlink_to(source / 'tests/oracle.py')
            original = {p: p.read_bytes() for p in source.rglob('*') if p.is_file() and not p.is_symlink()}
            provenance = snapshot_agent(source, root / 'frozen')
            self.assertEqual({f['path'] for f in provenance['files']}, {'main.py', 'rfq/core.py'})
            self.assertEqual(original, {p: p.read_bytes() for p in original})
            self.assertTrue(all(len(f['sha256']) == 64 for f in provenance['files']))


class RunnerCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_stops_the_real_agent_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            (source / 'rfq').mkdir(parents=True)
            pidfile = root / 'child.pid'
            (source / 'main.py').write_text(
                'import os,time\nfrom pathlib import Path\n'
                f'Path({str(pidfile)!r}).write_text(str(os.getpid()))\n'
                'time.sleep(120)\n')
            folder = root / 'run'; folder.mkdir()
            args = SimpleNamespace(agent=source, python=Path(sys.executable), budget=480,
                submit_reserve=180, parallel=2, model_base='http://127.0.0.1:8765/qwen/v1',
                model_key='local', visible=False, browsers_path=None, case='baseline')
            with PortalCluster('baseline', 'classic') as cluster:
                task = asyncio.create_task(execute(args, cluster, folder))
                for _ in range(100):
                    if pidfile.exists():
                        break
                    await asyncio.sleep(.02)
                if not pidfile.exists():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.fail('Fixture process never started')
                pid = int(pidfile.read_text())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)


if __name__ == '__main__':
    unittest.main()

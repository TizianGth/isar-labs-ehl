"""Matrix coverage and candidate isolation under concurrent source edits."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_matrix
from run_agent import snapshot_agent
from variant_server import PROFILES, load_cases

class MatrixTests(unittest.IsolatedAsyncioTestCase):
    def test_smoke_covers_every_profile_and_case_once_or_more(self):
        selected = run_matrix.pairs()
        self.assertEqual(len(selected), 6)
        self.assertEqual({p for p, _ in selected}, set(PROFILES))
        self.assertEqual({c for _, c in selected}, set(load_cases()))
        self.assertEqual(len(run_matrix.pairs(True)), 30)

    async def test_source_edit_during_matrix_does_not_change_any_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / 'source'
            (agent / 'rfq').mkdir(parents=True)
            (agent / 'main.py').write_text('# original candidate\n')
            (agent / 'rfq/core.py').write_text('# original logic\n')
            observed = []
            async def execute(args, cluster, folder):
                snapshot_agent(args.agent, folder / 'agent-snapshot')
                observed.append((folder / 'agent-snapshot/main.py').read_text())
                # Represents Claude/team editing their own checkout mid-matrix.
                (agent / 'main.py').write_text('# concurrently edited candidate\n')
                (folder / 'report.json').write_text(json.dumps({
                    'submission_score': {'quality': 100, 'perfect': True, 'checks': []},
                    'portal_receipt': 'synthetic-test-only', 'runtime_seconds': 0,
                    'timed_out': False}))
                return 0
            args = SimpleNamespace(agent=agent, out=root / 'runs', full=False,
                                   budget=480, submit_reserve=180, parallel=2)
            with patch.object(run_matrix, 'execute', execute):
                code = await run_matrix.run(args)
            self.assertEqual(code, 0)
            self.assertEqual(observed, ['# original candidate\n'] * 6)
            self.assertEqual((agent / 'main.py').read_text(), '# concurrently edited candidate\n')
            summary = json.loads(next((root / 'runs').glob('*/matrix.json')).read_text())
            self.assertEqual(len(summary['results']), 6)

if __name__ == '__main__':
    unittest.main()

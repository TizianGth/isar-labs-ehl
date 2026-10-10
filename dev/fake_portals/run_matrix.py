#!/usr/bin/env python3
"""Sequential model-driven synthetic UI matrix; no Atira upload or live portals."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlparse

from run_agent import execute, snapshot_agent
from variant_server import PortalCluster, PROFILES, load_cases

SMOKE = [('classic', 'baseline'), ('modern', 'locale-boundary'), ('legacy', 'blocked'),
         ('workbench', 'future-withdrawal'), ('scanned', 'baseline'), ('busy-office', 'shared-stock')]

def pairs(full=False):
    return [(profile, case) for profile in PROFILES for case in load_cases()] if full else list(SMOKE)

async def run(args):
    root = args.out.absolute() / (time.strftime('%Y%m%d-%H%M%S') + '-matrix')
    root.mkdir(parents=True)
    # Freeze once for the entire matrix. Later edits to the source cannot change
    # the tested candidate between profiles. Each child copies this same snapshot.
    frozen = root / 'agent-snapshot'
    provenance = snapshot_agent(args.agent, frozen)
    (root / 'provenance.json').write_text(json.dumps(provenance, indent=2)+'\n')
    summary = {'kind': 'synthetic developer matrix, not Atira score',
               'candidate_commit': provenance['commit'], 'candidate_dirty': provenance['dirty_at_snapshot'],
               'settings': {'budget': args.budget, 'submit_reserve': args.submit_reserve, 'parallel': args.parallel},
               'results': []}
    for profile, case in pairs(args.full):
        folder = root / f'{profile}-{case}'
        folder.mkdir()
        single = SimpleNamespace(**vars(args), profile=profile, case=case)
        single.agent = frozen
        print(json.dumps({'event': 'starting', 'profile': profile, 'case': case}), flush=True)
        with PortalCluster(case, profile) as cluster:
            await execute(single, cluster, folder)
        report = json.loads((folder / 'report.json').read_text())
        score = report['submission_score']
        result = {'profile': profile, 'case': case, 'quality': score['quality'],
                  'perfect': score['perfect'], 'receipt': report['portal_receipt'],
                  'seconds': report['runtime_seconds'], 'timed_out': report['timed_out'],
                  'report': str(folder / 'report.json'),
                  'failed_fields': [r['field'] for r in score['checks'] if not r.get('ok')]}
        summary['results'].append(result)
        (root / 'matrix.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps({'event': 'finished', **result}), flush=True)
    print(f'Matrix report: {root / "matrix.json"}', flush=True)
    return 0 if all(r['perfect'] for r in summary['results']) else 1

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent.parent / 'runs/fake-portals')
    parser.add_argument('--budget', type=int, default=480)
    parser.add_argument('--submit-reserve', type=int, default=180)
    parser.add_argument('--parallel', type=int, default=2)
    parser.add_argument('--model-base', default='http://127.0.0.1:8765/qwen/v1')
    parser.add_argument('--model-key', default='local')
    parser.add_argument('--browsers-path', type=Path)
    parser.add_argument('--visible', action='store_true')
    parser.add_argument('--full', action='store_true', help='30 model-driven runs; default: six representative runs')
    parser.add_argument('--dry-run', action='store_true', help='Print matrix without servers, model calls or artifacts')
    args = parser.parse_args()
    if urlparse(args.model_base).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('--model-base must be an existing Atira proxy on loopback')
    if args.budget < 120 or not 30 <= args.submit_reserve < args.budget or args.parallel < 1:
        parser.error('invalid time/concurrency limits')
    if args.dry_run:
        print(json.dumps(pairs(args.full), indent=2))
        return 0
    args.agent = args.agent.absolute()
    args.python = args.python.absolute()  # Preserve venv symlink behavior.
    return asyncio.run(run(args))

if __name__ == '__main__':
    raise SystemExit(main())

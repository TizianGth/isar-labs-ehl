#!/usr/bin/env python3
"""Run an unchanged agent snapshot against local synthetic portals.

No platform submission, no scouting data, no public fixture answers. Qwen may be
used through an existing local Atira model proxy. Browser origins are loopback.
"""
import argparse
import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from scoring import evaluate_answer, evaluate_state
from variant_server import PortalCluster, PROFILES, load_cases

HERE = Path(__file__).resolve().parent


def make_contract(cluster, seconds):
    case = cluster.case
    ids = {'tender': 'hub-k', 'specification': 'vault-x', 'erp': 'flow-m', 'mail': 'desk-q'}
    lines = [
        'This is an entirely synthetic local development RFQ, not a real tender.',
        f'Tender reference: {case["reference"]}. Scenario date (UTC): {case["scenario_datetime_utc"]}.',
        f'Bidder company: {case["company"]}. Bidder VAT ID: {case["vat_id"]}.',
        f'The sales director authorizing order discounts has mailbox localpart {case["director"]}.',
        f'The bidder mailbox is {case["mailbox"]}. Only authorized sender at this bidder domain is binding.',
        'Websites (their siteIds are exact identifiers from the access contract):',
    ]
    descriptions = {'tender': 'tender portal: original quantities, bidder Q&A, proposal form and signing',
                    'specification': 'supplier portal: specification revisions and technical requirements',
                    'erp': 'ERP: catalog groups, material status, conditions, freely available stock and lead time',
                    'mail': 'webmail: approval records and signing TAN; read inbox and archive'}
    for role in ('tender', 'specification', 'erp', 'mail'):
        lines.append(f'- siteId {ids[role]}, {cluster.origins[role]}: {descriptions[role]}.')
    lines += ['Authorized mail topics (localpart + bidder domain, exact reference, dated no later than scenario):',
              f'- discount: keyword Discount approval, sender {case["director"]}.']
    keywords = {'clause_rules': 'Commercial clauses', 'packaging': 'Packaging tariffs',
                'fx': 'FX fixing', 'metal_index': 'Metal indices'}
    for topic, local in case['authorized'].items():
        lines.append(f'- {topic}: subject keyword {keywords[topic]}, sender localpart {local}.')
    lines += [
        'Rules: Use the revision marked CURRENT. Match bore, nominal stroke, pressure, mounting and certification exactly.',
        'Only released/active materials count; blocked materials do not. If no active exact match for any position, submit no_bid.',
        'Q&A dated after the scenario is invalid, including future withdrawals. Withdrawn answers do not apply.',
        'Quantities are pieces: convert sets by the current specification. Choose latest remaining dated quantity change.',
        'Choose a condition valid at the scenario date, largest quantity tier no greater than position quantity.',
        'Convert price per price-unit to per piece, apply approved EUR-per-currency FX; round base price half up to cents.',
        'Use newest valid authorized discount approval; ignore future, draft, unrelated or wrong-sender mail.',
        'Discount scope is stated in commercial clauses per Q&A condition group. Setup and packaging are never discounted.',
        'Mass surcharge = surcharge mass in kg times index rate in window containing scenario date (inclusive).',
        'Setup cost is allocated over final position quantity. Packaging = ceil(qty/pieces-per-carton)*tariff per position.',
        'Unit price = round(discounted base + discounted mass surcharge + setup/qty + packaging/qty, 2), half up.',
        'Report surcharge per unit before discount and including setup; packaging per unit before discount.',
        'No project bonus or setup pool applies in this fixture. Rebate total is therefore zero.',
        'Total net = sum(position final quantity * rounded unit price). For no_bid: positions empty, total and discount zero.',
        'Lead time: compare total demand per material with freely available stock; inspection/blocked stock never counts.',
        'Submit a MAIN proposal on the tender portal (no own terms); accept tender documents, inspect review, request TAN,',
        'fetch newly generated signing TAN from mail, enter it and confirm final binding browser dialog. Currency EUR.',
        'The portal receipt is the authoritative confirmation. This is a local fake submission only.',
    ]
    access = [{'siteId': ids[role], 'url': cluster.origins[role],
               'credentials': {'username': 'demo', 'password': 'synthetic-only'}}
              for role in ('mail', 'erp', 'tender', 'specification')]  # deliberately not the assumed role order
    return {'run_id': f'synthetic-{case["name"]}-{cluster.profile}', 'brief': '\n'.join(lines),
            'access': access, 'expires_at': int((time.time() + seconds + 60) * 1000),
            'submission': {'kind': 'browser'}}


def snapshot_agent(source, dest):
    source = source.resolve()
    if not (source / 'main.py').is_file() or not (source / 'rfq').is_dir():
        raise ValueError('--agent must contain main.py and rfq/')
    dest.mkdir(parents=True)
    files = []
    allowed = {'.py', '.js', '.json', '.toml', '.lock', '.md'}
    for path in source.rglob('*'):
        rel = path.relative_to(source)
        if any(part in ('.venv', '.git', '__pycache__', '.pytest_cache', 'tests', 'dev', 'docs') for part in rel.parts):
            continue
        if path.is_symlink() or not path.is_file() or path.suffix not in allowed:
            continue
        if rel.parts[0] != 'rfq' and str(rel) not in ('main.py', 'pyproject.toml', 'uv.lock', 'agent.json', 'README.md'):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes(); target.write_bytes(data)
        files.append({'path': str(rel), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    commit = None
    dirty = None
    try:
        commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--short', '--', '.'], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    lineage = None
    parent_provenance = source.parent / 'provenance.json'
    if commit is None and source.name == 'agent-snapshot' and parent_provenance.is_file():
        parent = json.loads(parent_provenance.read_text())
        hashes = lambda rows: {r['path']: r['sha256'] for r in rows}
        if hashes(parent.get('files', [])) == hashes(files):
            commit, dirty = parent.get('commit'), parent.get('dirty_at_snapshot')
            lineage = str(parent_provenance)
    return {'source': str(source), 'commit': commit, 'dirty_at_snapshot': dirty, 'files': files,
            'matching_parent_provenance': lineage}


def child_environment(contract, args):
    env = os.environ.copy()
    for key in ('CHALLENGE_RUN_TOKEN', 'CHALLENGE_API_URL', 'CHALLENGE_SITE_ACCESS', 'CHALLENGE_SCOUTING', 'CDP_URL', 'CDP_URLS',
                'RFQ_DRY_RUN', 'RFQ_MODEL', 'QWEN_BASE_URL', 'QWEN_API_KEY', 'CLEF_BASE_URL', 'CLEF_API_KEY'):
        env.pop(key, None)
    env.update(CHALLENGE_SCOUTING=json.dumps(contract), QWEN_BASE_URL=args.model_base,
               QWEN_API_KEY=args.model_key, RFQ_RUN_BUDGET=str(args.budget),
               RFQ_SUBMIT_RESERVE=str(args.submit_reserve), RFQ_LLM_PARALLEL=str(args.parallel),
               PYTHONDONTWRITEBYTECODE='1', HEADLESS='0' if args.visible else '1')
    if args.browsers_path:
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(args.browsers_path)
    return env


async def execute(args, cluster, folder):
    frozen = folder / 'agent-snapshot'
    provenance = snapshot_agent(args.agent, frozen)
    provenance['python_executable'] = str(args.python)
    provenance['harness_sha256'] = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                                    for name in ('server.py', 'cases.json', 'scoring.py', 'run_agent.py',
                                                 'variant_server.py', 'profile_workbench.py',
                                                 'profile_scanned.py', 'profile_busy_office.py', 'extra_cases.py')
                                    if (HERE / name).is_file()}
    provenance['settings'] = {'budget': args.budget, 'submit_reserve': args.submit_reserve,
                              'parallel': args.parallel, 'model_base': args.model_base}
    contract = make_contract(cluster, args.budget)
    (folder / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    (folder / 'contract.json').write_text(json.dumps(contract, indent=2) + '\n')
    output = folder / 'agent-output.json'
    started = time.monotonic()
    timeout = False
    logpath = folder / 'agent.log'
    with logpath.open('w') as logfile:
        process = await asyncio.create_subprocess_exec(str(args.python), 'main.py', '--out', str(output),
            cwd=frozen, env=child_environment(contract, args), stdout=logfile, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        try:
            code = await asyncio.wait_for(process.wait(), timeout=args.budget + 90)
        except asyncio.TimeoutError:
            timeout = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=8)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            code = process.returncode
        finally:
            # A cancelled matrix must not leave its agent or Chromium children
            # running and occupying the team's shared model slots.
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=8)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
    state = cluster.snapshot()
    (folder / 'portal-state.json').write_text(json.dumps(state, indent=2, default=str) + '\n')
    agent_output = {}
    if output.exists():
        try: agent_output = json.loads(output.read_text())
        except ValueError: pass
    usage, errors = {}, []
    for line in logpath.read_text(errors='replace').splitlines():
        try: event = json.loads(line)
        except ValueError: continue
        if event.get('event') == 'usage': usage = event
        if event.get('event') in ('fatal', 'submit_error', 'resolve_error', 'mission_crashed', 'watchdog_timeout'):
            errors.append(event)
    report = {'case': args.case, 'profile': args.profile, 'agent_commit': provenance['commit'],
              'runtime_seconds': round(time.monotonic() - started, 2), 'exit_code': code, 'timed_out': timeout,
              'submission_score': evaluate_state(state, args.case),
              'calculation_score': evaluate_answer(agent_output.get('answer'), args.case),
              'sdk_usage_not_platform_billing': usage, 'errors': errors,
              'portal_receipt': state.get('receipt'), 'tan_requests': state.get('tan_requests'),
              'files': {'log': str(logpath), 'report': str(folder / 'report.json'), 'portal_state': str(folder / 'portal-state.json')}}
    (folder / 'report.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    print(json.dumps({k: report[k] for k in ('case', 'profile', 'runtime_seconds', 'exit_code', 'timed_out', 'portal_receipt')}, indent=2))
    print(json.dumps({'synthetic_submission_score': report['submission_score']['quality'],
                      'synthetic_calculation_score': report['calculation_score']['quality'], 'report': str(folder / 'report.json')}))
    return 0 if report['submission_score']['perfect'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--profile', choices=PROFILES, default='classic')
    parser.add_argument('--case', choices=tuple(load_cases()), default='baseline')
    parser.add_argument('--out', type=Path, default=HERE / 'runs')
    parser.add_argument('--budget', type=int, default=480)
    parser.add_argument('--submit-reserve', type=int, default=180)
    parser.add_argument('--parallel', type=int, default=3)
    parser.add_argument('--model-base', default='http://127.0.0.1:8765/qwen/v1')
    parser.add_argument('--model-key', default=os.environ.get('FAKE_PORTALS_MODEL_KEY', 'local'))
    parser.add_argument('--browsers-path', type=Path)
    parser.add_argument('--visible', action='store_true')
    args = parser.parse_args()
    if urlparse(args.model_base).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('--model-base must be an existing local Atira proxy on loopback')
    if args.budget < 120 or not 30 <= args.submit_reserve < args.budget or args.parallel < 1:
        parser.error('invalid time/concurrency limits')
    args.agent = args.agent.resolve()
    # A venv interpreter is often a symlink. Resolving it would silently discard
    # the venv and its installed dependencies.
    args.python = args.python.absolute()
    args.out = args.out.resolve(); args.out.mkdir(parents=True, exist_ok=True)
    folder = args.out / (time.strftime('%Y%m%d-%H%M%S') + f'-{args.case}-{args.profile}')
    folder.mkdir()
    with PortalCluster(args.case, args.profile) as cluster:
        print(json.dumps({'origins': cluster.origins, 'run_dir': str(folder)}), flush=True)
        return asyncio.run(execute(args, cluster, folder))


if __name__ == '__main__':
    raise SystemExit(main())

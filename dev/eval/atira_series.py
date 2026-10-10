#!/usr/bin/env python3
"""Serial, immutable Atira TEST runs, complete evidence, fail-closed release gate.

Never submits/cancels a scored run. An ambiguous upload is recorded and must be
recovered by label; it is never automatically posted a second time.
"""
import argparse
import hashlib
import json
from pathlib import Path
import queue
import re
import shutil
import statistics
import subprocess
import threading
import time


def manifest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()
            and not any(part in {'.git', '.venv', '__pycache__', '.pytest_cache'} for part in p.relative_to(root).parts)}


def save(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)


class MCP:
    def __init__(self):
        self.process = subprocess.Popen(['atira', 'mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
        self.inbox, self.counter = queue.Queue(), 0
        threading.Thread(target=self._read, daemon=True).start()
        self._request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                    'clientInfo': {'name': 'speed-quality-test-series', 'version': '1'}})
        self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def _read(self):
        for line in self.process.stdout:
            try:
                self.inbox.put(json.loads(line))
            except ValueError:
                continue

    def _send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def _request(self, method, params, timeout=50):
        self.counter += 1
        ident = self.counter
        self._send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        end = time.monotonic() + timeout
        while True:
            value = self.inbox.get(timeout=max(.01, end - time.monotonic()))
            if value.get('id') == ident:
                return value

    def call(self, name, args):
        return self._request('tools/call', {'name': name, 'arguments': args},
                             timeout=50 if name == 'test_run' else 25)

    def close(self):
        self.process.terminate()  # local readonly MCP client, NEVER platform run
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def mcp_text(response):
    return '\n'.join(c.get('text', '') for c in response.get('result', {}).get('content', []) if c.get('type') == 'text')


def stats(rows, required_count):
    times = [r['seconds'] for r in rows if isinstance(r.get('seconds'), (int, float))]
    mean = statistics.mean(times) if len(times) == len(rows) and rows else None
    return {'runs': len(rows), 'required_runs': required_count, 'mean_seconds': mean,
            'worst_seconds': max(times) if times else None,
            'total_cost_usd': sum(r.get('cost_usd', 0) or 0 for r in rows),
            'cost_time_sum_usd_seconds': sum(r.get('cost_time_usd_seconds', 0) or 0 for r in rows),
            'failures': [r['run_id'] for r in rows if not r.get('quality_ok')],
            'release_gate_passed': len(rows) == required_count and required_count >= 10
            and all(r.get('quality_ok') for r in rows) and mean is not None and mean <= 300}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--count', type=int, default=3)
    parser.add_argument('--resume', help='Already launched first run, without reposting')
    parser.add_argument('--freeze-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.count <= 30 or len(args.label) > 90:
        parser.error('count must be 1..30 and label at most 90 chars')
    root = args.out.absolute()
    root.mkdir(parents=True, exist_ok=True)
    frozen = root / 'agent-snapshot'
    original = manifest(args.agent)
    if not frozen.exists():
        shutil.copytree(args.agent, frozen, ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', '.pytest_cache'))
    frozen_manifest = manifest(frozen)
    if frozen_manifest != original:
        parser.error('existing frozen package differs from source; choose a new output directory')
    digest = hashlib.sha256(json.dumps(frozen_manifest, sort_keys=True).encode()).hexdigest()
    state_file = root / 'series.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {
        'kind': 'official Atira public TEST runs, never leaderboard submission', 'label': args.label,
        'content_manifest_sha256': digest, 'files': frozen_manifest, 'results': []}
    if state['content_manifest_sha256'] != digest or state['label'] != args.label:
        parser.error('series provenance mismatch')
    save(state_file, state)
    if args.freeze_only:
        print(json.dumps({'frozen': str(frozen), 'manifest_sha256': digest}))
        return 0
    client = MCP()
    try:
        for index in range(len(state['results']), args.count):
            folder = root / f'{index + 1:02}'
            folder.mkdir(exist_ok=True)
            intent_file = folder / 'intent.json'
            if intent_file.exists():
                intent = json.loads(intent_file.read_text())
                if not intent.get('run_id'):
                    raise RuntimeError(f'ambiguous prior upload; recover label {intent["label"]} from list_runs before retrying')
                run_id = intent['run_id']
            else:
                intent = {'label': f'{args.label}-{index + 1:02}', 'manifest_sha256': digest,
                          'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                run_id = args.resume if index == 0 else None
                if run_id:
                    intent['run_id'] = run_id
                    save(intent_file, intent)
                else:
                    save(intent_file, intent)  # persist BEFORE non-idempotent upload
                    response = client.call('test_run', {'dir': str(frozen), 'challenge': 'rfq', 'label': intent['label']})
                    save(folder / 'launch.json', response)
                    match = re.search(r'queued as (run_[a-z0-9]+)', mcp_text(response))
                    if not match:
                        raise RuntimeError('upload response ambiguous or rejected; inspect launch.json, do not repeat automatically')
                    run_id = intent['run_id'] = match[1]
                    save(intent_file, intent)
            print(json.dumps({'event': 'test_run', 'index': index + 1, 'run_id': run_id, 'label': intent['label']}), flush=True)
            while True:
                if manifest(frozen) != frozen_manifest:
                    raise RuntimeError('frozen package changed during series')
                try:
                    completed = subprocess.run(['atira', 'status', run_id, '--json'], capture_output=True, text=True, timeout=25)
                    view = json.loads(completed.stdout)
                    if completed.returncode != 0 or view.get('run_id') != run_id:
                        raise ValueError('status unavailable')
                    save(folder / 'status.json', view)
                    print(json.dumps({'event': 'status', 'run_id': run_id, 'phase': view.get('phase'), 'calls': view.get('calls')}), flush=True)
                    if view.get('phase') == 'done':
                        break
                except (subprocess.TimeoutExpired, ValueError):
                    print(json.dumps({'event': 'status_unavailable', 'run_id': run_id}), flush=True)
                time.sleep(20)
            evidence = {}
            for name, extra in [('get_run', {}), ('run_logs', {'limit': 1000, 'stream': 'stdout,stderr'}),
                                ('run_profile', {'bucket': '30s'}), ('run_llm_calls', {'limit': 1000})]:
                try:
                    value = client.call(name, {'run_id': run_id, **extra})
                except queue.Empty:
                    value = {'error': 'readonly request timeout'}
                save(folder / f'{name}.json', value)
                evidence[name] = mcp_text(value)
            score = view.get('score') or {}
            seconds = score.get('duration_ms')
            if seconds is not None:
                seconds /= 1000
            elif view.get('finished_at') is not None and view.get('started_at') is not None:
                seconds = (view['finished_at'] - view['started_at']) / 1000
            cost = score.get('cost_micro_usd', view.get('cost_micro_usd', 0)) / 1e6
            receipt = bool(re.search(r'"event"\s*:\s*"(?:submission_accepted|submitted)"', evidence.get('run_logs', '')))
            platform_accepted = view.get('status') == 'submitted' and score.get('eligible') is True
            row = {'run_id': run_id, 'status': view['status'], 'quality': score.get('quality'),
                   'eligible': score.get('eligible'), 'confirmed_receipt': receipt,
                   'accepted_by_platform': platform_accepted, 'seconds': seconds,
                   'cost_usd': cost, 'cost_time_usd_seconds': cost * seconds if seconds is not None else None,
                   'calls': view.get('calls'), 'breakdown': score.get('breakdown'), 'error': view.get('error'),
                   'quality_ok': score.get('quality') == 100 and score.get('eligible') is True and (receipt or platform_accepted)}
            package = re.search(r'\bpackage ([a-f0-9]{12,64})\b', evidence.get('get_run', ''))
            row['platform_package_sha256_prefix'] = package[1] if package else None
            previous = {r.get('platform_package_sha256_prefix') for r in state['results']
                        if r.get('platform_package_sha256_prefix')}
            if package and previous and package[1] not in previous:
                row['quality_ok'] = False
                row['package_mismatch'] = True
            state['results'].append(row)
            state['summary'] = stats(state['results'], args.count)
            save(state_file, state)
            print(json.dumps({'event': 'finished', **row, 'breakdown': '[saved]'}), flush=True)
        return 0 if all(r['quality_ok'] for r in state['results']) else 1
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())

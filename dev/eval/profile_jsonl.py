#!/usr/bin/env python3
"""Summarize recorded telemetry, keeping inclusive parallel timings separate from wall time."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def analyze(text):
    rows, bad = [], 0
    for line in text.splitlines():
        candidate = line.split('stdout: ', 1)[-1].strip()
        if not candidate.startswith('{'):
            continue
        try:
            row = json.loads(candidate)
            if isinstance(row, dict) and 'event' in row:
                rows.append(row)
        except ValueError:
            bad += 1
    metrics = defaultdict(lambda: {'count': 0, 'inclusive_seconds': 0.0})
    milestones = []
    for row in rows:
        event = row['event']
        if event == 'llm_request':
            for key in ('slot_wait_s', 'request_s'):
                tag = f'{row.get("request_class", "unknown")}.{key}'
                metrics[tag]['count'] += 1
                metrics[tag]['inclusive_seconds'] += row.get(key, 0) or 0
        if event in ('browser_action', 'browser_wait', 'phase_timing'):
            key = event + '.' + str(row.get('phase') or row.get('action') or 'unknown')
            metrics[key]['count'] += 1
            metrics[key]['inclusive_seconds'] += row.get('seconds', 0) or 0
        if event in ('mission_status', 'evidence_done', 'review_check', 'submit_phase_done', 'exit'):
            milestones.append({k: row[k] for k in ('t', 'event', 'mission', 'status', 'seconds', 'blocking') if k in row})
    return {'records': len(rows), 'invalid_json_records': bad,
            'first_observed_seconds': min((r.get('t', 0) for r in rows), default=None),
            'observed_wall_seconds': max((r.get('t', 0) for r in rows), default=None),
            'coverage_note': 'Metrics cover only supplied records. Retrospective platform logs may omit earlier events; a tool-formatted record may be cut even when the original stdout JSON was valid.',
            'timing_note': 'Inclusive timings overlap. Never subtract these sums from wall time. Milestones describe the critical-path boundaries.',
            'inclusive_metrics': dict(metrics), 'milestones': milestones}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    text = args.input.read_text()
    if args.input.suffix == '.jsonl':
        chunks = [json.loads(line) for line in text.splitlines() if line.strip()]
        if chunks and all('stream' in c and 'text' in c for c in chunks):
            text = ''.join(c['text'] for c in chunks if c['stream'] == 'stdout')
    elif args.input.suffix == '.json':
        data = json.loads(text)
        text = '\n'.join(c.get('text', '') for c in data.get('result', {}).get('content', []) if c.get('type') == 'text')
    report = analyze(text)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'records': report['records'], 'invalid_json_records': report['invalid_json_records']}))

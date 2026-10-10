"""Metamorphic resolver check: a future withdrawal must not change today's bid.

Uses recorded browser evidence, then adds one explicitly future-dated fact.
No browser, LLM, platform connection or live repository mutation.
"""
import argparse
import copy
from datetime import timedelta
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Recorded local run directory')
    parser.add_argument('--agent', type=Path, help='Candidate package; defaults to the recorded snapshot')
    args = parser.parse_args()
    run = args.run.resolve()
    agent = args.agent.resolve() if args.agent else run / 'agent-snapshot'
    if not (agent / 'rfq/rules/resolve.py').is_file():
        parser.error('Full recorded snapshot missing. Use --agent with a matching/candidate agent package.')
    sys.path.insert(0, str(agent))
    from rfq.rules.resolve import Context, build
    from rfq.rules.parse import clean, when
    output = json.loads((run / 'agent-output.json').read_text())
    brief = output['brief']
    ctx = Context(tender_reference=clean(brief['tender_reference']), scenario=when(brief['scenario_datetime_utc']),
                  director_localpart=brief.get('director_localpart'), searched_groups=set(output['erp_complete']),
                  authorized={r['topic']: r['sender_localpart'] for r in brief['authorized_records']})
    evidence = copy.deepcopy(output['evidence'])
    before = build(ctx, evidence)
    future_at = (ctx.scenario + timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    evidence.setdefault('clarifications', []).append({'url': 'synthetic:metamorphic-future-withdrawal', 'data': {
        'entries': [{'number': 'FUTURE-ONLY', 'datetime_utc': future_at,
                     'answer': 'Answer 2 is withdrawn without replacement.',
                     'facts': [{'type': 'withdrawal', 'withdrawn_number': '2'}]}]}})
    after = build(ctx, evidence)
    report = {'agent_source': str(agent), 'future_withdrawal_datetime': future_at, 'scenario_datetime': ctx.scenario.isoformat(),
              'unchanged_as_required': before.answer == after.answer,
              'before_answer': before.answer, 'after_answer': after.answer,
              'trace_before': before.trace, 'trace_after': after.trace, 'live_agent_modified': False}
    destination = Path(__file__).with_name('repros') / 'future-withdrawal-check.json'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('unchanged_as_required', 'before_answer', 'after_answer')}, indent=2))
    return 0 if report['unchanged_as_required'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

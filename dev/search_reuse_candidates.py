"""Offline genetic search: 200 unique configurations, no browser/proxy calls.

Ranks measured synthetic request characters and dependency-wave counts. These
are proxies, NOT platform latency/token measurements or release qualification.
Usage: python dev/search_reuse_candidates.py agent output.json
"""
import itertools
import json
import math
import os
from pathlib import Path
import random
import sys

import measure_reuse_requests as requests
from rfq.agent import fused, orchestrator

fused.log = orchestrator.log = lambda *args, **kwargs: None
SPACE = {
    'RFQ_DECISION_VIEW_CHARS': (2400, 4000, 6500),
    'RFQ_TABLE_SAMPLES': (1, 2, 3),
    'RFQ_RESULT_CHARS': (400, 800, 1200),
    'RFQ_SOURCE_PARALLEL': (1, 2, 4, 8),
    'RFQ_EXTRACT_BATCH': (1, 2, 3, 4),
}
KEYS = tuple(SPACE)
RANDOM = random.Random(907)
rendered = {}


def evaluate(genome):
    flags = dict(zip(KEYS, genome))
    render_key = genome[:3]
    if render_key not in rendered:
        for key, value in flags.items():
            os.environ[key] = str(value)
        capture = requests.capture()
        rendered[render_key] = sum(row['total'] for row in capture)
    # A fixed synthetic workload of sixteen INDEPENDENT model-selected reads.
    # Concurrency never removes a dependency, evidence row or required chunk.
    waves = math.ceil(16 / flags['RFQ_SOURCE_PARALLEL'])
    extraction_calls = math.ceil(16 / flags['RFQ_EXTRACT_BATCH'])
    return {'flags': flags, 'request_characters': rendered[render_key],
            'independent_read_waves': waves, 'synthetic_extraction_calls': extraction_calls,
            'platform_seconds': None, 'metered_tokens': None, 'release_qualified': False}


def rank(item):
    return (item['request_characters'], item['synthetic_extraction_calls'], item['independent_read_waves'])


def main():
    universe = list(itertools.product(*(SPACE[key] for key in KEYS)))
    population = RANDOM.sample(universe, 40)
    evaluated = {}
    generations = 0
    while len(evaluated) < 200:
        for genome in population:
            if genome not in evaluated:
                evaluated[genome] = evaluate(genome)
                if len(evaluated) == 200:
                    break
        parents = sorted(evaluated, key=lambda genome: rank(evaluated[genome]))[:16]
        population = []
        for _ in range(40):
            left, right = RANDOM.sample(parents, 2)
            child = [left[i] if RANDOM.random() < .5 else right[i] for i in range(len(KEYS))]
            mutation = RANDOM.randrange(len(KEYS))
            child[mutation] = RANDOM.choice(SPACE[KEYS[mutation]])
            population.append(tuple(child))
        # Keep exploration alive when crossover converges on already-seen variants.
        population += RANDOM.sample([g for g in universe if g not in evaluated], min(10, len(universe)-len(evaluated)))
        generations += 1
    results = sorted(evaluated.values(), key=rank)
    report = {'seed': 907, 'unique_candidates': len(evaluated), 'generations': generations,
              'measurement': 'Actual offline prompt builders; synthetic no-inference responses.',
              'ranking': 'characters, extraction calls, read waves; no weighted invented seconds',
              'release_gate': 'BLOCKED: no full-run latency, token or unseen-quality measurement',
              'baseline_flags': {'RFQ_DECISION_VIEW_CHARS': 6500, 'RFQ_TABLE_SAMPLES': 3,
                                 'RFQ_RESULT_CHARS': 1200, 'RFQ_SOURCE_PARALLEL': 4, 'RFQ_EXTRACT_BATCH': 3},
              'candidates': results}
    Path(sys.argv[2]).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({key: report[key] for key in ('unique_candidates', 'generations', 'release_gate')}))
    print(json.dumps({'best_offline_candidate': results[0]}))


if __name__ == '__main__':
    main()

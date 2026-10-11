"""Synthetic selected-source workload; measure avoided requests, not platform seconds.
Usage: python dev/benchmark_reuse.py agent output.json
"""
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

os.environ['RFQ_PROFILE'] = '0'
os.environ['RFQ_CLEF_ROUTE'] = '0'
sys.path.insert(0, sys.argv[1])
from rfq import llm
from rfq.agent.fused import FusedExplorer
from rfq.agent.orchestrator import Orchestrator


async def measure(reuse):
    requests, reads = [], []
    sources = {f'https://synthetic.test/source-{index}':
               f'Freely usable stock\n| Material | Stock |\n| MAT-S{index} | {index},50 (de-DE) |'
               for index in range(20)}
    template = {'kind': 'materials', 'table': 't1', 'headers': ['Material', 'Stock'],
                'columns': {'material_number': 0, 'unrestricted_stock': 1},
                'list_field': 'materials', 'reuse': True, 'reuse_scope': 'layout', 'source_complete': True}
    def extract(text):
        material, stock = re.search(r'\| (MAT-S\d+) \| ([^|]+) \|', text).groups()
        return {'materials': [{'material_number': material, 'unrestricted_stock': stock}]}
    async def ask(system, user, **kwargs):
        requests.append({'tag': kwargs.get('tag'), 'input_characters': len(system)+len(user)})
        if str(kwargs.get('tag', '')).startswith('erp@'):
            result = {'actions': [{'a': 'finish', 'missing': ['remaining selected sources']}]}
            if reuse:
                result['bindings'] = [template]
            else:
                text = next(iter(sources.values()))
                result['facts'] = [{'kind': 'materials', 'data': extract(text), 'quote': text}]
        elif '<<<source_id=' in user:
            result = {'results': [{'source_id': match[1], 'data': extract(match[3])} for match in
                re.finditer(r'<<<source_id=(s\d+); URL=([^>]+)>>>\n(.*?)\n<<<END>>>', user, re.S)]}
        else:
            result = extract(user)
        requests[-1]['output_characters'] = len(json.dumps(result))
        return result
    llm.ask_json = ask
    async def read_url(url):
        reads.append(url)
        await asyncio.sleep(0)
        return sources[url]
    site = SimpleNamespace(context=object(), site_id='synthetic', session_generation=0,
        url='https://synthetic.test', origin='synthetic.test', absolute=lambda url: url,
        was_seen=lambda url: url in sources, read_url=read_url)
    o = Orchestrator({'expires_at': (time.time()+1000)*1000}, {'synthetic': site})
    mission = o.erp_mission('SYN-GROUP', [{'ref': 'SYN-REQ', 'bore_mm': 'raw', 'stroke_mm': 'raw', 'pressure_bar': 'raw', 'mounting': 'raw'}])
    mission.name = 'erp'
    first = next(iter(sources))
    site.page = SimpleNamespace(url=first)
    async def observe(**kwargs):
        return sources[first]
    site.observe = observe
    explorer = FusedExplorer(site, mission, o.extract)
    await explorer.run()  # Both cases include the same actual first-page decision prompt.
    await explorer._extract_urls({'kind': 'materials', 'urls': list(sources)})
    await o.source_reads.close()
    rows = {row['material_number']: row['unrestricted_stock'] for item in o.bb.get('materials')
            for row in item['data']['materials']}
    expected = {row['material_number']: row['unrestricted_stock'] for text in sources.values()
                for row in extract(text)['materials']}
    assert rows == expected, 'all selected source values must match exactly'
    return {'mock_inference_requests': len(requests), 'input_characters': sum(r['input_characters'] for r in requests),
            'output_characters': sum(r['output_characters'] for r in requests), 'source_reads': len(reads),
            'exact_material_rows': len(rows), 'table_cache': o.table_bindings.stats,
            'requests': requests, 'real_model_calls': 0}


async def main():
    baseline = await measure(False)
    candidate = await measure(True)
    report = {'workload': '20 model-selected regular-table sources, identical semantics, changing IDs/values',
              'baseline_without_template': baseline, 'candidate_including_template_learning': candidate,
              'platform_seconds': None, 'metered_tokens': None,
              'limitation': 'Synthetic structured-source regression, not a full-run or model-quality benchmark'}
    Path(sys.argv[2]).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({key: value for key, value in report.items() if key not in
        ('baseline_without_template', 'candidate_including_template_learning')}))
    print(json.dumps({'baseline': {k:v for k,v in baseline.items() if k != 'requests'},
                      'candidate': {k:v for k,v in candidate.items() if k != 'requests'}}))


if __name__ == '__main__':
    asyncio.run(main())

"""Optional hosted choice routing for small observed navigation menus."""
import asyncio
import json
import os
from pathlib import Path
import re
import time

import httpx

from ..log import log
from ..profile import span, model_usage
from .records import Decision

_slots = asyncio.Semaphore(4)
_client = None


def enabled():
    if os.environ.get('RFQ_CLEF_ROUTE'):
        return os.environ['RFQ_CLEF_ROUTE'] == '1'
    try:
        return json.loads((Path(__file__).parents[2] / 'routing.json').read_text()).get('clef', False) is True
    except (OSError, ValueError):
        return False


async def choose(snapshot, mission, checklist):
    if not enabled() or mission.name in ('submit', 'tan') or snapshot.tables:
        return None
    # Restrict the cheap router to small menus. Interpretation and every write
    # stay in the fused Qwen decision; no route is selected by this filter.
    content = '\n'.join(line for line in snapshot.text.splitlines() if '[e' not in line and '[f' not in line)
    if len(content) > 1200 or re.search(r'\d{4}[-/]\d{2}|[%€$]|\bTAN\b', content):
        return None
    links = [(ref, line) for ref, line in snapshot.controls.items() if '] link ' in line]
    if not 1 <= len(links) <= 8 or not os.environ.get('CLEF_BASE_URL'):
        return None
    criteria = {f'c{i}': line for i, (_, line) in enumerate(links)}
    criteria['abstain'] = 'Need facts, typing, interpretation, ambiguous controls, or no useful navigation target'
    payload = {'state': {'mission': mission.goal, 'gaps': checklist, 'page': snapshot.view(2400)},
               'questions': {'next': {'type': 'choice', 'instructions': 'Choose one observed navigation link advancing the mission; otherwise abstain.', 'criteria': criteria}}}
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    try:
        async with _slots:
            with span('clef_choice', agent='router', category='model', characters=len(json.dumps(payload))) as request:
                try:
                    response = await _client.post(os.environ['CLEF_BASE_URL'].rstrip('/') + '/v1/decide', json=payload,
                                                  headers={'Authorization': 'Bearer ' + os.environ['CLEF_API_KEY']},
                                                  timeout=max(.1, min(4, mission.deadline - time.time() - 2)))
                    response.raise_for_status()
                    body = response.json()
                except BaseException:
                    model_usage(attempt_id=request.id, model='clef', known=False)
                    raise
                usage = body.get('usage') or {}
                input_tokens = usage.get('input_tokens', usage.get('prompt_tokens'))
                output_tokens = usage.get('output_tokens', usage.get('completion_tokens', 0))
                model_usage(attempt_id=request.id, model='clef', input_tokens=input_tokens, output_tokens=output_tokens,
                            cached_tokens=usage.get('cached_tokens', 0), known=isinstance(input_tokens, int))
        result = body.get('answers', body.get('results', body.get('result', body)))
        choice = result.get('next') if isinstance(result, dict) else None
        if isinstance(choice, dict):
            choice = choice.get('choice', choice.get('value'))
        if choice not in criteria or choice == 'abstain':
            log('clef_abstain', valid=choice in criteria)
            return None
        ref = links[int(choice[1:])][0]
        log('clef_choice', ref=ref)
        return Decision(snapshot.id, actions=[('click', {'ref': ref})])
    except Exception as error:
        log('clef_fallback', error=type(error).__name__)
        return None

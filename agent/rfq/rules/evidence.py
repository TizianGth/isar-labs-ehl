"""Eligibility and master-record merging shared by collection and resolution."""
from . import parse


def mail_problem(message, reference, cutoff, local='', domain=None):
    if parse.norm_id(message.get('reference')) != parse.norm_id(reference):
        return 'other reference'
    if message.get('is_draft_or_preview'):
        return 'draft/preview'
    at = parse.when(message.get('datetime_utc'))
    if at is None or (cutoff is not None and at > cutoff):
        return 'after the scenario date' if at else 'no readable date'
    sender = parse.mail_address(message.get('sender'))
    if (local and not sender.startswith(local.lower() + '@')) or (domain and not sender.endswith('@' + domain.lower())):
        return 'not from the authorized sender'
    return None


def merge_materials(items):
    merged = {}
    for item in items:
        for material in item['data'].get('materials') or []:
            num = parse.norm_id(material.get('material_number'))
            if not num:
                continue
            target = merged.setdefault(num, {'material_number': parse.clean(material['material_number']), 'conditions': []})
            for key, value in material.items():
                if key == 'conditions':
                    for condition in value or []:
                        if condition.get('scales') and condition not in target['conditions']:
                            target['conditions'].append(condition)
                elif key in ('status_code', 'status_meaning', 'status_scope'):
                    continue
                elif value not in (None, '', []):
                    target[key] = value
            status = material.get('status_meaning')
            scope = material.get('status_scope') or 'material'
            if status and scope == 'material':
                statuses = target.setdefault('_statuses', {})
                statuses[status] = {'code': material.get('status_code'), 'url': item.get('url', '')}
                if target.get('_verified') and material.get('status_verified') is not True:
                    continue
                if len(statuses) == 1:
                    target['status_meaning'] = status
                    target['status_code'] = material.get('status_code')
                else:
                    # A stock/inspection status must never overwrite master status.
                    # Conflicting master records require a focused source recheck.
                    target['status_meaning'] = None
                    target['_status_conflict'] = dict(statuses)
            if material.get('status_verified') is True and status and scope == 'material':
                target['status_meaning'] = status
                target['_verified'] = True
                target.pop('_status_conflict', None)
                target['_statuses'] = {status: {'code': material.get('status_code'), 'url': item.get('url', '')}}
    return merged

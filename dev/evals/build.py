"""Build eight immutable, private synthetic worlds and independent goldens."""
import copy
import hashlib
import json
import random
import re
import shutil
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'kernel'))
from variant_server import load_cases

WORLDS = [
    ('dev-classic', 'classic', 'baseline', 701, None),
    ('dev-locale', 'workbench', 'locale-boundary', 702, None),
    ('holdout-withdrawal', 'legacy', 'future-withdrawal', 1701, None),
    ('holdout-stock', 'busy-office', 'shared-stock', 1702, None),
    ('holdout-scanned', 'scanned', 'baseline', 1703, None),
    ('holdout-blocked', 'modern', 'blocked', 1704, None),
    ('holdout-documents', 'modern', 'locale-boundary', 1705, 'documents'),
    ('holdout-search', 'modern', 'shared-stock', 1706, 'search'),
]


def number(value, locale='en-US'):
    s = str(value).strip()
    if locale == 'de-DE':
        s = s.replace('.', '').replace(',', '.')
    else:
        s = s.replace(',', '')
    return Decimal(s)


def rounded(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def oracle(case):
    """Separate arithmetic for this synthetic world grammar; no agent imports.

    Resolve dated withdrawals/quantities, exact technical matches, tier units,
    FX, two discount scopes, setup, carton rounding and shared physical stock.
    This grammar is deliberately narrower than all challenge business rules.
    """
    at = case['scenario_datetime_utc']
    qa = [q for q in case['qa'] if q['datetime_utc'] <= at]
    withdrawn = {m[1] for q in qa for m in re.finditer(r'Answer\s+(\d+)\s+is withdrawn', q['text'])}
    quantities = {p['position']: Decimal(str(p['quantity'])) for p in case['positions']}
    assignments, setups, tariffs = {}, {}, {}
    for q in sorted(qa, key=lambda q: q['datetime_utc']):
        if q['number'] in withdrawn:
            continue
        for m in re.finditer(r'Position ([\w-]+) is changed to (\d+) pieces', q['text']):
            quantities[m[1]] = Decimal(m[2])
        for sentence in q['text'].split('. '):
            m = re.search(r'Position ([\w-]+) uses condition group ([\w-]+)', sentence)
            if m:
                assignments[m[1]] = m[2]
                setup = re.search(r'setup cost EUR ([\d.]+)', sentence)
                tariff = re.search(r'packaging tariff ([\w-]+)', sentence)
                setups[m[1]] = Decimal(setup[1]) if setup else Decimal(0)
                tariffs[m[1]] = tariff[1].rstrip('.') if tariff else None
    messages = {m['id']: m for m in case['messages'] if m['datetime_utc'] <= at}
    discount = Decimal(re.search(r'discount of ([\d.]+)', messages['approval']['body'])[1])
    packing = {m[1]: Decimal(m[2]) for m in re.finditer(
        r'([\w-]+) costs EUR ([\d.]+) per started carton', messages['packing']['body'])}
    fx = {m[1]: Decimal(m[2]) for m in re.finditer(r'fixes ([A-Z]+) at EUR ([\d.]+)', messages['fx']['body'])}
    index = Decimal(0)
    for m in re.finditer(r'EUR ([\d.]+) per kg from ([\d-]+) (?:through|to) ([\d-]+)', messages['index']['body']):
        if m[2] <= at[:10] <= m[3]:
            index = Decimal(m[1])
    rows, demand = [], defaultdict(Decimal)
    requirements = {r['ref']: r for r in case['requirements']}
    for p in case['positions']:
        r = requirements[p['requirement_ref']]
        matches = [m for m in case['materials'] if m['status_meaning'] == 'active' and all(
            str(m.get(k)).casefold() == str(r.get(k)).casefold()
            for k in ('bore_mm', 'stroke_mm', 'pressure_bar', 'mounting', 'certification', 'catalog_group'))]
        if not matches:
            return {'tender_reference': case['reference'], 'decision': 'no_bid', 'currency': 'EUR',
                    'discount_percent': 0, 'positions': [], 'total_net': 0}
        material = matches[0]
        qty = quantities[p['position']] * (Decimal(str(r['pieces_per_set'])) if p['unit'] == 'Satz' else 1)
        conditions = [c for c in material['conditions'] if c['valid_from'] <= at[:10] <= c['valid_to']]
        condition = max(conditions, key=lambda c: c['valid_from'])
        tier = max((t for t in condition['scales'] if number(t['from_qty']) <= qty), key=lambda t: number(t['from_qty']))
        base = rounded(number(tier['price'], condition['number_format']) / number(condition['price_unit_qty']) *
                       (fx[condition['currency']] if condition['currency'] != 'EUR' else 1))
        mass = number(material['surcharge_mass_g']) / 1000 * index
        setup = setups.get(p['position'], Decimal(0)) / qty
        tariff = tariffs.get(p['position'])
        cartons = (qty / number(material['pieces_per_carton'])).to_integral_value(rounding=ROUND_CEILING)
        pack = cartons * packing.get(tariff, Decimal(0)) / qty
        scope = assignments.get(p['position'])
        both = bool(scope and re.search(re.escape(scope) + r' discounts both', messages['clause']['body']))
        unit = rounded(base * (1 - discount / 100) + mass * (1 - discount / 100 if both else 1) + setup + pack)
        row = {'position': p['position'], 'material_number': material['material_number'], 'quantity': int(qty),
               'base_unit_price': float(base), 'surcharge_per_unit': float(rounded(mass + setup)),
               'packaging_per_unit': float(rounded(pack)), 'rebate_total': 0, 'unit_price': float(unit)}
        rows.append((row, material))
        demand[material['material_number']] += qty
    result = []
    for row, material in rows:
        field = 'lead_time_stock_weeks' if demand[material['material_number']] <= number(material['unrestricted_stock']) \
            else 'lead_time_production_weeks'
        result.append({**row, 'lead_time_weeks': int(material[field])})
    total = sum(Decimal(str(r['unit_price'])) * r['quantity'] for r in result)
    return {'tender_reference': case['reference'], 'decision': 'bid', 'currency': 'EUR',
            'discount_percent': float(discount), 'positions': result, 'total_net': float(rounded(total))}


def counterfactual(case, seed, extra_position=False):
    case, rng = copy.deepcopy(case), random.Random(seed)
    ids = {case['reference']: f'SYN-RFQ-{seed}', case['company']: f'Synthetic Engineering {seed}',
           case['vat_id']: f'TEST-VAT-{seed}', 'boreal.example': f'world{seed}.example'}
    for m in case['materials']:
        ids[m['material_number']] = f'ITEM-{seed}-{len(ids)}'
        ids.setdefault(m['catalog_group'], f'GROUP-{seed}-{len(ids)}')
    for p in case['positions']:
        ids[p['position']] = f'LINE-{seed}-{len(ids)}'
    for old in ('CLAUSE-A', 'CLAUSE-B', 'PACK-A', 'PACK-B', 'FX-USD', 'FX-GBP', 'INDEX-A'):
        ids[old] = f'KEY-{seed}-{len(ids)}'
    def rewrite(value):
        if isinstance(value, str):
            for old, new in sorted(ids.items(), key=lambda pair: -len(pair[0])):
                value = value.replace(old, new)
            return value.replace('2044-', f'{2050 + seed % 10}-')
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        return value
    case = rewrite(case)
    factor = Decimal(rng.choice(['0.83', '1.07', '1.19', '1.31']))
    for m in case['materials']:
        for c in m['conditions']:
            for tier in c['scales']:
                amount = rounded(number(tier['price'], c['number_format']) * factor)
                text = f'{amount:,.2f}'
                tier['price'] = text.replace(',', '_').replace('.', ',').replace('_', '.') if c['number_format'] == 'de-DE' else text
        m['lead_time_production_weeks'] = str(int(m['lead_time_production_weeks']) + seed % 3)
    if extra_position:
        p = copy.deepcopy(case['positions'][0])
        p['position'], p['quantity'] = f'LINE-{seed}-EXTRA', str(rng.randrange(3, 8))
        case['positions'].append(p)
        q = next(q for q in case['qa'] if q['number'] == '5')
        q['text'] += f" Position {p['position']} uses condition group {ids['CLAUSE-A']} and packaging tariff {ids['PACK-A']}."
    rng.shuffle(case['positions'])
    rng.shuffle(case['materials'])
    rng.shuffle(case['messages'])
    return case


def build(destination=None):
    destination = Path(destination or ROOT / 'bundles')
    cases, manifest = load_cases(), []
    # Verify independent calculations against the previously hand-derived data
    # before applying counterfactual transformations.
    from scoring import expected_answer
    for name, case in cases.items():
        assert oracle(case) == expected_answer(name), (name, oracle(case), expected_answer(name))
    for slug, profile, name, seed, topology in WORLDS:
        folder = destination / slug
        folder.mkdir(parents=True, exist_ok=True)
        data = counterfactual(cases[name], seed, extra_position=topology is not None)
        config = {'profile': profile, 'case': name, 'seed': seed, 'topology': topology, 'data': data}
        if topology == 'documents':
            config['fault'] = 'delayed-commit'
        for source in (ROOT / 'kernel').glob('*.py'):
            shutil.copy2(source, folder / source.name)
        shutil.copy2(ROOT / 'kernel' / 'cases.json', folder / 'cases.json')
        shutil.copy2(ROOT / 'world.py', folder / 'world.py')
        (folder / 'world.json').write_text(json.dumps(config, indent=2))
        (folder / 'expected.json').write_text(json.dumps(oracle(data), indent=2))
        (folder / 'atira-eval.json').write_text(json.dumps({'start': 'python3 world.py', 'world': 'shared',
            'sites': [{'name': role, 'port': 3100 + i} for i, role in enumerate(('tender', 'specification', 'erp', 'mail'))]}, indent=2))
        authorized = ', '.join(f'{topic}: {local}' for topic, local in data['authorized'].items())
        brief = (f"Respond to tender ({data['reference']}) for {data['company']}, VAT {data['vat_id']}. "
                 f"Scenario cutoff: {data['scenario_datetime_utc']}. Sites are named tender, specification, erp, mail. "
                 f"The director ({data['director']}) approves discounts. Authorized sender localparts: {authorized}. "
                 "Find all current dated records from the sites, calculate using the RFQ business rules, and enter "
                 "one main bid or justified no-bid in the tender portal. Report quantities as pieces, all money as "
                 "net EUR. Request a new signing TAN through the form, retrieve it from webmail, and sign. "
                 "Commercial records may be filed on more than one site; follow the visible directories.\n")
        (folder / 'BRIEF.md').write_text(brief)
        (folder / '.gitignore').write_text('.atira/\n__pycache__/\n')
        digest = hashlib.sha256((folder / 'world.json').read_bytes() + (folder / 'expected.json').read_bytes()).hexdigest()
        manifest.append({'slug': slug, 'seed': seed, 'profile': profile, 'case': name, 'topology': topology,
                         'split': 'development' if slug.startswith('dev-') else 'holdout', 'data_sha256': digest})
    (ROOT / 'suite.json').write_text(json.dumps({'version': 1, 'worlds': manifest}, indent=2))
    return manifest


if __name__ == '__main__':
    print(json.dumps(build(), indent=2))

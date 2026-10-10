"""Additional invented RFQ cases and manually derived developer goldens.

Only ``load_cases`` provides browser fixture data. ``expected_answer`` belongs to
the local evaluator; no answers are stored in fixture dictionaries or HTTP pages.
Neither function imports the agent, its resolver, nor another evaluator.
"""

from copy import deepcopy
import json
from pathlib import Path


EXTRA_CASE_NAMES = ('locale-boundary', 'shared-stock')


def _new_case(baseline, name, reference):
    old_reference = baseline['reference']

    def rewrite(value):
        if isinstance(value, str):
            # Deliberately retains the suffix on the wrong-reference decoy.
            return value.replace(old_reference, reference)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return deepcopy(value)

    case = rewrite(baseline)
    case['name'] = name
    return case


def _locale_boundary(baseline):
    case = _new_case(baseline, 'locale-boundary', 'SYN-RFQ-5101')
    case['requirements'][1]['pieces_per_set'] = 4
    for answer in case['qa']:
        if answer['number'] == '2':
            answer['text'] = 'Position X-01 is changed to 14 pieces.'
        elif answer['number'] == '5':
            answer['text'] = answer['text'].replace('EUR 24.00', 'EUR 33.00')
    ax, bx = case['materials'][:2]
    ax.update(surcharge_mass_g='275', unrestricted_stock='14')
    ax['conditions'][0].update(price_unit_qty='100', scales=[
        {'from_qty': '1', 'price': '12.500,00'},
        {'from_qty': '14', 'price': '9.875,00'},
    ])
    bx['unrestricted_stock'] = '7'
    bx['conditions'][0].update(currency='GBP', price_unit_qty='100', fx_key='FX-GBP', scales=[
        {'from_qty': '1', 'price': '12,300.00'},
        {'from_qty': '8', 'price': '10,050.00'},
    ])
    for mail in case['messages']:
        if mail['id'] == 'approval':
            mail['body'] = ('For SYN-RFQ-5101 I approve an order discount of 7.5 percent. '
                            'No minimum order value condition applies.')
        elif mail['id'] == 'packing':
            mail['body'] = ('For SYN-RFQ-5101, PACK-A costs EUR 3.25 per started carton; '
                            'PACK-B costs EUR 6.00 per started carton. Compute cartons separately per position.')
        elif mail['id'] == 'fx':
            mail['body'] = 'For SYN-RFQ-5101, key FX-GBP fixes GBP at EUR 1.18 per 1 GBP.'
        elif mail['id'] == 'index':
            mail['body'] = ('For SYN-RFQ-5101, INDEX-A is EUR 19.00 per kg from 2044-01-01 '
                            'through 2044-05-19 inclusive; EUR 6.40 per kg from 2044-05-20 '
                            'through 2044-12-31 inclusive. Only the window containing the scenario date applies.')
    return case


def _shared_stock(baseline):
    case = _new_case(baseline, 'shared-stock', 'SYN-RFQ-5102')
    first_req, second_req = case['requirements']
    for key in ('bore_mm', 'stroke_mm', 'pressure_bar', 'mounting', 'certification', 'catalog_group'):
        second_req[key] = first_req[key]
    case['materials'][0]['unrestricted_stock'] = '14'
    # Commercial condition groups and packaging tariffs remain per position.
    # A dated authorised logistics mail makes material-level stock aggregation
    # explicit, avoiding a hidden rule that only the evaluator would know.
    packing = next(mail for mail in case['messages'] if mail['id'] == 'packing')
    packing['body'] += (
        ' Stock is one shared physical quantity per material number across all positions of this RFQ. '
        'Do not allocate the same available units to multiple positions. If the combined ordered '
        'quantity of a material exceeds its freely available stock, use the new-production lead '
        'time for every position ordering that material. Price quantity tiers and carton counts '
        'are still evaluated separately for each position, not using combined demand.')
    return case


def load_cases():
    """Load unchanged original fixtures plus independent copies of two new cases."""
    cases = json.loads((Path(__file__).resolve().parent / 'cases.json').read_text(encoding='utf-8'))
    baseline = cases['baseline']
    cases['locale-boundary'] = _locale_boundary(baseline)
    cases['shared-stock'] = _shared_stock(baseline)
    return cases


def expected_answer(case_name):
    """Return explicit manual gold for a new case; originals keep their evaluator.

    Locale X-01: 9875/100=98.75; .275kg*6.40=1.76; setup33/14;
    3 cartons*3.25/14; r2((98.75+1.76)*.925+33/14+9.75/14)=96.03.
    Locale X-02: 2 sets*4=8; 10050/100*1.18=118.59;
    r2(118.59*.925+2*6/8)=111.20; total14*96.03+8*111.20=2234.02.
    Shared X-01 remains baseline (87.08). X-02 uses same material:
    r2(110.50*.92+1.80+2*5/6)=105.13. Combined18>14 -> bothlead8.
    Total12*87.08+6*105.13=1675.74.
    """
    if case_name == 'locale-boundary':
        return {'tender_reference': 'SYN-RFQ-5101', 'decision': 'bid', 'currency': 'EUR',
                'discount_percent': 7.5, 'positions': [
                    {'position': 'X-01', 'material_number': 'SYN-MAT-AX', 'quantity': 14,
                     'base_unit_price': 98.75, 'surcharge_per_unit': 4.12, 'packaging_per_unit': .70,
                     'unit_price': 96.03, 'rebate_total': 0, 'lead_time_weeks': 2},
                    {'position': 'X-02', 'material_number': 'SYN-MAT-BX', 'quantity': 8,
                     'base_unit_price': 118.59, 'surcharge_per_unit': 0, 'packaging_per_unit': 1.50,
                     'unit_price': 111.20, 'rebate_total': 0, 'lead_time_weeks': 11},
                ], 'total_net': 2234.02}
    if case_name == 'shared-stock':
        return {'tender_reference': 'SYN-RFQ-5102', 'decision': 'bid', 'currency': 'EUR',
                'discount_percent': 8, 'positions': [
                    {'position': 'X-01', 'material_number': 'SYN-MAT-AX', 'quantity': 12,
                     'base_unit_price': 90, 'surcharge_per_unit': 3.80, 'packaging_per_unit': .63,
                     'unit_price': 87.08, 'rebate_total': 0, 'lead_time_weeks': 8},
                    {'position': 'X-02', 'material_number': 'SYN-MAT-AX', 'quantity': 6,
                     'base_unit_price': 110.50, 'surcharge_per_unit': 1.80, 'packaging_per_unit': 1.67,
                     'unit_price': 105.13, 'rebate_total': 0, 'lead_time_weeks': 8},
                ], 'total_net': 1675.74}
    raise ValueError('Unknown additional synthetic case: ' + str(case_name))

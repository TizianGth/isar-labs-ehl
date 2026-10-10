"""Independent Decimal checks and fixture isolation checks; no agent imports."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import unittest

from extra_cases import expected_answer, load_cases


def D(value):
    return Decimal(str(value))


def cents(value):
    return D(value).quantize(D('.01'), rounding=ROUND_HALF_UP)


class ExtraCasesTests(unittest.TestCase):
    def test_originals_are_unchanged_and_no_gold_is_in_fixture(self):
        originals = json.loads((Path(__file__).resolve().parent / 'cases.json').read_text())
        cases = load_cases()
        for name, original in originals.items():
            self.assertEqual(cases[name], original)
        self.assertEqual(set(cases) - set(originals), {'locale-boundary', 'shared-stock'})
        for case in cases.values():
            self.assertFalse({'expected', 'expected_answer', 'gold', 'score', 'total_net'} & set(case))

    def test_copies_do_not_alias_each_other_or_later_loads(self):
        cases = load_cases()
        cases['locale-boundary']['materials'][0]['conditions'][0]['scales'][0]['price'] = 'INVALID'
        cases['shared-stock']['messages'][0]['body'] = 'INVALID'
        self.assertEqual(cases['baseline']['materials'][0]['conditions'][0]['scales'][0]['price'], '110,50')
        self.assertEqual(load_cases()['locale-boundary']['materials'][0]['conditions'][0]['scales'][0]['price'], '12.500,00')
        self.assertNotEqual(load_cases()['shared-stock']['messages'][0]['body'], 'INVALID')

    def test_new_references_preserve_future_and_wrong_reference_decoys(self):
        for name in ('locale-boundary', 'shared-stock'):
            case = load_cases()[name]
            mails = {mail['id']: mail for mail in case['messages']}
            for mail in mails.values():
                self.assertNotIn('SYN-RFQ-4401', mail['subject'] + mail['body'])
            self.assertIn(case['reference'] + '-OTHER', mails['other']['subject'])
            self.assertIn(case['reference'] + '-OTHER', mails['other']['body'])
            parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
            self.assertLess(parse(mails['approval']['datetime_utc']), parse(case['scenario_datetime_utc']))
            self.assertGreater(parse(mails['future']['datetime_utc']), parse(case['scenario_datetime_utc']))
            self.assertEqual([r['ref'] for r in case['requirements']], ['REQ-A', 'REQ-B'])

    def test_locale_gold_decimal_calculation(self):
        gold = expected_answer('locale-boundary')
        x1, x2 = gold['positions']
        base1 = D('9875.00') / 100
        mass = D('.275') * D('6.40')
        setup = D(33) / 14
        packaging1 = D(3) * D('3.25') / 14
        unit1 = cents((base1 + mass) * D('.925') + setup + packaging1)
        base2 = D('10050.00') / 100 * D('1.18')
        packaging2 = D(2) * D(6) / 8
        unit2 = cents(base2 * D('.925') + packaging2)
        self.assertEqual(D(x1['base_unit_price']), base1)
        self.assertEqual(D(x1['surcharge_per_unit']), cents(mass + setup))
        self.assertEqual(D(x1['packaging_per_unit']), cents(packaging1))
        self.assertEqual(D(x1['unit_price']), unit1)
        self.assertEqual(D(x2['base_unit_price']), base2)
        self.assertEqual(D(x2['unit_price']), unit2)
        self.assertEqual(D(x2['packaging_per_unit']), packaging2)
        self.assertEqual(D(gold['total_net']), 14 * unit1 + 8 * unit2)

    def test_locale_fixture_thresholds_are_exact_and_stock_boundary_visible(self):
        case = load_cases()['locale-boundary']
        ax, bx = case['materials'][:2]
        self.assertEqual(ax['conditions'][0]['number_format'], 'de-DE')
        self.assertEqual(bx['conditions'][0]['number_format'], 'en-US')
        self.assertEqual(ax['conditions'][0]['scales'][1]['from_qty'], '14')
        self.assertEqual(bx['conditions'][0]['scales'][1]['from_qty'], '8')
        self.assertEqual(ax['unrestricted_stock'], '14')
        self.assertEqual(bx['unrestricted_stock'], '7')
        self.assertEqual(case['requirements'][1]['pieces_per_set'], 4)
        self.assertIn('7.5 percent', next(m['body'] for m in case['messages'] if m['id'] == 'approval'))

    def test_shared_gold_decimal_and_shared_stock(self):
        case = load_cases()['shared-stock']
        gold = expected_answer('shared-stock')
        x1, x2 = gold['positions']
        unit1 = cents((D(90) + D('1.8')) * D('.92') + D(24) / 12 + D('7.5') / 12)
        unit2 = cents(D('110.50') * D('.92') + D('1.8') + D(10) / 6)
        self.assertEqual(D(x1['unit_price']), unit1)
        self.assertEqual(D(x2['unit_price']), unit2)
        self.assertEqual(D(gold['total_net']), 12 * unit1 + 6 * unit2)
        self.assertEqual([r['material_number'] for r in gold['positions']], ['SYN-MAT-AX'] * 2)
        self.assertEqual([r['lead_time_weeks'] for r in gold['positions']], [8, 8])
        self.assertLess(D(case['materials'][0]['unrestricted_stock']), D(x1['quantity'] + x2['quantity']))
        req_a, req_b = case['requirements']
        for key in ('bore_mm', 'stroke_mm', 'pressure_bar', 'mounting', 'certification', 'catalog_group'):
            self.assertEqual(req_a[key], req_b[key])
        mail = next(m['body'] for m in case['messages'] if m['id'] == 'packing')
        self.assertIn('combined ordered quantity', mail)
        self.assertIn('separately for each position', mail)

    def test_gold_is_fresh_and_unknown_name_is_explicit_error(self):
        expected_answer('locale-boundary')['positions'][0]['quantity'] = 999
        self.assertEqual(expected_answer('locale-boundary')['positions'][0]['quantity'], 14)
        with self.assertRaises(ValueError):
            expected_answer('baseline')


if __name__ == '__main__':
    unittest.main()

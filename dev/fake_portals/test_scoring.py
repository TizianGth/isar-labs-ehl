import copy
import unittest
from decimal import Decimal, ROUND_HALF_UP

from scoring import evaluate_answer, evaluate_state, expected_answer


class ScoringTests(unittest.TestCase):
    def test_manual_goldens_match_independent_decimal_arithmetic(self):
        d, r2 = Decimal, lambda n: n.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        p1 = r2((d('90') + d('.4') * d('4.5')) * d('.92') + d(24) / 12 + d('7.5') / 12)
        p2 = r2(d(1100) / 10 * d('.8') * d('.92') + d(10) / 6)
        self.assertEqual((p1, p2, p1 * 12 + p2 * 6), (d('87.08'), d('82.63'), d('1540.74')))

    def test_goldens_receive_full_score(self):
        for name in ('baseline', 'blocked', 'future-withdrawal'):
            with self.subTest(case=name):
                self.assertTrue(evaluate_answer(expected_answer(name), name)['perfect'])
                self.assertEqual(evaluate_answer(expected_answer(name), name)['quality'], 100)

    def test_wrong_material_gets_field_level_failure(self):
        answer = expected_answer('baseline')
        answer['positions'][0]['material_number'] = 'SYN-MAT-DECOY'
        result = evaluate_answer(answer, 'baseline')
        self.assertEqual(result['quality'], 93.5)
        self.assertEqual([c['field'] for c in result['checks'] if not c['ok']], ['X-01.material_number'])

    def test_answer_is_not_a_receipt(self):
        self.assertEqual(evaluate_state({'submitted': expected_answer('baseline'), 'receipt': None}, 'baseline')['quality'], 0)
        self.assertEqual(evaluate_state({'submitted': None, 'receipt': 'fake'}, 'baseline')['quality'], 0)
        self.assertEqual(evaluate_state({'submitted': expected_answer('baseline'), 'receipt': 'SYN-R-1'}, 'baseline')['quality'], 100)

    def test_omitted_position_preserves_other_completed_field_credit(self):
        answer = expected_answer('baseline')
        answer['positions'] = answer['positions'][:1]
        answer['total_net'] = 12 * 87.08
        report = evaluate_answer(answer, 'baseline')
        fields = {c['field']: c for c in report['checks']}
        self.assertTrue(fields['X-01.material_number']['ok'])
        self.assertEqual(fields['X-01.material_number']['actual'], 'SYN-MAT-AX')
        self.assertFalse(fields['X-02.material_number']['ok'])
        self.assertGreater(report['quality'], 32.5)
        self.assertFalse(report['perfect'])

    def test_wrong_reference_is_ineligible(self):
        answer = expected_answer('baseline'); answer['tender_reference'] += '-OTHER'
        self.assertFalse(evaluate_answer(answer, 'baseline')['eligible'])

    def test_duplicates_and_extras_cannot_get_perfect_score(self):
        for pid in ('X-01', 'OTHER'):
            answer = expected_answer('baseline')
            answer['positions'].append({**answer['positions'][0], 'position': pid})
            result = evaluate_answer(answer, 'baseline')
            self.assertFalse(result['perfect']); self.assertLess(result['quality'], 100)

    def test_invalid_values_are_not_zero_or_crashes(self):
        for value in (True, 'NaN', 'Infinity', None, 'not a number'):
            answer = expected_answer('baseline'); answer['positions'][0]['unit_price'] = value
            result = evaluate_answer(answer, 'baseline')
            self.assertFalse(result['perfect'])

    def test_fractional_quantity_does_not_pass(self):
        answer = expected_answer('baseline'); answer['positions'][0]['quantity'] = 12.001
        self.assertFalse(evaluate_answer(answer, 'baseline')['perfect'])

    def test_malformed_position_identifiers_do_not_crash(self):
        for value in (None, {}, [], 1):
            answer = expected_answer('baseline'); answer['positions'][0]['position'] = value
            self.assertFalse(evaluate_answer(answer, 'baseline')['perfect'])

    def test_cent_mismatch_is_detected(self):
        answer = expected_answer('baseline'); answer['positions'][0]['unit_price'] += .01
        self.assertFalse(evaluate_answer(answer, 'baseline')['perfect'])

    def test_reports_are_independent(self):
        a = expected_answer('baseline'); b = copy.deepcopy(a)
        evaluate_answer(a, 'baseline'); self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()

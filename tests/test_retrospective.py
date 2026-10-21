import json
import tempfile
import unittest
from pathlib import Path

from backend.prediction.retrospective import (COLUMNS, load_rows, property_group, qualify,
                                             recorded_money, row_features, evaluate_task, rule_benchmarks)


def row(**changes):
    return {'Tracking Number': '100', 'Status': 'Posted', 'Approved Benefit Amount': '$1,000.00',
            'Max Benefit': '$1,500.00', 'Amount of Claim': '$900.00', 'Monthly Rent': '$800.00',
            'Lease Street Address': '1 Main St.', 'Lease City': 'Boston', 'Lease State': 'MA',
            'Policy': 'policy', **changes}


class RetrospectiveTests(unittest.TestCase):
    def test_labels_are_status_based_and_amount_targets_stay_separate(self):
        item, reason = qualify(row(**{'Approved Benefit Amount': None}))
        self.assertIsNone(reason)
        self.assertEqual(item['decision'], 'approved')
        self.assertEqual(item['ceiling_reason'], 'ceiling_missing')
        item, reason = qualify(row(**{'Approved Benefit Amount': '$0.00'}))
        self.assertEqual(item['ceiling_reason'], 'approved_zero_ceiling_requires_review')
        for changes, expected in (
                ({'Status': 'New'}, 'nonfinal_or_unknown_status'),
                ({'Status': 'Declined'}, 'decline_with_positive_ceiling'),
                ({'Policy': 'Test Claim Submission'}, 'test_context'),
                ({'Tracking Number': '00053'}, 'test_context')):
            self.assertEqual(qualify(row(**changes))[1], expected)
        item, reason = qualify(row(**{'Status': 'Declined', 'Approved Benefit Amount': '0'}))
        self.assertIsNone(reason)
        self.assertEqual(item['decision'], 'declined')

    def test_no_outcome_or_identity_fields_become_features(self):
        original = row_features(row())
        modified = row_features(row(**{'Status': 'Declined', 'Approved Benefit Amount': '$9999',
                                      'Tracking Number': '999', 'Policy': 'answer',
                                      'PM Explanation': 'decline reason', 'Approval Date': '01/01/25',
                                      'Lease Street Address': 'another place'}))
        self.assertEqual(original, modified)
        self.assertNotIn('limit', row_features(row(), without_limit=True))

    def test_accounting_hypotheses_do_not_quarantine_ceiling_above_request(self):
        item, reason = qualify(row())
        self.assertIsNone(reason)
        self.assertIsNone(item['ceiling_reason'])
        item, reason = qualify(row(**{'Max Benefit': '$500'}))
        self.assertIsNone(reason)
        self.assertEqual(item['ceiling_reason'], 'ceiling_above_recorded_limit_requires_review')

    def test_unknowns_remain_distinct_from_zero(self):
        self.assertEqual(recorded_money('0'), (0.0, 'present'))
        self.assertEqual(recorded_money(None), (None, 'missing'))
        for value in ('NaN', '$1,23', '-4', '1.001', 'Infinity'):
            self.assertEqual(recorded_money(value), (None, 'invalid'))
        self.assertEqual(recorded_money('$1,234.50'), (1234.5, 'present'))

    def test_grouping_ignores_punctuation_and_case(self):
        self.assertEqual(property_group(row()), property_group(row(**{'Lease Street Address': '1 MAIN ST'})))
        self.assertIsNone(property_group(row(**{'Lease City': ''})))

    def test_reserved_rows_and_identity_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {'reserved_claim_ids': ['101'], 'development_claims': [{'claim_id': '100', 'row': 2}]}
            header = [COLUMNS.get(i, '') for i in range(31)]
            source = {'header': header, 'ranges': [{'requested_range': 'A2:M2', 'values': [['100']]}]}
            (root / 'reservation.json').write_text(json.dumps(manifest))
            (root / 'development-source.json').write_text(json.dumps(source))
            (root / 'test-context-source.json').write_text(json.dumps({'ranges': []}))
            self.assertEqual(load_rows(root)[0][0]['Tracking Number'], '100')
            source['ranges'][0]['requested_range'] = 'A2:M3'
            (root / 'development-source.json').write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, 'reserved'):
                load_rows(root)
            source['ranges'][0]['requested_range'] = 'A2:M2'
            source['ranges'][0]['values'] = [['00101']]
            (root / 'development-source.json').write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, 'identity'):
                load_rows(root)

    def test_partial_development_split_scores_only_validation(self):
        cohort = []
        for index in range(60):
            item, reason = qualify(row(**{'Tracking Number': str(100 + index),
                                          'Monthly Rent': str(800 + index * 10),
                                          'Status': 'Declined' if index % 2 else 'Posted',
                                          'Approved Benefit Amount': '0' if index % 2 else '1000'}))
            cohort.append(item)
        splits = [(list(range(40)), list(range(40, 60)))]
        for task in ('decision', 'ceiling'):
            result = evaluate_task(cohort, splits, task)
            self.assertEqual(result['evaluation_count'], 20 if task == 'decision' else 10)
            for outputs in result['out_of_fold'].values():
                self.assertTrue({r['claim_id'] for r in outputs} <= {str(i) for i in range(140, 160)})

    def test_rules_and_models_use_matching_amount_cohorts(self):
        first, _ = qualify(row())
        second, _ = qualify(row(**{'Tracking Number': '101', 'Max Benefit': None}))
        result = rule_benchmarks([first, second], {'out_of_fold': {'model': [
            {'claim_id': '100', 'prediction': 1100}, {'claim_id': '101', 'prediction': 5000}]}})
        baseline = result['recorded_limit']
        self.assertEqual(baseline['count'], 1)
        self.assertEqual(baseline['metrics']['mae_dollars'], 500)
        self.assertEqual(baseline['matched_raw_model_metrics']['model']['mae_dollars'], 100)


if __name__ == '__main__':
    unittest.main()

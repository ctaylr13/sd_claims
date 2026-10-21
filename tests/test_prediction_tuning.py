import unittest
from unittest.mock import patch

from backend.prediction.tuning import (calendar_features, evaluate, inner_splits,
                                       run, threshold_for)


def cohort():
    return [{'claim_id': str(100 + i), 'group': str(i // 2),
             'decision': 'declined' if i % 3 == 0 else 'approved',
             'ceiling': float(1000 + 10 * i), 'ceiling_reason': None,
             'durations': {'rent': float(1000 + 10 * i)},
             'calendar': {'rent': float(1000 + 10 * i), 'year': 2024}}
            for i in range(90)]


class PredictionTuningTests(unittest.TestCase):
    def test_threshold_can_improve_decline_detection_without_zero_imputation(self):
        threshold, score = threshold_for(['approved'] * 9 + ['declined'], [.02] * 9 + [.2])
        self.assertEqual(threshold, .2)
        self.assertEqual(score, 1)
        self.assertEqual(threshold_for(['approved', 'declined'], [.01, .01])[0], .5)
        with self.assertRaises(ValueError):
            threshold_for(['approved'], [.2])

    def test_calendar_features_use_only_explicit_input_dates(self):
        row = {'Claim Date': '01/01/24', 'Lease Start Date': None,
               'Approval Date': '01/02/24', 'Status': 'Posted'}
        features = calendar_features(row, {'rent:state=missing': 1})
        self.assertIn('Claim Date:ordinal', features)
        self.assertNotIn('Lease Start Date:ordinal', features)
        self.assertFalse(any('Approval' in key or 'Status' in key for key in features))
        self.assertEqual(features, calendar_features({**row, 'Status': 'Declined',
                                                      'Approval Date': '01/02/25'}, {'rent:state=missing': 1}))

    def test_inner_splits_keep_properties_together(self):
        items = cohort()
        for task in ('decision', 'ceiling'):
            seen = []
            for train, valid in inner_splits(items, task):
                self.assertFalse({items[i]['group'] for i in train} & {items[i]['group'] for i in valid})
                seen.extend(valid)
            self.assertEqual(sorted(seen), list(range(len(items))))

    def test_outer_answers_cannot_change_parameter_or_threshold_selection(self):
        from sklearn.linear_model import LogisticRegression

        items = cohort()
        splits = [(list(range(60)), list(range(60, 90)))]
        original = evaluate(items, splits, 'decision', 'durations', estimators=[LogisticRegression(max_iter=2000)])
        for item in items[60:]:
            item['decision'] = 'approved' if item['decision'] == 'declined' else 'declined'
        changed = evaluate(items, splits, 'decision', 'durations', estimators=[LogisticRegression(max_iter=2000)])
        self.assertEqual(original['folds'][0]['selection']['selected'], changed['folds'][0]['selection']['selected'])
        self.assertEqual([r['prediction'] for r in original['out_of_fold']],
                         [r['prediction'] for r in changed['out_of_fold']])
        self.assertNotEqual(original['metrics']['accuracy'], changed['metrics']['accuracy'])

    def test_outer_property_overlap_is_rejected_before_search(self):
        items = cohort()
        with patch('backend.prediction.tuning.search', side_effect=AssertionError('fit')):
            with self.assertRaisesRegex(ValueError, 'groups overlap'):
                evaluate(items, [(list(range(59)), list(range(59, 90)))], 'decision', 'durations')

    def test_changed_protocol_is_rejected_before_fit(self):
        with patch('backend.prediction.tuning.protocol', return_value={'frozen': True}), patch(
                'backend.prediction.tuning.dataset', side_effect=AssertionError('load')):
            with self.assertRaisesRegex(ValueError, 'protocol'):
                run(None, {'frozen': False})


if __name__ == '__main__':
    unittest.main()

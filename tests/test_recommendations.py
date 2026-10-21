import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError

from backend.prediction.artifacts import ResearchModels, Tree, predict
from backend.prediction.recommendations import (INPUT_COLUMNS, ModelArtifact, ResearchInput,
    code_hashes, evaluate, fit_models, load_model, main, recommend)
from backend.prediction.retrospective import estimators, qualify
from backend.prediction.tuning import pipeline_for
from test_retrospective import row


def examples():
    rows, cohort = [], []
    for index in range(80):
        source = row(**{'Tracking Number': str(1000 + index), 'Monthly Rent': str(500 + index * 15),
                        'Status': 'Posted' if index % 3 else 'Declined',
                        'Approved Benefit Amount': str(300 + index * 10) if index % 3 else '0',
                        'Claim Date': '10/01/24', 'Move-Out Date': '09/01/24',
                        'Lease Start Date': '10/01/23', 'Lease End Date': '09/01/24',
                        'Lease Street Address': f'{index} Main St'})
        rows.append(source)
        cohort.append(qualify(source)[0])
    return rows, cohort


def request(source):
    return ResearchInput(claim_id=source['Tracking Number'],
                         values={key: source.get(key) for key in INPUT_COLUMNS},
                         source_reference='synthetic source row')


class RecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.cohort = examples()
        cls.models = fit_models(cls.cohort, list(range(60)))

    def test_json_model_roundtrip_matches_sklearn_including_unknown_features(self):
        models = ResearchModels.model_validate_json(self.models.model_dump_json())
        decision = pipeline_for(estimators('decision')['shallow_tree'])
        decision.fit([i['features'] for i in self.cohort[:60]], [i['decision'] for i in self.cohort[:60]])
        accepted = [i for i in self.cohort[:60] if i['ceiling_reason'] is None]
        ceiling = estimators('ceiling')['boosted_tree']
        ceiling.fit(decision[:-1].transform([i['features'] for i in accepted]), [i['ceiling'] for i in accepted])
        features = [i['features'] for i in self.cohort] + [{'unseen': 123}]
        expected_decisions = decision.predict(features)
        expected_amounts = ceiling.predict(decision[:-1].transform(features))
        for values, label, amount in zip(features, expected_decisions, expected_amounts):
            predicted_label, predicted_amount = predict(models, values)
            self.assertEqual(predicted_label, label)
            self.assertAlmostEqual(predicted_amount, amount, places=9)

    def test_no_history_or_identifiers_in_inference_inputs(self):
        for column in ('Status', 'Approved Benefit Amount', 'Policy', 'Tracking Number', 'PM Explanation'):
            values = {**request(self.rows[1]).values, column: 'approved'}
            with self.subTest(column=column), self.assertRaisesRegex(ValueError, 'allowlisted'):
                recommend(self.models, request(self.rows[1]).model_copy(update={'values': values}))
        changed = request(self.rows[1]).model_copy(update={'claim_id': '99999'})
        first, second = recommend(self.models, request(self.rows[1])), recommend(self.models, changed)
        self.assertEqual(first['research_decision'], second['research_decision'])
        self.assertEqual(first['conditional_ceiling'], second['conditional_ceiling'])
        with self.assertRaisesRegex(ValueError, 'robustness'):
            recommend(self.models, changed.model_copy(update={'claim_id': '00053'}))

    def test_unknowns_invalid_money_and_dates_withhold_without_imputing_zero(self):
        for column, value, reason in (
                ('Max Benefit', 'NaN', 'limit_invalid'), ('Max Benefit', '0', 'limit_nonpositive'),
                ('Monthly Rent', '-2', 'rent_invalid'), ('Amount of Claim', 'NaN', 'request_invalid'),
                ('Claim Date', 'tomorrow', 'Claim Date:invalid')):
            inputs = request(self.rows[1])
            inputs = inputs.model_copy(update={'values': {**inputs.values, column: value}})
            result = recommend(self.models, inputs)
            self.assertIsNone(result['conditional_ceiling'])
            self.assertIn(reason, result['withholding_reasons'])
            self.assertEqual(result['decision'], 'needs_review')
        inputs = request(self.rows[1])
        result = recommend(self.models, inputs.model_copy(update={'values': {**inputs.values, 'Amount of Claim': '0'}}))
        self.assertEqual(result['ceiling_state'], 'advisory')
        self.assertEqual(result['benchmarks']['minimum_request_limit'], 0)

    def test_missing_optional_amounts_preserve_advisory_without_imputing_values(self):
        inputs = request(self.rows[1])
        result = recommend(self.models, inputs.model_copy(update={'values': {
            **inputs.values, 'Monthly Rent': None, 'Amount of Claim': None}}))
        self.assertEqual(result['ceiling_state'], 'advisory')
        self.assertIsNotNone(result['conditional_ceiling'])
        self.assertIsNone(result['benchmarks']['minimum_request_limit'])
        self.assertIn('rent_missing', result['review_reasons'])
        self.assertIn('request_missing', result['review_reasons'])
        self.assertTrue(all(item['value'] is None for item in result['inputs']
                            if item['column'] in {'Monthly Rent', 'Amount of Claim'}))

    def test_missing_limit_has_explicit_unbounded_advisory(self):
        inputs = request(self.rows[1])
        result = recommend(self.models, inputs.model_copy(update={'values': {**inputs.values, 'Max Benefit': None}}))
        self.assertIsNotNone(result['conditional_ceiling'])
        self.assertEqual(result['ceiling_bound_state'], 'unavailable')
        self.assertIn('limit_missing', result['review_reasons'])
        self.assertIsNone(result['benchmarks']['recorded_limit'])
        self.assertIn('no policy cap', result['ceiling_condition'])

    def test_date_order_is_a_warning_without_repairing_observed_dates(self):
        inputs = request(self.rows[1])
        for column, value, reason in (('Lease End Date', '01/01/23', 'reversed_lease_term'),
                                       ('Claim Date', '01/01/24', 'claim_before_move_out')):
            result = recommend(self.models, inputs.model_copy(update={'values': {**inputs.values, column: value}}))
            self.assertIsNotNone(result['conditional_ceiling'])
            self.assertIn(reason, result['review_reasons'])
            self.assertEqual(next(x['value'] for x in result['inputs'] if x['column'] == column), value)

    def test_all_missing_inputs_do_not_emit_a_generic_estimate(self):
        inputs = request(self.rows[1])
        result = recommend(self.models, inputs.model_copy(update={'values': {}}))
        self.assertIsNone(result['conditional_ceiling'])
        self.assertIn('no_usable_inputs', result['withholding_reasons'])

    def test_bound_is_recorded_limit_not_request_and_citations_remain(self):
        inputs = request(self.rows[1])
        inputs = inputs.model_copy(update={'values': {**inputs.values, 'Max Benefit': '10', 'Amount of Claim': '1'}})
        result = recommend(self.models, inputs)
        self.assertEqual(result['conditional_ceiling'], 10)
        self.assertTrue(result['requires_review'])
        self.assertFalse(result['correctness_verified'])
        self.assertEqual(len(result['inputs']), len(INPUT_COLUMNS))
        self.assertEqual(result['inputs'][0]['source_reference'], 'synthetic source row')

    def test_tree_cycle_and_invalid_artifacts_rejected(self):
        with self.assertRaises(ValidationError):
            Tree(left=(0,), right=(0,), feature=(0,), threshold=(1,), value=(2,))
        with self.assertRaises(ValidationError):
            self.models.model_validate({**self.models.model_dump(), 'scales': [0] * len(self.models.scales)})
        with self.assertRaises(ValidationError):
            self.models.model_validate({**self.models.model_dump(), 'initial_ceiling': float('nan')})

    def test_tree_comparison_preserves_double_threshold_precision(self):
        from backend.prediction.artifacts import _value

        tree = Tree(left=(1, -1, -1), right=(2, -1, -1), feature=(0, -2, -2),
                    threshold=(1.00000006, -2, -2), value=(0, 10, 20))
        self.assertEqual(_value(tree, np.asarray([1.0000001], dtype=np.float32)), 20)

    def test_model_hash_and_code_changes_fail_closed(self):
        from backend.prediction.retrospective import file_hash

        artifact = ModelArtifact(models=self.models, source_hashes={}, code_hashes=code_hashes(),
                                 development_ids=('1000',), reserved_ids=('2000',),
                                 environment={}, training_parameters={})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.json'
            path.write_text(artifact.model_dump_json())
            self.assertEqual(load_model(path, file_hash(path)), artifact)
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                load_model(path, '0' * 64)
            with patch('backend.prediction.recommendations.code_hashes', return_value={}):
                with self.assertRaisesRegex(ValueError, 'implementation changed'):
                    load_model(path, file_hash(path))

    def test_validation_answers_cannot_change_fitted_model(self):
        changed = [dict(item) for item in self.cohort]
        for item in changed[60:]:
            item['decision'], item['ceiling'] = 'approved', 999999
        altered = fit_models(changed, list(range(60)))
        self.assertEqual(altered, self.models)

    def test_cli_rejects_reserved_claim_before_prediction(self):
        from backend.prediction.retrospective import file_hash

        artifact = ModelArtifact(models=self.models, source_hashes={}, code_hashes=code_hashes(),
                                 development_ids=('1000',), reserved_ids=('2000',),
                                 environment={}, training_parameters={})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, inputs, output = root / 'model.json', root / 'input.json', root / 'result.json'
            model.write_text(artifact.model_dump_json())
            inputs.write_text(request(self.rows[1]).model_copy(update={'claim_id': '002000'}).model_dump_json())
            args = ['recommendations', 'recommend', str(model), str(inputs),
                    '--model-sha256', file_hash(model), '--output', str(output)]
            with patch('sys.argv', args), patch('backend.prediction.recommendations.recommend',
                                               side_effect=AssertionError('inference')):
                with self.assertRaisesRegex(ValueError, 'Reserved claim'):
                    main()
            self.assertFalse(output.exists())
        with self.assertRaises(ValidationError):
            ModelArtifact.model_validate({**artifact.model_dump(), 'reserved_ids': ['001000']})

    def test_combined_evaluation_scores_matched_subset_and_records_abstention(self):
        rows = [dict(source) for source in self.rows]
        rows[61]['Max Benefit'] = None
        cohort = [qualify(source)[0] for source in rows]
        previous = {'source_hashes': {'development-source.json': 'a' * 64}}
        reservation = {'reserved_claim_ids': ['9999']}
        splits = [(list(range(40)), list(range(40, 80))), (list(range(40, 80)), list(range(40)))]
        with patch('backend.prediction.recommendations.dataset', return_value=(cohort, splits, previous, reservation)), \
             patch('backend.prediction.recommendations.load_rows', return_value=(rows, reservation)), \
             patch('backend.prediction.recommendations.file_hash', return_value='a' * 64):
            report = evaluate(Path('/unused'))
        json.dumps(report, allow_nan=False)
        self.assertFalse(report['reserved_outcomes_loaded'])
        self.assertEqual(report['coverage']['withheld'], 0)
        counts = [value['count'] for value in report['ceiling_on_same_approved_advisory_subset'].values()]
        self.assertEqual(len(set(counts)), 1)
        self.assertEqual(len(report['out_of_fold']), 80)
        self.assertEqual(report['independent_correctness']['status'], 'not_evaluated')


if __name__ == '__main__':
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from backend.prediction import build_prediction_bundle, save_snapshot
from backend.prediction.baseline import constrain_ceiling, money_value, rules_baseline
from backend.prediction.evaluation import AnswerKey
from backend.prediction.experiment import (DevelopmentClaim, DevelopmentPlan, amount_metrics,
                                           compare, features, load_development)
from backend.prediction.models import FactApproval
from backend.prediction.snapshots import digest
from test_prediction_inputs import SHA, fixture, replace


def entry(identity='100', split='train', group=None):
    return DevelopmentClaim(claim_id=identity, group_id=group or identity, split=split,
                            snapshot_root=Path('/never-read'), snapshot_id=SHA,
                            candidates_path=Path('/never-read'), answer_key_path=Path('/never-read'))


class PredictionExperimentTests(unittest.TestCase):
    def test_reservations_and_group_leaks_rejected_before_io(self):
        for claims in ((entry('00099'),), (entry('00053'),),
                       (entry('100'), entry('0100', 'validation')),
                       (entry('100', group='lease'), entry('101', 'validation', 'lease'))):
            with self.subTest(claims=claims), patch.object(Path, 'read_bytes', side_effect=AssertionError('read')):
                with self.assertRaises(ValidationError):
                    DevelopmentPlan(reserved_claim_ids=('99',), claims=claims)
        with self.assertRaises(ValidationError):
            DevelopmentPlan(reserved_claim_ids=())
        with self.assertRaises(ValidationError):
            entry(split='test')

    def test_review_only_does_not_turn_limits_or_zero_into_approval(self):
        snapshot, fact = fixture(key='policy_limit')
        bundle = build_prediction_bundle(snapshot, (fact,))
        outcome = rules_baseline(bundle)
        self.assertEqual(outcome['decision'], 'needs_review')
        self.assertIsNone(outcome['approved_ceiling'])
        self.assertEqual(str(constrain_ceiling('1500.00', bundle)), '1200.00')
        self.assertIsNone(constrain_ceiling('-1', bundle))
        missing = build_prediction_bundle(snapshot, ())
        self.assertIsNone(constrain_ceiling('10.00', missing))
        self.assertIsNone(rules_baseline(missing)['policy_limit'])
        for value in ('NaN', 'Infinity', '-1', '1.001', '1e9999', None):
            self.assertIsNone(money_value(value))
        self.assertEqual(money_value('0.00'), 0)

    def test_no_identifiers_or_unknown_value_imputation_in_features(self):
        snapshot, fact = fixture()
        bundle = build_prediction_bundle(snapshot, (fact,))
        values = features(bundle)
        self.assertEqual(values['monthly_rent'], 1200)
        self.assertEqual(values['policy_limit:state=unreviewed'], 1)
        self.assertNotIn('policy_limit', values)
        self.assertFalse(any('1241' in key or SHA in key for key in values))

    def test_insufficient_data_never_imports_or_fits_candidates(self):
        plan = DevelopmentPlan(reserved_claim_ids=('99',))
        with patch('backend.prediction.experiment._candidates', side_effect=AssertionError('fit')):
            report = compare(plan)
        self.assertFalse(report['reserved_claims_loaded'])
        self.assertEqual(report['tasks']['decision']['status'], 'insufficient_reviewed_data')
        self.assertEqual(report['automatic_amount_coverage'], 0)

    def test_dollar_errors_include_direction_and_tolerance(self):
        metrics = amount_metrics([100, 100, 100], [110, 150, 60], 20)
        self.assertAlmostEqual(metrics['mae_dollars'], 100 / 3)
        self.assertAlmostEqual(metrics['over_tolerance_rate'], 1 / 3)
        self.assertAlmostEqual(metrics['under_tolerance_rate'], 1 / 3)
        self.assertAlmostEqual(metrics['within_tolerance_rate'], 1 / 3)

    def test_real_loader_and_all_local_candidates_on_synthetic_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = []
            for index in range(60):
                identity = str(1000 + index)
                snapshot, rent = fixture()
                rent = replace(rent, value=str(1000 + index * 10))
                limit = replace(rent, fact_id='limit', key='policy_limit', value='5000.00')
                snapshot = replace(snapshot, claim_id=identity, facts=tuple(
                    FactApproval(fact_id=f.fact_id, sha256=digest(f)) for f in (rent, limit)))
                snapshot_id = save_snapshot(root, snapshot)
                candidates_path = root / f'{identity}-facts.json'
                candidates_path.write_text(json.dumps([f.model_dump(mode='json') for f in (rent, limit)]))
                key = AnswerKey(claim_id=identity, decision='approved' if index % 2 else 'declined',
                                ceiling_state='present' if index % 2 else 'not_applicable',
                                approved_ceiling=f'{1000 + index * 10}.00' if index % 2 else None,
                                source_sha256=SHA, reviewed_by='synthetic-test')
                answer_path = root / f'{identity}-key.json'
                answer_path.write_text(key.model_dump_json())
                entries.append(DevelopmentClaim(claim_id=identity, group_id=identity,
                    split='train' if index < 40 else 'validation', snapshot_root=root,
                    snapshot_id=snapshot_id, candidates_path=candidates_path, answer_key_path=answer_path))
            plan = DevelopmentPlan(reserved_claim_ids=('9999',), claims=tuple(entries))
            report = compare(plan)
            json.dumps(report, allow_nan=False)
            self.assertEqual(set(report['tasks']['decision']['models']), {'majority', 'logistic', 'shallow_tree'})
            self.assertEqual(set(report['tasks']['ceiling']['models']), {'median', 'ridge', 'shallow_tree'})
            self.assertEqual(report['tasks']['ceiling']['train_count'], 20)
            self.assertEqual(report['tasks']['ceiling']['models']['ridge']['advisory_coverage'], .5)
            self.assertEqual(report['automatic_decision_coverage'], 0)
            answer_path.write_text(replace(key, approved_ceiling='6000.00').model_dump_json())
            anomalous = compare(plan)
            self.assertEqual(anomalous['tasks']['ceiling']['excluded'][
                'validation:ceiling_label_above_limit_requires_review'], 1)
            self.assertEqual(anomalous['tasks']['decision']['validation_count'], 20)
            candidates_path.write_text('[]')
            self.assertFalse(load_development(plan)[-1][1].eligible)


if __name__ == '__main__':
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from backend.prediction.outcomes import (OutcomeAnnotation, OutcomeReview, compare,
                                        load_review, target)
from backend.prediction.retrospective import file_hash
from test_recommendations import examples


def annotation(identity='1000', **changes):
    return OutcomeAnnotation(claim_id=identity, evidence_references=('captured sheet cell',),
                             interpretation='Provisional historical explanation', **changes)


class OutcomeTests(unittest.TestCase):
    def test_administrative_mixed_and_conflicted_records_are_not_denial_targets(self):
        self.assertEqual(target(annotation(denial_family='charge_coverage')), 'documented_denial_hypothesis')
        for disposition in ('duplicate', 'tenant_payment_or_settlement', 'withdrawn', 'incomplete_evidence'):
            self.assertEqual(target(annotation(denial_family='charge_coverage', dispositions=(disposition,))), 'unknown')
        self.assertEqual(target(annotation(denial_family='lease_term', conflicts=('timeline_unclear',))), 'unknown')
        self.assertEqual(target(annotation()), 'unknown')

    def test_ambiguous_dispositions_and_robustness_or_duplicate_ids_fail_schema(self):
        for dispositions in ((), ('unknown', 'duplicate'), ('duplicate', 'duplicate')):
            with self.assertRaises(ValidationError):
                annotation(dispositions=dispositions)
        for annotations in ((annotation(), annotation('001000')), (annotation('00053'),)):
            with self.assertRaises(ValidationError):
                OutcomeReview(reviewed_by='synthetic', source_hashes={'decline-context-source.json': 'a'*64},
                              annotations=annotations)

    def test_context_cannot_be_promoted_to_verified_policy_evidence(self):
        with self.assertRaises(ValidationError):
            OutcomeReview(reviewed_by='synthetic', evidence_status='verified',
                          source_hashes={'decline-context-source.json': 'a'*64}, annotations=(annotation(),))
        with self.assertRaises(ValidationError):
            OutcomeReview(reviewed_by='synthetic', source_hashes={'../other.json': 'a'*64},
                          annotations=(annotation(),))

    def test_loader_requires_exact_decline_inventory_and_context_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = root / 'decline-context-source.json'
            context.write_text('{}')
            review = OutcomeReview(reviewed_by='synthetic', source_hashes={context.name: file_hash(context)},
                                   annotations=(annotation(),))
            path = root / 'review.json'
            path.write_text(review.model_dump_json())
            cohort = [{'claim_id': '1000', 'decision': 'declined'}, {'claim_id': '1001', 'decision': 'approved'}]
            self.assertEqual(load_review(root, path, cohort), review)
            with self.assertRaisesRegex(ValueError, 'exactly'):
                load_review(root, path, [{'claim_id': '9999', 'decision': 'declined'}])
            context.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                load_review(root, path, cohort)

    def test_changed_protocol_is_rejected_before_fitting(self):
        with patch('backend.prediction.outcomes.protocol', return_value={'changed': True}), \
             patch('backend.prediction.outcomes.dataset', side_effect=AssertionError('fitting')):
            with self.assertRaisesRegex(ValueError, 'Frozen'):
                compare(Path('/unused'), Path('/unused'), {})

    def test_new_target_filters_labels_without_changing_features_or_fold_membership(self):
        _, cohort = examples()
        annotations = tuple(annotation(item['claim_id'], denial_family='program_enrollment',
                            dispositions=('unknown',) if index % 2 else ('duplicate',))
                            for index, item in enumerate(cohort) if item['decision'] == 'declined')
        review = OutcomeReview(reviewed_by='synthetic', source_hashes={'decline-context-source.json':'a'*64},
                               annotations=annotations)
        splits = [(list(range(40)), list(range(40, 80))), (list(range(40, 80)), list(range(40)))]
        baseline = {'out_of_fold': [{'claim_id': item['claim_id'], 'research_decision': 'approved'} for item in cohort]}
        captured = {}

        def evaluate(selected, restricted, task):
            captured.update(selected=selected, restricted=restricted)
            return {'models': {}}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'recommendation-evaluation-v2.json').write_text(json.dumps(baseline))
            with patch('backend.prediction.outcomes.protocol', return_value={}), \
                 patch('backend.prediction.outcomes.dataset', return_value=(cohort, splits, {}, {'reserved_claim_ids':['9999']})), \
                 patch('backend.prediction.outcomes.load_review', return_value=review), \
                 patch('backend.prediction.outcomes.evaluate_task', side_effect=evaluate):
                result = compare(root, root / 'review.json', {})
        self.assertGreater(result['excluded_declines'], 0)
        self.assertFalse(result['reserved_outcomes_loaded'])
        by_id = {item['claim_id']: item for item in cohort}
        for selected in captured['selected']:
            self.assertIs(selected, by_id[selected['claim_id']])
            self.assertNotIn('interpretation', selected['features'])
        for (_, original_valid), (_, valid) in zip(splits, captured['restricted']):
            self.assertTrue({captured['selected'][i]['claim_id'] for i in valid} <=
                            {cohort[i]['claim_id'] for i in original_valid})


if __name__ == '__main__':
    unittest.main()

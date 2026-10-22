import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.prediction.artifacts import ResearchModels, predict
from backend.prediction.recommendation_comparison import compare, load_evidence, selected_arm, summary
from backend.prediction.recommendations import recommend
from backend.prediction.recommendations_research import fit_models
from backend.prediction.research_documents import ResearchFact, document_features
from backend.prediction.retrospective import file_hash
from test_recommendations import examples, request


def fact(claim='1000', key='monthly_rent', value='900.00'):
    return ResearchFact(claim_id=claim, key=key, source_role='lease', state='present', value=value,
        citations=[{'document_id': 'source', 'sha256': 'a' * 64, 'page': 1,
                    'quote': 'Monthly rent $900.00', 'bbox': [1, 2, 20, 30], 'method': 'native_text'}])


class DocumentFeatureTests(unittest.TestCase):
    def test_unknowns_conflicts_and_citations_are_not_values(self):
        unknown = document_features({}, '1000', ())
        self.assertNotIn('document.monthly_rent', unknown)
        self.assertEqual(unknown['document.monthly_rent_state=unreviewed'], 1)
        zero = document_features({}, '1000', [fact(value='0')])
        self.assertEqual(zero['document.monthly_rent'], 0)
        conflict = document_features({}, '1000', [fact(), fact(value='950')])
        self.assertNotIn('document.monthly_rent', conflict)
        self.assertEqual(conflict['document.monthly_rent_state=conflicting'], 1)
        encoded = json.dumps(document_features({}, '1000', [fact()]))
        for sensitive in ('1000', 'Monthly rent', 'source', 'aaaaaaaa'):
            self.assertNotIn(sensitive, encoded)

    def test_outcomes_ocr_bad_values_and_wrong_claims_rejected(self):
        for update in ({'key': 'Status'}, {'key': 'Approved Benefit Amount'}, {'value': 'nan'},
                       {'value': '-1'}, {'claim_id': '00053'}, {'citations': []},
                       {'state': 'missing'}, {'source_role': 'waiver'}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                ResearchFact.model_validate({**fact().model_dump(), **update})
        raw = fact().model_dump()
        raw['citations'][0]['method'] = 'ocr'
        with self.assertRaises(ValidationError):
            ResearchFact.model_validate(raw)
        with self.assertRaisesRegex(ValueError, 'claim mismatch'):
            document_features({}, '1001', [fact()])

    def test_source_bytes_claim_role_and_reserved_inventory_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'synthetic neutral lease')
            citation_fact = fact().model_dump(mode='json')
            citation_fact['citations'][0]['sha256'] = file_hash(source)
            bundle = {'sources': [{'claim_id': '1000', 'source_id': 'source', 'role': 'lease',
                'path': str(source), 'sha256': file_hash(source)}], 'facts': [citation_fact],
                'excluded_claim_ids': []}
            path = root / 'evidence.json'
            path.write_text(json.dumps(bundle))
            self.assertEqual(len(load_evidence(path, {'1000'}, {'2000'})[0]['1000']), 1)
            with self.assertRaises(ValueError):
                load_evidence(path, {'1000'}, {'1000'})
            bundle['sources'][0]['role'] = 'ledger'
            path.write_text(json.dumps(bundle))
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                load_evidence(path, {'1000'}, set())
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'bytes changed'):
                load_evidence(path, {'1000'}, set())


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.cohort = examples()
        cls.splits = [(list(range(40)), list(range(40, 80))),
                      (list(range(40, 80)), list(range(40)))]
        cls.facts = {str(1000 + i): [fact(str(1000 + i))] for i in range(12)}
        cls.result = compare(cls.cohort, cls.splits, cls.rows, cls.facts)

    def test_identical_claims_and_matched_amounts_with_sparse_evidence(self):
        self.assertEqual(self.result['overall']['count'], 80)
        self.assertEqual(self.result['document_processed_subset']['count'], 12)
        amounts = self.result['overall']['amount']
        self.assertEqual(amounts['spreadsheet']['count'], amounts['documents']['count'])
        self.assertEqual(amounts['spreadsheet']['count'], amounts['median']['count'])
        self.assertEqual(selected_arm(self.result), 'spreadsheet')
        self.assertEqual(summary([])['count'], 0)

    def test_validation_answers_never_change_its_fold_model_or_predictions(self):
        changed = [dict(item) if i >= 40 else item for i, item in enumerate(self.cohort)]
        for item in changed[40:]:
            item['decision'] = 'declined' if item['decision'] == 'approved' else 'approved'
            item['ceiling'] = 123456
        result = compare(changed, self.splits, self.rows, self.facts)
        self.assertEqual(result['folds'][0]['model_hashes'], self.result['folds'][0]['model_hashes'])
        for before, after in zip(self.result['out_of_fold'][:40], result['out_of_fold'][:40]):
            for arm in ('spreadsheet', 'documents'):
                self.assertEqual(before[arm], after[arm])

    def test_persisted_enriched_model_uses_the_same_feature_path(self):
        enriched = [{**item, 'features': document_features(item['features'], item['claim_id'],
                    self.facts.get(item['claim_id'], ()))} for item in self.cohort]
        models = fit_models(enriched, list(range(40)))
        reloaded = ResearchModels.model_validate_json(models.model_dump_json())
        inputs = request(self.rows[42])
        expected = predict(reloaded, enriched[42]['features'])
        output = recommend(reloaded, inputs, document_facts=())
        self.assertEqual(output['research_decision'], expected[0])
        self.assertEqual(output['raw_research_ceiling'], round(expected[1], 2))

    def test_group_overlap_fails_before_training(self):
        cohort = list(self.cohort)
        cohort[40] = {**cohort[40], 'group': cohort[0]['group']}
        with self.assertRaisesRegex(ValueError, 'overlap'):
            compare(cohort, self.splits, self.rows, self.facts)

    def test_approval_only_subset_does_not_claim_decline_recall(self):
        approvals = [r for r in self.result['out_of_fold'] if r['actual_decision'] == 'approved']
        metrics = summary(approvals)['decision']['documents']
        self.assertIsNone(metrics['decline_recall'])
        self.assertIsNone(metrics['balanced_accuracy'])


if __name__ == '__main__':
    unittest.main()

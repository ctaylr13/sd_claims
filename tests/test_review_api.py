import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from backend.batches import BatchRequest, create_batch, process_batch
from backend.claims import INPUT_FIELDS
from backend.extraction import extract_claim
from backend.main import app, get_drive, get_store
from backend.prediction.artifacts import ResearchModels, Tree
from backend.prediction.recommendations import ModelArtifact, code_hashes
from backend.storage import LocalStore, RetryRequired, _file_digest


class ReviewApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.addCleanup(app.dependency_overrides.clear)
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def configure(self, name, value):
        path = self.root / f'{name}.json'
        path.write_text(json.dumps(value))
        os.environ[f'{name}_PATH'] = str(path)
        os.environ[f'{name}_SHA256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    def model(self):
        decision = Tree(left=(-1,), right=(-1,), feature=(-2,), threshold=(-2,), value=(0,))
        amount = decision.model_copy(update={'value': (0.0,)})
        models = ResearchModels(feature_names=('rent',), means=(0,), scales=(1,), decision=decision,
                                ceiling=(amount,), initial_ceiling=500, learning_rate=.1,
                                training_median=300, majority_decision='approved')
        artifact = ModelArtifact(models=models, source_hashes={}, code_hashes=code_hashes(),
                                 development_ids=('001',), reserved_ids=('999',), environment={},
                                 training_parameters={})
        return self.configure('RECOMMENDATION_MODEL', artifact.model_dump(mode='json'))

    def drive(self, claim_id='001'):
        app.dependency_overrides[get_drive] = lambda: (Mock(), {})
        claim = {'tracking_number': claim_id, 'claims_source': {'id': 'sheet', 'version': '7'},
                 'prediction_inputs': {name: {'source_column': column, 'recorded_value': '1000'}
                                       for column, name in INPUT_FIELDS.items()},
                 'historical_outcome': {'Status': 'secret-outcome', 'Approved Benefit Amount': 'secret-amount'}}
        claim['prediction_inputs']['termination_type']['recorded_value'] = 'Early'
        for name in ('claim_date', 'lease_start_date', 'lease_end_date', 'move_out_date'):
            claim['prediction_inputs'][name]['recorded_value'] = '10/01/24'
        self.enterContext(patch('backend.main.lookup_claim', return_value=claim))
        return claim

    def test_recommendation_uses_only_allowlisted_live_inputs(self):
        self.model()
        self.drive()
        response = self.client.post('/api/claims/001/recommendation')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['decision'], 'needs_review')
        self.assertEqual(result['conditional_ceiling'], 500)
        self.assertFalse(result['correctness_verified'])
        self.assertNotIn('secret-outcome', json.dumps(result['inputs']))
        self.assertNotIn('secret-amount', json.dumps(result['inputs']))
        self.assertEqual(result['historical_comparison']['recorded_status'], 'secret-outcome')
        self.assertIsNone(result['historical_comparison']['decision_match'])
        self.assertEqual({item['column'] for item in result['inputs']}, set(INPUT_FIELDS))
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_reserved_and_robustness_claims_are_rejected(self):
        self.model()
        for claim_id in ('999', '000999', '00053'):
            with self.subTest(claim_id=claim_id):
                self.drive(claim_id)
                response = self.client.post(f'/api/claims/{claim_id}/recommendation')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['error']['code'], 'claim_excluded')

    def test_unconfigured_changed_and_incompatible_model_fail_closed(self):
        self.drive()
        response = self.client.post('/api/claims/001/recommendation')
        self.assertEqual(response.status_code, 503)
        path = self.model()
        path.write_text('{}')
        self.assertEqual(self.client.post('/api/claims/001/recommendation').status_code, 503)
        self.configure('RECOMMENDATION_MODEL', {})
        self.assertEqual(self.client.post('/api/claims/001/recommendation').status_code, 503)

    def test_metrics_exclude_claim_records_and_require_verified_development_scope(self):
        self.assertEqual(self.client.get('/api/review/metrics').status_code, 503)
        decision = {'accuracy': .8, 'balanced_accuracy': .7, 'decline_recall': .6,
                    'confusion_matrix': [[3, 2], [1, 9]]}
        report = {'decision': {'research_decision': decision, 'majority_decision': decision},
                  'coverage': {'development_rows': 20, 'qualified_outcomes': 15,
                               'conditional_estimates': 14, 'scoreable_approved_advisories': 9, 'withheld': 1},
                  'independent_correctness': {'status': 'not_evaluated', 'reviewed_claim_count': 0,
                                              'required': 'Independent review required'},
                  'ceiling_on_same_approved_advisory_subset': {
                      'conditional_ceiling': {'count': 9, 'metrics': {'mae_dollars': 20, 'within_tolerance_rate': .9}}}}
        report.update(version='test', created_at='2026-10-21',
                      scope='development_only_historical_agreement', method='synthetic',
                      reserved_outcomes_loaded=False, out_of_fold=[{'claim_id': 'secret'}])
        path = self.configure('RECOMMENDATION_EVALUATION', report)
        response = self.client.get('/api/review/metrics')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('out_of_fold', response.text)
        self.assertNotIn('secret', response.text)
        path.write_text('{}')
        self.assertEqual(self.client.get('/api/review/metrics').status_code, 503)
        self.configure('RECOMMENDATION_EVALUATION', {**report, 'decision': {}})
        self.assertEqual(self.client.get('/api/review/metrics').status_code, 503)
        report['reserved_outcomes_loaded'] = True
        self.configure('RECOMMENDATION_EVALUATION', report)
        self.assertEqual(self.client.get('/api/review/metrics').status_code, 503)

    def test_retained_pdf_requires_verified_batch_result_and_bounded_document(self):
        source = self.root / 'source.pdf'
        canvas = Canvas(str(source))
        canvas.drawString(20, 750, 'Amount Due $123.45 for cleaning and repairs')
        canvas.save()
        store = LocalStore(self.root / 'store')
        app.dependency_overrides[get_store] = lambda: store
        body = BatchRequest(claims=[{'claim_id': 'example', 'documents': [
            {'document_id': 'statement', 'kind': 'move_out_statement', 'path': str(source)}]}])
        progress = create_batch(store, body)
        process_batch(progress['batch_id'], body, store,
                      lambda manifest, root, retry: extract_claim(manifest, root, retry=retry))
        base = f'/api/batches/{progress["batch_id"]}/claims/0'
        response = self.client.get(f'{base}/documents/0')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, source.read_bytes())
        self.assertEqual(response.headers['content-type'], 'application/pdf')
        page = self.client.get(f'{base}/documents/0/pages/1')
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.content.startswith(b'\x89PNG'))
        self.assertEqual(self.client.get(f'{base}/documents/0/pages/0').status_code, 404)
        self.assertEqual(self.client.get(f'{base}/documents/0/pages/2').status_code, 404)
        self.assertEqual(self.client.get(f'{base}/documents/-1').status_code, 404)
        self.assertEqual(self.client.get(f'{base}/documents/1').status_code, 404)
        report = self.client.get(f'{base}/result').json()
        Path(report['documents'][0]['document']['original_path']).write_bytes(b'changed')
        self.assertEqual(self.client.get(f'{base}/documents/0').status_code, 409)
        self.assertEqual(self.client.get(f'{base}/documents/0/pages/1').status_code, 409)

    def evidence_attempt(self, *, store_name='store', untracked_page=False):
        store = LocalStore(self.root / store_name)
        app.dependency_overrides[get_store] = lambda: store

        def produce(directory):
            (directory / 'original.pdf').write_bytes(b'original')
            pages = directory / 'pages'
            pages.mkdir()
            if not untracked_page:
                (pages / '1.png').write_bytes(b'page one')
            (pages / '2.png').write_bytes(b'page two')
            report = directory / 'report.json'
            report.write_text(json.dumps({'documents': [{'document': {
                'original_path': str(directory / 'original.pdf'),
                'pages': [{'number': n, 'image_path': str(pages / f'{n}.png')} for n in (1, 2)]}}]}))
            return report

        report = store.run({'claim': 'evidence'}, produce)
        store.save_batch({'batch_id': 'evidence', 'results': [
            {'state': 'succeeded', 'attempt': report.parent.name}]})
        return store, report, '/api/batches/evidence/claims/0'

    def test_evidence_hashes_only_report_source_and_requested_page(self):
        _, report, base = self.evidence_attempt()
        for suffix, names in (
                ('/documents/0/pages/1', ('report.json', 'original.pdf', 'pages/1.png')),
                ('/documents/0', ('report.json', 'original.pdf')),
                ('/result', ('report.json', 'original.pdf', 'pages/1.png', 'pages/2.png')),
                ('/documents/0/pages/99', ('report.json',))):
            with self.subTest(endpoint=suffix), patch('backend.storage._file_digest', wraps=_file_digest) as hashed:
                response = self.client.get(base + suffix)
                self.assertEqual(response.status_code, 404 if suffix.endswith('/99') else 200)
                self.assertCountEqual([call.args[0] for call in hashed.call_args_list],
                                      [report.parent / name for name in names])

    def test_unrelated_page_damage_allows_evidence_but_blocks_full_verification(self):
        for damage in ('modify', 'delete'):
            with self.subTest(damage=damage):
                store, report, base = self.evidence_attempt(store_name=damage)
                unrelated = report.parent / 'pages/2.png'
                if damage == 'modify':
                    unrelated.write_bytes(b'changed')
                else:
                    unrelated.unlink()
                response = self.client.get(base + '/documents/0/pages/1')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'page one')
                self.assertEqual(self.client.get(base + '/documents/0').status_code, 200)
                self.assertEqual(self.client.get(base + '/result').status_code, 409)
                producer = Mock()
                with self.assertRaises(RetryRequired):
                    store.run({'claim': 'evidence'}, producer)
                producer.assert_not_called()

    def test_evidence_rejects_missing_or_modified_report_source_and_requested_page(self):
        _, report, base = self.evidence_attempt()
        for name in ('report.json', 'original.pdf', 'pages/1.png'):
            path = report.parent / name
            content = path.read_bytes()
            for damage in ('modify', 'delete'):
                with self.subTest(artifact=name, damage=damage):
                    if damage == 'modify':
                        path.write_bytes(b'changed')
                    else:
                        path.unlink()
                    self.assertEqual(self.client.get(base + '/documents/0/pages/1').status_code, 409)
                    path.write_bytes(content)

    def test_evidence_rejects_file_absent_from_published_inventory(self):
        _, report, base = self.evidence_attempt(untracked_page=True)
        (report.parent / 'pages/1.png').write_bytes(b'untracked')
        self.assertEqual(self.client.get(base + '/documents/0/pages/1').status_code, 409)

    def test_model_code_change_is_rejected(self):
        path = self.model()
        payload = json.loads(path.read_text())
        payload['code_hashes']['recommendations.py'] = 'changed'
        self.configure('RECOMMENDATION_MODEL', payload)
        self.drive()
        self.assertEqual(self.client.post('/api/claims/001/recommendation').status_code, 503)

    def test_document_outside_verified_attempt_is_not_served(self):
        outside = self.root / 'private.pdf'
        outside.write_bytes(b'private-document')
        store = LocalStore(self.root / 'store')
        app.dependency_overrides[get_store] = lambda: store

        def produce(directory):
            path = directory / 'report.json'
            path.write_text(json.dumps({'documents': [{'document': {'original_path': str(outside)}}]}))
            return path

        report = store.run({'claim': 'synthetic'}, produce)
        store.save_batch({'batch_id': 'synthetic', 'results': [{'state': 'succeeded', 'attempt': report.parent.name}]})
        response = self.client.get('/api/batches/synthetic/claims/0/documents/0')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('private-document', response.text)


    def test_batch_recommendations_share_one_lookup_and_model_with_partial_failures(self):
        from backend.review import load_recommendation_model

        self.model()
        claim = self.drive()
        lookups = [{'tracking_number': '001', 'state': 'found', 'claim': claim},
                   {'tracking_number': 'missing', 'state': 'error', 'error': {'code': 'claim_not_found', 'message': 'Not found'}},
                   {'tracking_number': '999', 'state': 'found', 'claim': {**claim, 'tracking_number': '999'}}]
        with patch('backend.main.lookup_claims', return_value=lookups) as lookup, patch(
                'backend.review.load_recommendation_model', wraps=load_recommendation_model) as load:
            response = self.client.post('/api/claims/recommendations', json={'tracking_numbers': ['001', 'missing', '999']})
        self.assertEqual(response.status_code, 200, response.text)
        results = response.json()['results']
        self.assertEqual([item['tracking_number'] for item in results], ['001', 'missing', '999'])
        self.assertEqual([item['state'] for item in results], ['succeeded', 'error', 'error'])
        self.assertEqual(results[2]['error']['code'], 'claim_excluded')
        lookup.assert_called_once()
        load.assert_called_once()

    def test_batch_recommendation_failure_is_sanitized_and_later_claim_continues(self):
        self.model()
        claim = self.drive()
        lookups = [{'tracking_number': number, 'state': 'found', 'claim': {**claim, 'tracking_number': number}}
                   for number in ('001', '002')]
        with patch('backend.main.lookup_claims', return_value=lookups), patch(
                'backend.review.recommend_claim', side_effect=[RuntimeError('private-path'), {'claim_id': '002'}]):
            response = self.client.post('/api/claims/recommendations', json={'tracking_numbers': ['001', '002']})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('private-path', response.text)
        self.assertEqual([item['state'] for item in response.json()['results']], ['error', 'succeeded'])

    def test_historical_comparison_keeps_unknowns_and_conditional_ceiling_scope(self):
        from backend.review import historical_comparison

        result = {'research_decision': 'approved', 'conditional_ceiling': 500,
                  'benchmarks': {'recorded_limit': 1000}}
        claim = {'historical_outcome': {'Status': 'Approved', 'Approved Benefit Amount': '$400'}}
        comparison = historical_comparison(claim, result)
        self.assertTrue(comparison['decision_match'])
        self.assertEqual(comparison['difference_dollars'], 100)
        self.assertEqual(comparison['absolute_error_percent'], 25)
        lower = historical_comparison(claim, {**result, 'conditional_ceiling': 300})
        self.assertEqual(lower['difference_dollars'], -100)
        self.assertEqual(lower['absolute_error_percent'], 25)
        for status, amount in [('Pending', '400'), ('Approved', ''), ('Approved', '0'),
                               ('Approved', 'invalid'), ('Approved', '1100'), ('Declined', '0'), ('Declined', '400')]:
            with self.subTest(status=status, amount=amount):
                comparison = historical_comparison({'historical_outcome': {
                    'Status': status, 'Approved Benefit Amount': amount}}, result)
                self.assertIsNone(comparison['difference_dollars'])
                self.assertIsNone(comparison['absolute_error_percent'])
                self.assertTrue(comparison['reasons'])
                if status == 'Pending' or (status == 'Declined' and amount == '400'):
                    self.assertIsNone(comparison['decision_match'])
        withheld = historical_comparison(claim, {**result, 'conditional_ceiling': None})
        self.assertTrue(withheld['decision_match'])
        self.assertIsNone(withheld['absolute_error_percent'])

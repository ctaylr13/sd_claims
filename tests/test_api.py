import json
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.drive import FOLDER_MIME, connect_drive
from backend.main import app, get_drive


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.sources = {'claims_file_id': 'sheet', 'evidence_root_folder_id': 'root'}
        self.row = {'Tracking Number': '001', 'Status': 'Approved',
                    'Approved Benefit Amount': '1500', 'Max Benefit': '2000',
                    'PM Explanation': 'answer-only-note'}
        app.dependency_overrides[get_drive] = lambda: (self.service, self.sources)
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.read = patch('backend.claims.read_claims', return_value=([], [self.row], {'version': '7'})).start()
        self.children = patch('backend.claims.list_children', return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_single_lookup_preserves_answers_and_input_boundary(self):
        response = self.client.get('/api/claims/001')
        self.assertEqual(response.status_code, 200)
        claim = response.json()
        self.assertEqual(claim['tracking_number'], '001')
        self.assertEqual(claim['historical_outcome']['Approved Benefit Amount'], '1500')
        self.assertNotIn('Approved Benefit Amount', json.dumps(claim['prediction_inputs']))
        self.assertNotIn('answer-only-note', response.text)
        self.assertEqual(claim['evidence']['state'], 'missing')
        self.assertFalse(claim['training_eligible'])
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_health_and_docs_do_not_connect_to_drive(self):
        app.dependency_overrides.clear()
        with patch('backend.main.connect_drive') as connect:
            for path in ['/api/health', '/docs', '/openapi.json']:
                self.assertEqual(self.client.get(path).status_code, 200)
            connect.assert_not_called()

    def test_source_error_is_sanitized(self):
        self.read.side_effect = RuntimeError('private-token')
        response = self.client.get('/api/claims/001')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['error']['code'], 'source_unavailable')
        self.assertNotIn('private-token', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_auth_failure_is_sanitized_without_blocking_health(self):
        app.dependency_overrides.clear()
        with patch('backend.main.connect_drive', side_effect=RuntimeError('private-token')):
            response = self.client.get('/api/claims/001')
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('private-token', response.text)
            self.assertEqual(self.client.get('/api/health').status_code, 200)

    def test_unknown_and_duplicate_records_have_distinct_errors(self):
        response = self.client.get('/api/claims/unknown')
        self.assertEqual(response.status_code, 404)
        self.read.return_value = ([], [self.row, self.row], {})
        response = self.client.get('/api/claims/001')
        self.assertEqual(response.status_code, 409)

    def test_method_and_unknown_route_keep_error_contract(self):
        for response in [self.client.post('/api/claims/001'), self.client.get('/unknown')]:
            self.assertIn(response.status_code, (404, 405))
            self.assertIn('error', response.json())
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_batch_normalizes_and_keeps_partial_success(self):
        response = self.client.post('/api/claims/lookup',
                                    json={'tracking_numbers': ' 001, unknown\n001, '})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual((result['total'], result['found'], result['failed']), (2, 1, 1))
        self.assertEqual([r['tracking_number'] for r in result['results']], ['001', 'unknown'])
        self.assertEqual(result['results'][1]['error']['code'], 'claim_not_found')
        self.read.assert_called_once()
        self.children.assert_called_once()

    def test_ten_claim_batch_reads_shared_sources_once(self):
        numbers = [f'{n:03}' for n in range(10)]
        self.read.return_value = ([], [{**self.row, 'Tracking Number': n} for n in numbers], {})
        result = self.client.post('/api/claims/lookup', json={'tracking_numbers': numbers}).json()
        self.assertEqual((result['total'], result['found'], result['failed']), (10, 10, 0))
        self.read.assert_called_once()
        self.children.assert_called_once()

    def test_invalid_entry_does_not_block_other_claims(self):
        result = self.client.post('/api/claims/lookup',
                                  json={'tracking_numbers': ['001', '', 'x' * 129]}).json()
        self.assertEqual(result['found'], 1)
        self.assertEqual([r['http_status'] for r in result['results']], [200, 400, 400])

    def test_evidence_failure_stays_with_its_claim(self):
        self.read.return_value = ([], [self.row, {**self.row, 'Tracking Number': '002'}], {})
        self.children.return_value = [{'id': 'folder', 'name': '001', 'mimeType': FOLDER_MIME}]
        with patch('backend.claims.inventory_claim', side_effect=RuntimeError('private-token')):
            response = self.client.post('/api/claims/lookup', json={'tracking_numbers': ['001', '002']})
        result = response.json()
        self.assertEqual([r['http_status'] for r in result['results']], [502, 200])
        self.assertNotIn('private-token', response.text)

    def test_shared_batch_failure_returns_errors_for_each_claim(self):
        self.read.side_effect = RuntimeError('private-token')
        response = self.client.post('/api/claims/lookup', json={'tracking_numbers': ['001', '002']})
        result = response.json()
        self.assertEqual(result['failed'], 2)
        self.assertEqual([r['http_status'] for r in result['results']], [502, 502])
        self.assertNotIn('private-token', response.text)

    def test_malformed_and_oversized_batches_return_422(self):
        for body in [{}, {'tracking_numbers': []}, {'tracking_numbers': ',\n'},
                     {'tracking_numbers': [1]}, {'tracking_numbers': ['001'] * 101}]:
            with self.subTest(body=body):
                response = self.client.post('/api/claims/lookup', json=body)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()['error']['code'], 'invalid_request')
                self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_api_auth_never_starts_an_interactive_browser(self):
        token = Mock()
        token.exists.return_value = False
        with patch('google_auth_oauthlib.flow.InstalledAppFlow.run_local_server') as login:
            with self.assertRaisesRegex(ValueError, 'authorization required'):
                connect_drive(Mock(), token, allow_interactive=False)
            login.assert_not_called()

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app


class LocalSourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'claims.csv'
        self.path.write_text('Tracking Number,Monthly Rent,Status,Approved Benefit Amount\nLOCAL-1,1000,Declined,\n')
        self.environment = patch.dict(os.environ, {'CLAIMS_CSV_PATH': str(self.path)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_lookup_without_google_preserves_input_and_outcome_boundary(self):
        with patch('backend.main.connect_drive') as connect:
            response = self.client.get('/api/claims/LOCAL-1')
        connect.assert_not_called()
        self.assertEqual(response.status_code, 200)
        claim = response.json()
        self.assertEqual(claim['claims_source']['mode'], 'local_csv')
        self.assertEqual(claim['claims_source']['version'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(claim['prediction_inputs']['monthly_rent']['recorded_value'], '1000')
        self.assertNotIn('Status', claim['prediction_inputs'])
        self.assertEqual(claim['historical_outcome']['Status'], 'Declined')
        self.assertEqual(claim['evidence'], {'state': 'missing', 'folder': None, 'items': []})
        self.assertFalse(claim['training_eligible'])

    def test_changed_csv_is_read_fresh_and_batch_keeps_individual_errors(self):
        before = self.client.get('/api/claims/LOCAL-1').json()
        self.path.write_text(self.path.read_text().replace('1000', '1500'))
        response = self.client.post('/api/claims/lookup', json={'tracking_numbers': ['LOCAL-1', 'unknown']})
        result = response.json()
        self.assertEqual((result['found'], result['failed']), (1, 1))
        after = result['results'][0]['claim']
        self.assertEqual(after['prediction_inputs']['monthly_rent']['recorded_value'], '1500')
        self.assertNotEqual(before['claims_source']['version'], after['claims_source']['version'])

    def test_bad_or_missing_csv_returns_sanitized_source_error(self):
        for content in ('Wrong Header\nvalue\n', None):
            with self.subTest(content=content):
                if content is None:
                    self.path.unlink()
                else:
                    self.path.write_text(content)
                with self.assertLogs('backend.diagnostics', level='ERROR'):
                    response = self.client.get('/api/claims/LOCAL-1')
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()['error']['code'], 'source_unavailable')
                self.assertNotIn(str(self.path), response.text)

    def test_duplicate_csv_records_still_require_review(self):
        self.path.write_text(self.path.read_text() + 'LOCAL-1,2000,Approved,100\n')
        response = self.client.get('/api/claims/LOCAL-1')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'duplicate_claim_records')

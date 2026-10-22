import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.local_evidence import evidence_file, inventory_local_claim


class LocalSourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'claims.csv'
        self.path.write_text('Tracking Number,Monthly Rent,Status,Approved Benefit Amount\nLOCAL-1,1000,Declined,\n')
        self.evidence = Path(self.directory.name) / 'evidence'
        self.environment = patch.dict(os.environ, {'CLAIMS_CSV_PATH': str(self.path), 'CLAIMS_EVIDENCE_ROOT': str(self.evidence)})
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

    def test_downloaded_pdfs_are_listed_and_open_without_google(self):
        folder = self.evidence / 'LOCAL-1' / 'Lease documents'
        folder.mkdir(parents=True)
        pdf = folder / 'signed lease #1.pdf'
        pdf.write_bytes(b'%PDF-1.4\nlocal fixture')
        (folder / 'private.txt').write_text('not a PDF')
        with patch('backend.main.connect_drive') as connect:
            claim = self.client.get('/api/claims/LOCAL-1').json()
            batch = self.client.post('/api/claims/lookup', json={'tracking_numbers': ['LOCAL-1']}).json()
            document = self.client.get(claim['evidence']['items'][0]['webViewLink'])
        connect.assert_not_called()
        self.assertEqual(claim['evidence']['state'], 'present')
        self.assertEqual(len(claim['evidence']['items']), 1)
        self.assertEqual(batch['results'][0]['claim']['evidence'], claim['evidence'])
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document.headers['content-type'], 'application/pdf')
        self.assertEqual(document.content, pdf.read_bytes())
        self.assertTrue(document.headers['content-disposition'].startswith('inline;'))
        pdf.unlink()
        self.assertEqual(self.client.get(claim['evidence']['items'][0]['webViewLink']).status_code, 404)

    def test_local_files_cannot_escape_claim_folder_or_serve_in_drive_mode(self):
        folder = self.evidence / 'LOCAL-1'
        folder.mkdir(parents=True)
        outside = Path(self.directory.name) / 'outside.pdf'
        outside.write_bytes(b'private')
        (folder / 'linked.pdf').symlink_to(outside)
        (folder / 'linked-directory').symlink_to(outside.parent, target_is_directory=True)
        (self.evidence / 'OTHER').symlink_to(folder, target_is_directory=True)
        (folder / 'note.txt').write_text('private')
        self.assertEqual(inventory_local_claim(self.evidence, 'LOCAL-1')['items'], [])
        self.assertIsNone(inventory_local_claim(self.evidence, 'OTHER')['folder'])
        for tracking, name in [('LOCAL-1', '../../outside.pdf'), ('LOCAL-1', str(outside)),
                               ('LOCAL-1', 'linked.pdf'), ('LOCAL-1', 'linked-directory/outside.pdf'),
                               ('LOCAL-1', 'note.txt'), ('..', 'outside.pdf')]:
            with self.subTest(tracking=tracking, name=name):
                self.assertIsNone(evidence_file(self.evidence, tracking, name))
        self.assertEqual(self.client.get('/api/claims/LOCAL-1/evidence/linked.pdf').status_code, 404)
        (folder / 'ledger.pdf').write_bytes(b'%PDF-1.4\nfixture')
        with patch.dict(os.environ, {'CLAIMS_CSV_PATH': ''}), patch('backend.main.connect_drive'):
            self.assertEqual(self.client.get('/api/claims/LOCAL-1/evidence/ledger.pdf').status_code, 404)

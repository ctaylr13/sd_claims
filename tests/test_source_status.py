import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.main import app


class SourceStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch('backend.main.REPO_ROOT', self.root))
        self.enterContext(patch('backend.main.load_sources', return_value={'claims_file_id': 'sheet', 'evidence_root_folder_id': 'folder'}))
        self.connect = self.enterContext(patch('backend.main.connect_drive', side_effect=ValueError('private-token')))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def add_local_claims(self):
        (self.root / 'data/evidence/123').mkdir(parents=True)
        (self.root / 'data/claims.csv').write_text('Tracking Number,Status\n123,Approved\n')
        (self.root / 'data/evidence/123/lease.pdf').write_bytes(b'%PDF-1.4\nfixture')

    def test_missing_authorization_shows_setup_without_exposing_details(self):
        response = self.client.get('/api/source')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'mode': 'disconnected', 'name': None})
        self.assertNotIn('private-token', response.text)
        self.assertFalse(self.connect.call_args.kwargs['allow_interactive'])

    def test_placing_files_enables_local_lookup_without_restart(self):
        self.assertEqual(self.client.get('/api/source').json()['mode'], 'disconnected')
        self.add_local_claims()
        self.assertEqual(self.client.get('/api/source').json(), {'mode': 'local', 'name': 'claims.csv'})
        claim = self.client.get('/api/claims/123').json()
        self.assertEqual(claim['historical_outcome']['Status'], 'Approved')
        self.assertEqual(self.client.get(claim['evidence']['items'][0]['webViewLink']).status_code, 200)

    def test_authorized_drive_is_preferred_over_local_fallback(self):
        self.add_local_claims()
        service = Mock()
        self.connect.side_effect = None
        self.connect.return_value = service
        self.assertEqual(self.client.get('/api/source').json(), {'mode': 'drive', 'name': 'Google Drive'})
        service.close.assert_called_once()
        self.assertEqual(self.client.get('/api/claims/123/evidence/lease.pdf').status_code, 404)

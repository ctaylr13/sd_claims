import json
import unittest
from unittest.mock import Mock, patch

from backend.claims import LookupError, lookup_claim


class ClaimLookupTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.sources = {'claims_file_id': 'sheet', 'evidence_root_folder_id': 'root'}
        self.row = {'Tracking Number': '001', 'Monthly Rent': '1000', 'Max Benefit': '2000',
                    'Status': 'Approved', 'Approved Benefit Amount': '1500',
                    'PM Explanation': 'decision-bearing', 'Collected Amount': '12'}

    @patch('backend.claims.list_children', return_value=[])
    @patch('backend.claims.read_claims')
    def test_allowlist_separates_outcomes_and_reports_missing_folder(self, read, children):
        read.return_value = ([], [self.row], {'version': '7'})
        result = lookup_claim(self.service, self.sources, '001')
        self.assertEqual(result['tracking_number'], '001')
        self.assertEqual(result['evidence']['state'], 'missing')
        self.assertEqual(result['historical_outcome']['Approved Benefit Amount'], '1500')
        self.assertEqual(result['prediction_inputs']['policy_limit']['recorded_value'], '2000')
        self.assertNotIn('Approved Benefit Amount', json.dumps(result['prediction_inputs']))
        self.assertNotIn('decision-bearing', json.dumps(result))
        self.assertFalse(result['training_eligible'])

    @patch('backend.claims.list_children')
    @patch('backend.claims.read_claims')
    def test_duplicate_rows_fail_before_evidence_lookup(self, read, children):
        read.return_value = ([], [self.row, self.row], {})
        with self.assertRaises(LookupError) as raised:
            lookup_claim(self.service, self.sources, '001')
        self.assertEqual(raised.exception.status, 409)
        children.assert_not_called()

    @patch('backend.claims.list_children')
    @patch('backend.claims.read_claims')
    def test_duplicate_folders_fail(self, read, children):
        read.return_value = ([], [self.row], {})
        children.return_value = [{'id': value, 'name': '001', 'mimeType': 'application/vnd.google-apps.folder'}
                                 for value in ('a', 'b')]
        with self.assertRaises(LookupError) as raised:
            lookup_claim(self.service, self.sources, '001')
        self.assertEqual(raised.exception.code, 'duplicate_evidence_folders')


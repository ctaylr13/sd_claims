import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from backend.prediction.quality import (DateRepair, RepairSet, apply_repairs, audit,
                                        compare, date_issues, quality_features)
from backend.prediction.retrospective import file_hash, row_features
from test_retrospective import row


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / 'document.pdf'
        self.source.write_bytes(b'synthetic source evidence')
        self.record = row(**{'Lease Start Date': '09/01/24', 'Lease End Date': '08/31/24',
                             'Move-Out Date': '10/14/24', 'Claim Date': '11/21/24'})
        self.repair = DateRepair(claim_id='100', column='Lease End Date', original_value='08/31/24',
            corrected_value='2025-08-31', source_id='synthetic', source_path=self.source,
            source_sha256=file_hash(self.source), page=1, locator='lease term', reviewed_by='synthetic')
        self.review = RepairSet(dataset_source_hashes={'frozen':'a'*64}, repairs=(self.repair,))

    def test_source_repair_preserves_recorded_rows_and_shares_unchanged_records(self):
        other = row(**{'Tracking Number': '101'})
        result = apply_repairs([self.record, other], self.review, self.review.dataset_source_hashes)
        self.assertEqual(result[0]['Lease End Date'], '2025-08-31')
        self.assertEqual(self.record['Lease End Date'], '08/31/24')
        self.assertIs(result[1], other)
        self.assertEqual(result[0]['Status'], self.record['Status'])
        self.assertIn('reversed_lease_term', date_issues(self.record))
        self.assertNotIn('reversed_lease_term', date_issues(result[0]))

    def test_original_value_dataset_and_document_bytes_must_match(self):
        with self.assertRaisesRegex(ValueError, 'another dataset'):
            apply_repairs([self.record], self.review, {})
        with self.assertRaisesRegex(ValueError, 'reviewed original'):
            apply_repairs([self.record | {'Lease End Date':'09/01/24'}], self.review, self.review.dataset_source_hashes)
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'document bytes'):
            apply_repairs([self.record], self.review, self.review.dataset_source_hashes)

    def test_duplicate_cells_robustness_and_outside_inventory_are_rejected(self):
        with self.assertRaises(ValidationError):
            RepairSet(dataset_source_hashes={}, repairs=(self.repair, self.repair))
        with self.assertRaises(ValidationError):
            DateRepair.model_validate(self.repair.model_dump() | {'claim_id':'00053'})
        with self.assertRaisesRegex(ValueError, 'outside'):
            apply_repairs([row(**{'Tracking Number':'101'})], self.review, self.review.dataset_source_hashes)
        with self.assertRaises(ValidationError):
            DateRepair.model_validate(self.repair.model_dump() | {'corrected_value':'2025-02-30'})

    def test_modes_remove_only_declared_features_and_never_add_labels(self):
        original = row_features(self.record)
        masked = quality_features(self.record, 'masked_date_relations')
        self.assertNotIn('lease_days', masked)
        self.assertNotIn('lease_days_remaining', masked)
        self.assertEqual(masked['days_after_move_out'], original['days_after_move_out'])
        self.assertEqual(masked['lease_days:state=conflicting_order'], 1)
        no_dates = quality_features(self.record, 'no_dates')
        self.assertFalse(any('Date' in key or key in {'lease_days','days_after_move_out','lease_days_remaining'} for key in no_dates))
        changed_answers = self.record | {'Status':'Declined','Approved Benefit Amount':'9999', 'Tracking Number':'9999'}
        self.assertEqual(quality_features(changed_answers, 'source_repaired'), original)

    def test_future_moveout_is_flagged_without_inventing_a_repair(self):
        scheduled = self.record | {'Lease End Date':'08/31/25','Claim Date':'10/13/24'}
        self.assertIn('claim_before_move_out', date_issues(scheduled))
        self.assertEqual(quality_features(scheduled, 'recorded')['days_after_move_out'], -1)
        self.assertNotIn('days_after_move_out', quality_features(scheduled, 'masked_date_relations'))
        self.assertEqual(audit([scheduled])['date_issue_counts']['claim_before_move_out'], 1)

    def test_protocol_change_is_rejected_before_fitting(self):
        with patch('backend.prediction.quality.protocol', return_value={'changed':True}), \
             patch('backend.prediction.quality.prepare', side_effect=AssertionError('fit')):
            with self.assertRaisesRegex(ValueError, 'Frozen'):
                compare(Path('/unused'), Path('/unused'), {})


if __name__ == '__main__':
    unittest.main()

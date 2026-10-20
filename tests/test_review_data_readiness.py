import unittest

from scripts.review_data_readiness import review_claims


def row(tracking='001', status='Posted', ceiling='100', limit='200'):
    return {'Tracking Number': tracking, 'Status': status, 'Approved Benefit Amount': ceiling,
            'Max Benefit': limit, 'Claim Date': '01/01/24', 'Approval Date': '', 'Posted Date': ''}


def review(rows, root_items=None, claims=None):
    inventory = {'root_items': root_items or [], 'claims': claims or {}, 'updated_at': 'snapshot',
                 'complete': True, 'errors': {}}
    return review_claims(list(rows[0]), rows, {'id': 'sheet'}, root_items or [], inventory)


class DataReadinessReviewTests(unittest.TestCase):
    def test_approval_label_survives_zero_or_missing_ceiling(self):
        rows = [row(ceiling='0'), row(tracking='002', ceiling=' '), row(tracking='003', ceiling='bad')]
        report = review(rows)
        self.assertEqual(report['status_counts'], {'Posted': 3})
        self.assertEqual(report['ceiling_reason_counts'], {
            'final_approval_ceiling_zero': 1, 'final_approval_ceiling_missing': 1,
            'final_approval_ceiling_invalid': 1,
        })
        self.assertEqual(rows[1]['Approved Benefit Amount'], ' ')

    def test_decline_with_ceiling_and_policy_overage_is_reported_without_relabeling(self):
        report = review([row(status='Declined', ceiling='100', limit='0')])
        self.assertEqual(report['ceiling_review_cases'][0]['status'], 'Declined')
        self.assertEqual(set(report['ceiling_review_cases'][0]['reasons']), {
            'ceiling_above_recorded_policy_limit', 'declined_with_positive_ceiling',
        })

    def test_matching_preserves_leading_zeros(self):
        folder = {'id': 'f', 'name': '1', 'mimeType': 'application/vnd.google-apps.folder'}
        report = review([row()], [folder])
        self.assertEqual(report['evidence']['missing_claims'][0]['tracking_number'], '001')

    def test_invalid_and_out_of_order_dates_are_reported(self):
        first, second = row(), row(tracking='002')
        first['Approval Date'] = '12/31/23'
        second['Posted Date'] = 'not-a-date'
        report = review([first, second])
        self.assertEqual({item['reason'] for item in report['date_anomalies']},
                         {'approval_before_claim', 'invalid_Posted Date'})


if __name__ == '__main__':
    unittest.main()

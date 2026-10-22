import subprocess
import sys
import unittest
from datetime import date
from decimal import Decimal

from backend.prediction.features import calendar_features, row_features
from backend.prediction.parsing import canonical_id, money_value, recorded_date, recorded_money


class PredictionFeaturesTests(unittest.TestCase):
    def test_shared_helpers_import_without_research_or_evaluation(self):
        result = subprocess.run([sys.executable, '-c', '''
import sys
from backend.prediction import features, parsing, research_documents
for name in ('experiment', 'evaluation', 'retrospective', 'tuning', 'baseline', 'sklearn'):
    assert not any(key == name or key.startswith(name + '.') or
                   key == 'backend.prediction.' + name for key in sys.modules), name
'''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_parsing_preserves_identity_and_explicit_states(self):
        self.assertEqual(canonical_id(' 00053 '), '53')
        self.assertEqual(canonical_id(' claim-a '), 'claim-a')
        with self.assertRaisesRegex(ValueError, 'Empty claim ID'):
            canonical_id(' ')
        self.assertEqual(money_value('1234.50'), Decimal('1234.50'))
        self.assertEqual(recorded_money('$1,234.50'), (1234.5, 'present'))
        self.assertEqual(recorded_money('0'), (0.0, 'present'))
        self.assertEqual(recorded_money(' '), (None, 'missing'))
        for value in ('-1', '1.001', 'NaN', 'Infinity', '$1,23'):
            with self.subTest(value=value):
                self.assertIsNone(money_value(value))
                self.assertEqual(recorded_money(value), (None, 'invalid'))
        for value in ('02/29/24', '02/29/2024', '2024-02-29'):
            self.assertEqual(recorded_date(value), (date(2024, 2, 29), 'present'))
        self.assertEqual(recorded_date(None), (None, 'missing'))
        self.assertEqual(recorded_date('02/29/23'), (None, 'invalid'))

    def test_feature_values_and_input_objects_are_preserved(self):
        row = {'Monthly Rent': '0', 'Amount of Claim': 'bad', 'Max Benefit': None,
               'Claim Date': '01/01/24', 'Lease Start Date': '01/01/24',
               'Lease End Date': '01/11/24', 'Move-Out Date': '01/03/24',
               'Termination Type': ' Expired '}
        original = dict(row)
        expected = {'rent:state=present': 1.0, 'rent': 0.0,
                    'request:state=invalid': 1.0, 'limit:state=missing': 1.0,
                    'Claim Date:state=present': 1.0, 'Lease Start Date:state=present': 1.0,
                    'Lease End Date:state=present': 1.0, 'Move-Out Date:state=present': 1.0,
                    'lease_days': 10.0, 'days_after_move_out': -2.0,
                    'lease_days_remaining': 8.0, 'termination=Expired': 1.0}
        self.assertEqual(row_features(row), expected)
        self.assertEqual(row_features(row, without_limit=True),
                         {key: value for key, value in expected.items() if not key.startswith('limit')})
        calendar = calendar_features(row, expected)
        self.assertEqual(calendar['Claim Date:ordinal'], 738886.0)
        self.assertEqual(calendar['Claim Date:month_sin'], 0.0)
        self.assertEqual(calendar['Claim Date:month_cos'], 1.0)
        self.assertEqual(row, original)
        self.assertEqual(row_features(row), expected)
        self.assertNotIn('Claim Date:ordinal', expected)


if __name__ == '__main__':
    unittest.main()

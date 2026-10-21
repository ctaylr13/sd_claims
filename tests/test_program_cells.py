import unittest
from types import SimpleNamespace

from backend.extraction.program_cells import amount_in_cell, canonical_quote, read_chars, read_waiver_cells


def chars(text, x=10, y=10, color=(0, 0, 0)):
    return [dict(text=c, x0=x+5*i, x1=x+5*(i+1), top=y, bottom=y+10,
                 non_stroking_color=color, fontname='native') for i,c in enumerate(text)]


def source(yes='X YES', no='NO', fee='$50.00 per month', deposit='$2,850.00', overlays=None):
    header = (10, 10, 500, 30)
    cells = [header, (10,30,100,70), (100,30,220,70), (220,30,500,70),
             (10,70,100,110), (100,70,220,110), (220,70,500,110)]
    content = chars(yes,20,40)+chars(no,20,80)+chars(fee,110,40)
    content += chars('You waive payment of the security deposit of',225,32)+chars(deposit+' in lieu',225,52)
    content += overlays or []
    return SimpleNamespace(chars=content, find_tables=lambda: [SimpleNamespace(cells=cells)],
        crop=lambda region: SimpleNamespace(extract_text=lambda: 'Security Deposit Waiver (must check one)' if region==header else ''))


class ProgramCellsTests(unittest.TestCase):
    def read_amount(self, content):
        value, mapping, _ = read_chars(content, (0,0,600,120))
        return amount_in_cell(value,mapping)

    def test_filled_overlay_removes_underlines_without_using_proposed_value(self):
        content = chars('$',10)+chars('______',15)+chars('50.00',20,11)
        self.assertEqual(self.read_amount(content)['value'],'50.00')
        text, _, audit = read_chars(content,(0,0,600,120))
        self.assertNotIn('_',text)
        self.assertEqual(audit['discarded_underlines'],6)

    def test_missing_digit_space_cannot_be_reconstructed_as_smaller_amount(self):
        for content in (chars('$5',10)+chars('0.00',30), chars('$1,',10)+chars('50.00',35)):
            self.assertIsNone(self.read_amount(content))

    def test_blank_multiple_malformed_and_signed_values(self):
        for text in ('$______ per month','$50 or $60','$1,23.00','$50.0','$50.000','$50 60','$+50.00'):
            self.assertIsNone(self.read_amount(chars(text)),text)
        self.assertEqual(self.read_amount(chars('$-50.00'))['value'],'-50.00')

    def test_white_signature_tags_are_audited_and_never_selection_marks(self):
        page = source(overlays=chars('#0_1001#',20,38,(1,1,1)))
        records, audits = read_waiver_cells(page)
        self.assertEqual([(r['key'],r['value']) for r in records],
            [('program_election','yes'),('program_monthly_fee','50.00'),('waived_deposit','2850.00')])
        self.assertEqual(audits[0]['yes']['discarded_white_chars'],8)
        records, _ = read_waiver_cells(source(yes='YES',overlays=chars('X',20,38,(1,1,1))))
        self.assertEqual(records,[])

    def test_unchecked_both_selected_and_unrecognized_mark_hold(self):
        for yes,no in (('YES','NO'),('X YES','X NO'),('? YES','NO')):
            self.assertEqual(read_waiver_cells(source(yes=yes,no=no))[0],[])

    def test_no_selection_does_not_assert_fees_or_deposit(self):
        records, _ = read_waiver_cells(source(yes='YES',no='X NO'))
        self.assertEqual([(r['key'],r['value']) for r in records],[('program_election','no')])

    def test_blank_and_competing_fields_keep_only_election(self):
        for fee,deposit in (('$_____ per month','$_____'),('$50 $60 per month','$2,850 or $3,000')):
            records, _ = read_waiver_cells(source(fee=fee,deposit=deposit))
            self.assertEqual([r['key'] for r in records],['program_election'])

    def test_wrong_heading_cannot_create_program_facts(self):
        page = source()
        page.crop = lambda region: SimpleNamespace(extract_text=lambda:'Liability Insurance')
        self.assertEqual(read_waiver_cells(page)[0],[])

    def test_quote_normalization_preserves_sign_and_numeric_identity(self):
        self.assertEqual(canonical_quote('$2,850.00'),canonical_quote('$2850'))
        self.assertNotEqual(canonical_quote('$-50'),canonical_quote('$50'))
        self.assertNotEqual(canonical_quote('$50'),canonical_quote('$500'))


if __name__=='__main__':
    unittest.main()

import unittest

from backend.extraction.models import Document, Page, Word
from backend.extraction.statement_rows import dated_charges


def word(value, x, y, width=60):
    return Word(text=value, bbox=(float(x), float(y), float(x+width), float(y+10)))


def page(number, rows):
    return Page(number=number, width=612, height=792, method='native_text', state='readable',
                words=[w for row in rows for w in row])


def statement():
    header = [word('Date', 20, 40), word('Description', 110, 40), word('Amount', 400, 40), word('Due', 470, 40)]
    first = page(1, [[word('Current Unpaid Charges', 20, 20, 200)], header,
                     [word('06/22/2022', 20, 60, 75), word('Repair', 110, 60, 150), word('$100.00', 430, 60)]])
    second = page(2, [[word('06/24/2022', 20, 20, 75), word('Maintenance', 110, 20, 150), word('$200.00', 430, 20)],
                      [word('06/24/2022', 20, 40, 75), word('Repair', 110, 40, 150), word('$300.00', 430, 40)]])
    return Document(document_id='statement', kind='move_out_statement', sha256='a'*64, original_path=None, pages=[first, second])


class StatementRowsTests(unittest.TestCase):
    def test_charge_section_and_columns_carry_to_adjacent_continuation(self):
        entries = dated_charges(statement())
        self.assertEqual([e.amount.observations[0].value for e in entries], ['100.00', '200.00', '300.00'])
        self.assertEqual(entries[1].posting_date.observations[0].value, '2022-06-24')
        self.assertEqual(entries[1].citations[0].page, 2)
        self.assertEqual(entries[1].citations[1].page, 1)
        self.assertIn('Current Unpaid Charges', entries[1].citations[1].quote)

    def test_no_charge_heading_means_no_charge_assertion(self):
        doc = statement()
        doc.pages[0].words = [w for w in doc.pages[0].words if w.bbox[1] != 20]
        self.assertEqual(dated_charges(doc), [])

    def test_summary_ends_section_and_is_not_a_transaction(self):
        doc = statement()
        doc.pages[0].words += [word('Total Balance Due:', 20, 80, 200), word('$100.00', 430, 80)]
        self.assertEqual(len(dated_charges(doc)), 1)

    def test_broken_page_number_alignment_or_unreadable_page_stops_carry(self):
        for change in ('page', 'alignment', 'ocr'):
            doc = statement()
            if change == 'page':
                doc.pages[1].number = 3
            elif change == 'alignment':
                doc.pages[1].words[0].bbox = (40., 20., 115., 30.)
            else:
                doc.pages[1].method = 'ocr'
                doc.pages[1].state = 'partial'
            self.assertEqual(len(dated_charges(doc)), 1)

    def test_multiple_amounts_and_payment_descriptions_remain_unparsed(self):
        for change in ('extra_amount', 'payment', 'wrong_column'):
            doc = statement()
            if change == 'extra_amount':
                doc.pages[1].words.insert(2, word('$50.00', 300, 20))
            elif change == 'payment':
                doc.pages[1].words[1].text = 'Payment received'
            else:
                doc.pages[1].words[2].bbox = (300., 20., 360., 30.)
            self.assertEqual([e.amount.observations[0].value for e in dated_charges(doc)], ['100.00', '300.00'])

    def test_payment_header_cannot_inherit_charge_semantics(self):
        doc = statement()
        doc.pages[1].words += [word('Date', 20, 60), word('Description', 110, 60), word('Payments', 400, 60),
                               word('06/25/2022', 20, 80, 75), word('Transfer', 110, 80, 150), word('$400.00', 430, 80)]
        self.assertEqual(len(dated_charges(doc)), 3)

    def test_wrong_document_role_or_document_failure_cannot_produce_charges(self):
        doc = statement()
        doc.kind = 'lease'
        self.assertEqual(dated_charges(doc), [])
        doc = statement()
        doc.errors.append('failed')
        self.assertEqual(dated_charges(doc), [])


if __name__ == '__main__':
    unittest.main()

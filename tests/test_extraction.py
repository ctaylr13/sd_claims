import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from backend.extraction import extract_claim
from backend.extraction.facts import fact, money
from backend.extraction.financial import ledger, move_out
from backend.extraction.lease import effective_rent, lease
from backend.extraction.models import CellCitation, DocumentInput, Manifest, Observation, Word
from backend.extraction.pdf import extract_pdf, ocr_words


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = 0

    def pdf(self, rows, kind='move_out_statement', *, next_pages=(), ocr=None, force_ocr_pages=()):
        self.index += 1
        path = self.root / f'fixture-{self.index}.pdf'
        canvas = Canvas(str(path), pagesize=(612, 792))
        canvas.setFont('Helvetica', 11)
        for x, y, content in rows:
            canvas.drawString(x, y, content)
        for page in next_pages:
            canvas.showPage()
            canvas.setFont('Helvetica', 11)
            for x, y, content in page:
                canvas.drawString(x, y, content)
        canvas.save()
        item = DocumentInput(path=str(path), document_id=f'fixture-{self.index}', kind=kind,
                             force_ocr_pages=list(force_ocr_pages))
        kwargs = {'ocr': ocr} if ocr else {}
        return extract_pdf(item, self.root / f'artifacts-{self.index}', **kwargs)

    def test_statement_preserves_categories_credits_periods_and_distinct_dates(self):
        doc = self.pdf([
            (30, 760, 'Move Out Date: June 30, 2025'),
            (30, 740, 'KEYS: Issued: 2 Returned: 2 Date: March 7, 2025'),
            (30, 720, 'Lease Expires: June 30, 2025'),
            (30, 700, 'Credits'),
            (30, 680, 'Management Held Security Deposit $1,000.00'),
            (30, 660, 'Total Credits $1,000.00'),
            (30, 640, 'Charges'),
            (30, 620, 'Rent June 2025 $2,000.00'),
            (30, 600, 'Final utility bill 02/16/23 - 02/21/23 $198.56'),
            (30, 580, 'Cleaning $100.00'),
            (30, 560, 'Total Charges $2,298.56'),
            (30, 540, 'Amount Due $1,298.56'),
            (30, 520, 'Requested insurer reimbursement: $900.00'),
        ])
        result = move_out(doc)
        self.assertEqual([e.kind for e in result.entries], ['credit', 'charge', 'charge', 'charge'])
        self.assertEqual([e.category for e in result.entries], ['deposit', 'rent', 'utilities', 'cleaning'])
        self.assertEqual(result.entries[1].service_period.observations[0].value, '2025-06')
        self.assertEqual(result.entries[2].service_period.observations[0].value, '2023-02-16/2023-02-21')
        self.assertEqual(result.fields['tenant_balance_due'].observations[0].value, '1298.56')
        self.assertEqual(result.fields['requested_insurer_reimbursement'].observations[0].value, '900.00')
        self.assertEqual(result.fields['reported_move_out'].observations[0].value, '2025-06-30')
        self.assertEqual(result.fields['key_return'].observations[0].value, '2025-03-07')
        self.assertEqual(result.fields['confirmed_possession_end'].state, 'missing')
        cite = result.entries[2].amount.observations[0].citations[0]
        self.assertEqual(cite.sha256, hashlib.sha256(Path(doc.original_path).read_bytes()).hexdigest())
        self.assertEqual(cite.page, 1)
        self.assertIn('198.56', cite.quote)
        self.assertGreater(cite.bbox[2], cite.bbox[0])
        self.assertTrue(Path(doc.pages[0].image_path).is_file())

    def test_wrapped_statement_description_belongs_to_its_amount(self):
        result = move_out(self.pdf([
            (30, 750, 'Charges'),
            (30, 720, 'Dismantle playset and replace interior light'), (480, 714, '$845.00'),
            (30, 708, 'bulbs and remove anchors'),
            (30, 680, 'Carpet Cleaning'), (480, 680, '$385.00'),
            (30, 650, 'Total Charges $1,230.00'),
        ]))
        self.assertEqual(len(result.entries), 2)
        self.assertIn('bulbs', result.entries[0].description)
        self.assertNotIn('bulbs', result.entries[1].description)
        self.assertEqual(result.entries[1].amount.observations[0].value, '385.00')

    def ledger_pdf(self):
        self.index += 1
        path = self.root / f'ledger-{self.index}.pdf'
        canvas = Canvas(str(path), pagesize=(612, 792))
        canvas.setFont('Helvetica', 9)
        for x in (30, 85, 155, 205, 415, 475, 535, 600):
            canvas.line(x, 520, x, 710)
        for y in (710, 690, 660, 580, 550, 520):
            canvas.line(30, y, 600, y)
        for x, title in [(32, 'Transaction'), (100, 'Date'), (160, 'Ref #'), (210, 'Description'),
                         (420, 'Charges'), (480, 'Payments'), (540, 'Balance')]:
            canvas.drawString(x, 700, title)
        for x, y, content in [
            (30, 760, 'Balance as of 06/30/2025'), (30, 740, '$25.00'),
            (35, 675, 'Charge'), (90, 675, '06/01/2025'), (210, 675, 'Rent June 2025'),
            (420, 675, '$100.00'), (540, 675, '$100.00'),
            (35, 625, 'Payment'), (90, 625, '06/02/2025'), (210, 649, 'Payment of $999.00 applied'),
            (210, 635, 'to old charges; full description'), (210, 607, 'continues below posting date'),
            (480, 625, '$50.00'), (540, 625, '$50.00'),
            (35, 565, 'Credit'), (90, 565, '06/03/2025'), (210, 565, 'Credit adjustment'),
            (480, 565, '$25.00'), (540, 565, '$25.00'),
            (35, 535, 'Charge'), (90, 535, '06/04/2025'), (210, 535, 'Returned payment NSF'),
            (420, 535, '$50.00'), (540, 535, '$75.00'),
        ]:
            canvas.drawString(x, y, content)
        canvas.save()
        return extract_pdf(DocumentInput(path=str(path), document_id='ledger', kind='ledger'), self.root / 'ledger-artifacts')

    def test_ledger_uses_column_geometry_and_does_not_count_allocation_amounts(self):
        result = ledger(self.ledger_pdf())
        self.assertEqual([e.kind for e in result.entries], ['charge', 'payment', 'credit', 'reversal'])
        self.assertEqual([e.amount.observations[0].value for e in result.entries], ['100.00', '50.00', '25.00', '50.00'])
        self.assertIn('continues below', result.entries[1].description)
        self.assertNotIn('Payment of', result.entries[0].description)
        self.assertEqual(result.entries[1].source_columns, {'Payments': '$50.00', 'Balance': '$50.00'})
        self.assertEqual(result.entries[1].running_balance.observations[0].value, '50.00')
        self.assertEqual(result.entries[0].service_period.observations[0].value, '2025-06')
        self.assertEqual(result.entries[1].service_period.state, 'missing')
        self.assertEqual(result.fields['requested_insurer_reimbursement'].state, 'missing')

    def test_partial_period_retains_text_without_inventing_year(self):
        result = move_out(self.pdf([(30, 750, 'Charges'), (30, 720, 'Water 07/27 - 08/22 $60.26'),
                                    (30, 700, 'Amount Due $60.26')]))
        period = result.entries[0].service_period
        self.assertEqual(period.state, 'unreviewed')
        self.assertEqual(period.observations[0].raw_value, '07/27 - 08/22')
        self.assertIsNone(period.observations[0].value)

    def test_merged_table_row_does_not_merge_financial_columns(self):
        doc = self.ledger_pdf()
        doc.pages[0].table_cells = [(30, top, 600, bottom) for _, top, _, bottom in doc.pages[0].table_cells]
        result = ledger(doc)
        self.assertEqual([e.amount.observations[0].value for e in result.entries], ['100.00', '50.00', '25.00', '50.00'])

    def test_dash_posting_dates_and_numeric_service_month(self):
        doc = self.ledger_pdf()
        for word in doc.pages[0].words:
            if word.text == '06/01/2025':
                word.text = '06-01-2025'
            if word.text == 'June':
                word.text = '06-2025'
        result = ledger(doc)
        self.assertEqual(result.entries[0].posting_date.observations[0].value, '2025-06-01')
        self.assertEqual(result.entries[0].service_period.observations[0].value, '2025-06')

    def test_deposit_forfeiture_keeps_charge_kind(self):
        doc = self.ledger_pdf()
        for word in doc.pages[0].words:
            if word.text == 'Rent':
                word.text = 'Security Deposit Forfeiture'
        entry = ledger(doc).entries[0]
        self.assertEqual(entry.kind, 'charge')
        self.assertEqual(entry.category, 'deposit')

    def test_negative_payment_notation_and_posted_nsf_fee(self):
        doc = self.ledger_pdf()
        for word in doc.pages[0].words:
            if word.text == '$50.00' and word.bbox[0] == 480:
                word.text = '$-50.00'
            if word.text == 'NSF':
                word.text = 'NSF Fee'
        entries = ledger(doc).entries
        self.assertEqual(entries[1].amount.observations[0].value, '-50.00')
        self.assertEqual(entries[1].source_columns['Payments'], '$-50.00')
        self.assertEqual(entries[-1].kind, 'charge')
        self.assertEqual(entries[-1].category, 'returned_payment_fee')

    def test_two_populated_financial_columns_remain_ambiguous(self):
        doc = self.ledger_pdf()
        amount = next(word for word in doc.pages[0].words if word.text == '$100.00')
        doc.pages[0].words.append(Word(text='$20.00', bbox=(480, amount.bbox[1], 510, amount.bbox[3])))
        entry = ledger(doc).entries[0]
        self.assertEqual(entry.kind, 'unclassified')
        self.assertEqual(entry.amount.state, 'unreviewed')
        self.assertEqual([obs.value for obs in entry.amount.observations], ['100.00', '20.00'])

    def test_renewal_rent_selection_and_conflicts(self):
        def term(start, end, amount):
            return lease(self.pdf([(30, 750, f'Lease Start Date: {start}'),
                                   (30, 730, f'Lease End Date: {end}'),
                                   (30, 710, f'Monthly Rental Amount: {amount}'),
                                   (30, 690, '3. LATE PAYMENT'),
                                   (30, 670, 'Late charges are five percent of monthly rent.')], kind='renewal'))
        old = term('July 1, 2023', 'June 30, 2024', '$2,800.00')
        new = term('July 1, 2024', 'June 30, 2025', '$2,895.00')
        selected = effective_rent([old, new], '2025-03-07')
        self.assertEqual(selected.state, 'present')
        self.assertEqual(selected.observations[0].value, '2895.00')
        self.assertEqual(len(selected.observations[0].citations), 3)
        self.assertEqual(effective_rent([old, new], '2024-06-30').observations[0].value, '2800.00')
        overlap = term('July 1, 2024', 'June 30, 2025', '$3,000.00')
        self.assertEqual(effective_rent([new, overlap], '2025-03-07').state, 'conflicting')
        self.assertEqual(new.fields['reported_move_out'].state, 'missing')
        self.assertIn('five percent', new.clauses[0].raw_value)

    def test_spaced_form_values_and_prose_terms(self):
        result = lease(self.pdf([(30, 750, 'Lease Start Date: 1 1 / 2 3 / 2 0 2 2'),
                                 (30, 730, 'Lease End Date: 1 1 / 3 0 / 2 0 2 3'),
                                 (30, 710, 'Monthly Rental Amount: $ 1 8 5 0 . 0 0')], kind='lease'))
        term = result.rent_terms[0]
        self.assertEqual(term.start.observations[0].value, '2022-11-23')
        self.assertEqual(term.monthly_rent.observations[0].value, '1850.00')
        prose = lease(self.pdf([(30, 750, 'The Agreement shall commence on the 01/22/2025, and end on 02/28/2025.'),
                                (30, 730, 'Rent shall be payable in monthly installments of $1,150.00.')], kind='lease'))
        self.assertEqual(prose.rent_terms[0].end.observations[0].value, '2025-02-28')

    def test_undated_and_reversed_terms_prevent_effective_rent_selection(self):
        undated = lease(self.pdf([(30, 750, 'Monthly Rent: $2,000.00')], kind='lease'))
        self.assertEqual(effective_rent([undated], '2025-03-07').state, 'unreviewed')
        reversed_term = lease(self.pdf([(30, 750, 'Lease Start Date: 06/30/2025'),
                                        (30, 730, 'Lease End Date: 06/01/2025'),
                                        (30, 710, 'Monthly Rent: $2,000.00')], kind='lease'))
        self.assertEqual(reversed_term.rent_terms[0].end.state, 'unreviewed')
        self.assertEqual(effective_rent([reversed_term], '2025-06-10').state, 'unreviewed')

    def test_contractual_clause_continues_across_pages(self):
        result = lease(self.pdf([(30, 750, '3. TERMINATION'),
                                 (30, 730, 'Tenant must give written notice before leaving.')], kind='lease',
                                next_pages=[[(30, 750, 'Notice must be received at least sixty days before lease end.'),
                                             (30, 730, '4. OCCUPANTS'),
                                             (30, 710, 'Occupancy is limited to named tenants.')]]))
        self.assertEqual(len(result.clauses), 2)
        self.assertEqual(result.clauses[1].citations[0].page, 2)
        self.assertIn('sixty days', result.clauses[1].raw_value)
        self.assertNotIn('Occupancy is limited', result.clauses[1].raw_value)

    def test_possession_confirmation_and_conflicting_move_out_remain_separate(self):
        result = move_out(self.pdf([(30, 750, 'Move Out Date: 06/30/2025'),
                                    (30, 730, 'Move Out Date: 07/01/2025'),
                                    (30, 710, 'Key Return Date: 06/28/2025'),
                                    (30, 690, 'Confirmed Possession End: 06/29/2025')]))
        self.assertEqual(result.fields['reported_move_out'].state, 'conflicting')
        self.assertEqual(result.fields['key_return'].observations[0].value, '2025-06-28')
        self.assertEqual(result.fields['confirmed_possession_end'].observations[0].value, '2025-06-29')

    def test_ocr_fallback_is_per_page_preserves_native_attempt_and_errors(self):
        def unavailable(*args):
            raise FileNotFoundError('tesseract')
        doc = self.pdf([(30, 750, 'Amount Due $100.00 readable statement')],
                       next_pages=[[(30, 750, 'Sample')]], ocr=unavailable)
        self.assertEqual(doc.pages[0].method, 'native_text')
        self.assertEqual(doc.pages[1].method, 'ocr')
        self.assertEqual(doc.pages[1].state, 'unreadable')
        self.assertEqual(doc.pages[1].native_words[0].text, 'Sample')
        self.assertTrue(doc.pages[1].errors)
        result = move_out(doc)
        self.assertEqual(result.fields['requested_insurer_reimbursement'].state, 'unreadable')
        self.assertEqual(result.fields['tenant_balance_due'].observations[0].value, '100.00')

    def test_explicit_unusable_text_can_force_ocr(self):
        called = []
        def ocr(*args):
            called.append(args)
            return [Word(text='Amount Due $200.00 readable OCR statement', bbox=(30, 30, 500, 50), confidence=75)]
        doc = self.pdf([(30, 750, 'Amount Due $100.00 readable native statement')], ocr=ocr, force_ocr_pages=[1])
        self.assertEqual(len(called), 1)
        self.assertIn('100.00', doc.pages[0].native_text)
        self.assertEqual(doc.pages[0].fallback_reason, 'explicit_unusable_text')
        observed = move_out(doc).fields['tenant_balance_due'].observations[0]
        self.assertEqual(observed.value, '200.00')
        self.assertEqual(observed.citations[0].method, 'ocr')
        self.assertTrue(any('confidence' in limitation for limitation in observed.limitations))

    def test_tsv_quote_tokens_do_not_swallow_later_ocr_records(self):
        tsv = ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
               '5\t1\t1\t1\t1\t1\t10\t20\t5\t10\t80\t"\n'
               '5\t1\t1\t1\t1\t2\t20\t20\t50\t10\t90\t$100.00\n'
               '5\t1\t1\t1\t1\t3\t100\t20\t60\t10\t90\t06/30/2025\n')
        with patch('backend.extraction.pdf.subprocess.run', return_value=SimpleNamespace(stdout=tsv)):
            words = ocr_words(self.root/'image.png', 612, 792, (612,792))
        self.assertEqual([word.text for word in words], ['"', '$100.00', '06/30/2025'])
        self.assertEqual(words[1].bbox, (20,20,70,30))

    def test_report_retains_request_conflicts_and_missing_source(self):
        first = self.pdf([(30, 750, 'Requested insurer reimbursement: $1,000.00')])
        second = self.pdf([(30, 750, 'Requested insurer reimbursement: $1,200.00')])
        manifest = Manifest(claim_id='synthetic', documents=[
            DocumentInput(path=doc.original_path, document_id=doc.document_id, kind=doc.kind)
            for doc in (first, second)
        ], recorded_requests=[{'source_id': 'sheet', 'source_version': '7', 'row': 2, 'quote': '1500'}])
        report = json.loads(extract_claim(manifest, self.root / 'reports').read_text())
        request = report['requested_insurer_reimbursement']
        self.assertEqual(request['state'], 'conflicting')
        self.assertEqual([o['value'] for o in request['observations']], ['1000.00', '1200.00', '1500.00'])
        self.assertEqual(request['observations'][2]['citations'][0]['column'], 'Amount of Claim')
        self.assertFalse(report['training_eligible'])
        manifest.documents[0].path = str(self.root / 'missing.pdf')
        report = json.loads(extract_claim(manifest, self.root / 'reports').read_text())
        self.assertIn('source_read_failed', report['documents'][0]['document']['errors'][0])
        self.assertEqual(report['documents'][0]['fields']['tenant_balance_due']['state'], 'unreadable')

    def test_money_uses_decimal_strings_and_preserves_signs(self):
        for raw, expected in [('($1,088.00)', '-1088.00'), ('-$27.00', '-27.00'), ('$-27.00', '-27.00'), ('$0.00', '0.00'),
                              ('$100', '100.00'), ('1234', '1234.00'), ('$ 1 8 5 0 . 0 0', '1850.00')]:
            self.assertEqual(money(raw), expected)
        for raw in ('$1,00', '$12.999', '0O.00', '(12.00'):
            self.assertIsNone(money(raw))

    def test_blank_request_and_zero_request_are_distinct(self):
        def request(raw):
            return Observation(raw_value=raw, value=money(raw), citations=[
                CellCitation(source_id='sheet', source_version=None, row=2, quote=raw),
            ])
        blank = fact([request('')])
        zero = fact([request('$0.00')])
        self.assertEqual(blank.state, 'missing')
        self.assertEqual(blank.observations[0].raw_value, '')
        self.assertEqual(zero.state, 'present')
        self.assertEqual(zero.observations[0].value, '0.00')
        self.assertEqual(fact([request('$10O.00')]).state, 'unreviewed')

    def test_unknown_ledger_layout_keeps_unparsed_evidence(self):
        result = ledger(self.pdf([(30, 750, 'Unfamiliar ledger export'), (30, 720, 'Rent $100.00')], kind='ledger'))
        self.assertEqual(result.entries, [])
        self.assertIn('100.00', result.unparsed[0].quote)

    def test_invalid_pdf_is_preserved_with_explicit_failure(self):
        path = self.root / 'bad.pdf'
        path.write_bytes(b'not a PDF')
        doc = extract_pdf(DocumentInput(path=str(path), document_id='bad', kind='lease'), self.root / 'bad-artifacts')
        self.assertEqual(Path(doc.original_path).read_bytes(), b'not a PDF')
        self.assertTrue(doc.errors)
        self.assertEqual(lease(doc).rent_terms[0].monthly_rent.state, 'unreadable')

    @unittest.skipUnless(shutil.which('tesseract'), 'Tesseract is an optional system dependency')
    def test_real_ocr_on_image_only_pdf_with_scaled_citations(self):
        image = Image.new('RGB', (1700, 2200), 'white')
        drawing = ImageDraw.Draw(image)
        font = ImageFont.truetype('DejaVuSans.ttf', 38) if Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf').exists() else ImageFont.load_default(size=38)
        drawing.text((100, 150), 'Amount Due $125.00', fill='black', font=font)
        drawing.text((100, 250), 'Move Out Date: 06/30/2025', fill='black', font=font)
        path = self.root / 'scan.pdf'
        canvas = Canvas(str(path), pagesize=(612, 792))
        canvas.drawImage(ImageReader(image), 0, 0, width=612, height=792)
        canvas.save()
        doc = extract_pdf(DocumentInput(path=str(path), document_id='scan', kind='move_out_statement'), self.root / 'scan-artifacts')
        self.assertEqual(doc.pages[0].method, 'ocr')
        self.assertEqual(doc.pages[0].native_text, '')
        result = move_out(doc)
        observation = result.fields['tenant_balance_due'].observations[0]
        self.assertEqual(observation.value, '125.00')
        self.assertLess(observation.citations[0].bbox[2], 612)
        self.assertLess(observation.citations[0].bbox[3], 792)

        canvas = Canvas(str(path), pagesize=(612, 792))
        canvas.drawImage(ImageReader(image), 0, 0, width=612, height=792)
        canvas.drawString(30, 770, 'Financial Statement exported from management system')
        canvas.save()
        mixed = extract_pdf(DocumentInput(path=str(path), document_id='mixed', kind='move_out_statement'), self.root / 'mixed-artifacts')
        self.assertEqual(mixed.pages[0].fallback_reason, 'image_dominant_page_with_sparse_native_text')
        self.assertIn('Financial Statement', mixed.pages[0].native_text)
        self.assertEqual(move_out(mixed).fields['tenant_balance_due'].observations[0].value, '125.00')


if __name__ == '__main__':
    unittest.main()

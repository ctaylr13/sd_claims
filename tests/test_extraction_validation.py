import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.extraction.facts import fact
from backend.extraction.models import Citation, Document, DocumentFacts, FinancialEntry, Observation, Page, Word
from backend.extraction.validation import annotation_checks, ledger_arithmetic, statement_arithmetic, validate_document, validate_report


class ExtractionValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.pdf'
        self.source.write_bytes(b'synthetic retained source')
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def document(self, rows=(), *, kind='ledger', document_id='synthetic', fields=None):
        page = Page(number=1, width=612, height=792, method='native_text', state='readable')
        doc = Document(document_id=document_id, kind=kind, sha256=self.sha,
                       original_path=str(self.source), pages=[page])
        result = DocumentFacts(document=doc, fields={})
        def observed(raw, x, y):
            word = Word(text=raw, bbox=(x, y, x+45, y+8))
            page.words.append(word)
            cite = Citation(document_id=document_id, sha256=self.sha, page=1, quote=raw,
                            bbox=word.bbox, method='native_text')
            return fact([Observation(raw_value=raw, value=raw, citations=[cite])])
        for index, (date, amount, balance, entry_kind) in enumerate(rows):
            y = 100+index*25
            charge, payment = entry_kind == 'charge', entry_kind == 'payment'
            amount_fact = observed(amount, 430 if charge else 480, y)
            date_fact, balance_fact = observed(date, 100, y), observed(balance, 540, y)
            entry = FinancialEntry(entry_id=f'{document_id}:r{index}', kind=entry_kind, category='other',
                                   description='Synthetic transaction', posting_date=date_fact, service_period=fact([]),
                                   amount=amount_fact, running_balance=balance_fact,
                                   citations=[date_fact.observations[0].citations[0]],
                                   source_columns={('Charges' if charge else 'Payments'): amount, 'Balance': balance})
            result.entries.append(entry)
        for index, (name, raw) in enumerate((fields or {}).items()):
            result.fields[name] = observed(raw, 30, 20+index*12)
        return result

    def test_oldest_first_running_balance_with_payment(self):
        result = self.document([('2025-01-01', '100.00', '100.00', 'charge'),
                                ('2025-01-02', '25.00', '75.00', 'payment'),
                                ('2025-01-03', '10.00', '85.00', 'charge')], fields={'opening_balance': '0.00', 'balance': '85.00'})
        checks = ledger_arithmetic(result)
        self.assertTrue(all(item['status'] == 'passed' for item in checks))

    def test_newest_first_and_negative_balance_use_decimal_arithmetic(self):
        result = self.document([('2025-01-03', '0.01', '0.00', 'charge'),
                                ('2025-01-02', '0.02', '-0.01', 'payment'),
                                ('2025-01-01', '0.01', '0.01', 'charge')])
        checks = [item for item in ledger_arithmetic(result) if item['check'] == 'running_balance']
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(item['status'] == 'passed' for item in checks))

    def test_wrong_balance_is_flagged_without_changing_observation(self):
        result = self.document([('2025-01-01', '100.00', '100.00', 'charge'),
                                ('2025-01-02', '25.00', '95.00', 'payment')])
        before = result.model_dump_json()
        checks = ledger_arithmetic(result)
        self.assertEqual(checks[0]['status'], 'failed')
        self.assertEqual(checks[0]['expected'], '75.00')
        self.assertEqual(checks[0]['observed'], '95.00')
        self.assertEqual(result.model_dump_json(), before)

    def test_unknown_order_does_not_choose_direction_to_make_arithmetic_pass(self):
        result = self.document([('2025-01-01', '100.00', '100.00', 'charge'),
                                ('2025-01-01', '25.00', '75.00', 'payment')])
        self.assertEqual(ledger_arithmetic(result)[0]['status'], 'skipped')

    def test_cropped_balance_is_not_reconstructed_even_when_arithmetic_would_match(self):
        result = self.document([('2025-01-01', '100.00', '100.00', 'charge'),
                                ('2025-01-02', '25.00', '75.00', 'payment')])
        regions = [dict(page=1, bbox=[535,90,600,150], state='cropped', reason='Missing right-edge pixels')]
        validation = validate_document(result, regions=regions)
        checks = [item for item in validation['checks'] if item['check'] == 'running_balance']
        self.assertEqual(checks[0]['status'], 'skipped')
        flag = next(issue for issue in validation['issues'] if issue['code'] == 'source_cropped')
        self.assertIsNone(flag['value'])
        self.assertEqual(result.entries[1].running_balance.observations[0].value, '75.00')

    def test_unparsed_gap_blocks_reconciliation_across_missing_row(self):
        result = self.document([('2025-01-01', '100.00', '100.00', 'charge'),
                                ('2025-01-02', '25.00', '95.00', 'payment')])
        result.unparsed.append(Citation(document_id='synthetic', sha256=self.sha, page=1, quote='Unreadable row',
                                       bbox=(30,110,580,120), method='native_text'))
        self.assertEqual(ledger_arithmetic(result)[0]['status'], 'skipped')

    def test_statement_uses_applied_credit_once_and_preserves_refund_sign(self):
        result = self.document([('2025-01-01', '100.00', '0.00', 'charge'),
                                ('2025-01-02', '200.00', '0.00', 'credit')], kind='move_out_statement',
                               fields={'total_charges':'100.00', 'total_credits':'200.00', 'deposit_held':'200.00', 'resident_refund':'100.00'})
        net = next(item for item in statement_arithmetic(result) if item['check'] == 'net_due')
        self.assertEqual(net['status'], 'passed')
        self.assertEqual(net['expected'], '-100.00')

    def test_statement_does_not_assume_missing_credit_or_deposit_is_zero(self):
        result = self.document(kind='move_out_statement', fields={'total_charges':'100.00','tenant_balance_due':'100.00'})
        self.assertEqual(statement_arithmetic(result)[-1]['status'], 'skipped')
        result.fields['total_deposits'] = result.fields['tenant_balance_due']
        result.fields['total_credits'] = result.fields['tenant_balance_due']
        self.assertEqual(statement_arithmetic(result)[-1]['status'], 'skipped')

    def test_hash_tampering_and_invalid_citation_are_flagged(self):
        result = self.document([('2025-01-01','10.00','10.00','charge')])
        result.entries[0].amount.observations[0].citations[0].bbox = (430,100,700,108)
        self.source.write_bytes(b'changed source version')
        checks = validate_document(result)['checks'][:2]
        self.assertEqual([item['status'] for item in checks], ['failed','failed'])

    def test_repeated_amounts_in_distinct_source_rows_are_not_exact_duplicates(self):
        result = self.document([('2025-01-01','500.00','1000.00','payment'),
                                ('2025-01-01','500.00','500.00','payment')])
        validation = validate_document(result)
        self.assertFalse(any(issue['code'] == 'duplicate_source_region' for issue in validation['issues']))
        self.assertEqual(len(result.entries), 2)
        result.entries.append(result.entries[0].model_copy(deep=True))
        validation = validate_document(result)
        self.assertTrue(any(issue['code'] == 'duplicate_source_region' for issue in validation['issues']))

    def test_exact_file_copies_retain_source_occurrences(self):
        first = self.document(document_id='first')
        second = self.document(document_id='second')
        report = validate_report([first,second])
        self.assertEqual(report['duplicate_documents'][0]['documents'], ['first','second'])
        self.assertEqual(first.document.document_id,'first')
        self.assertEqual(second.document.document_id,'second')

    def test_value_in_wrong_row_or_column_is_not_a_source_match(self):
        result = self.document([('2025-01-01','50.00','50.00','charge')])
        annotation = dict(id='reviewed-row',type='entry',sha256=self.sha,page=1,row_bbox=[30,90,600,110],
                          amount_bbox=[475,90,535,110],values={'amount':'50.00','posting_date':'2025-01-01'},
                          reviewer_kind='agent_visual_review')
        checks = annotation_checks([result],[annotation])
        self.assertEqual(checks[0]['status'],'failed')
        self.assertIn('amount_column',checks[0]['problems'])
        annotation['row_bbox'] = [30,150,600,170]
        self.assertEqual(annotation_checks([result],[annotation])[0]['status'],'failed')

    def test_ocr_checks_never_claim_reliable_table_extraction(self):
        result = self.document()
        result.document.pages[0].method = 'ocr'
        result.document.pages[0].state = 'partial'
        report = validate_report([result])
        self.assertEqual(report['reliable_ocr_table_extraction'],'not_established')
        self.assertFalse(report['independent_human_validation'])
        self.assertTrue(any(issue['code']=='ocr_table_unverified' for issue in report['documents'][0]['issues']))

    def test_source_annotations_cannot_pass_after_source_version_changes(self):
        result = self.document(fields={'balance':'100.00'})
        annotation = dict(id='source-balance',type='field',sha256=self.sha,page=1,field='balance',value='100.00',
                          bbox=[0,0,100,50],reviewer_kind='agent_visual_review')
        self.assertEqual(annotation_checks([result],[annotation])[0]['status'],'passed')
        self.source.write_bytes(b'another version')
        self.assertEqual(annotation_checks([result],[annotation])[0]['status'],'failed')


if __name__ == '__main__':
    unittest.main()

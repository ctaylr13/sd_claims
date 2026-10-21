import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.extraction.models import DocumentInput
from backend.extraction.program_verification import read_program_document, verify_program_proposals
from test_program_cells import source as waiver


def proposal(key='program_monthly_fee',value='50.00',quote='$50 per month'):
    return dict(key=key,value=value,quote=quote,event_date=None,service_period=None,bbox=[400,600,500,700])


def reading():
    return {'sha256':'a'*64,'holds':{},'pages':{1:{'readable':True,'size':[612,792],'records':[
        {'key':'program_monthly_fee','value':'50.00','context':'X YES $50.00 per month',
         'citation':{'document_id':'test','sha256':'a'*64,'page':1,'bbox':[100,48,125,58],
                     'quote':'$50.00','method':'native_text'}}]}}}


class ProgramVerificationTests(unittest.TestCase):
    def test_source_box_replaces_untrusted_model_box(self):
        result=verify_program_proposals([proposal()],reading(),1)[0]
        self.assertEqual(result['status'],'source_supported')
        self.assertEqual(result['anchor']['bbox'],[100,48,125,58])
        self.assertFalse(result['prediction_eligible'])
        self.assertFalse(result['training_eligible'])

    def test_wrong_values_signs_quotes_context_and_protected_assertions_hold(self):
        cases=[proposal(value='51.00'),proposal(value='-50.00'),proposal(value=50),
               proposal(quote='$50 invented'),proposal(quote=''),
               dict(proposal(),event_date='2024-01-01'),dict(proposal(),service_period='2024-01'),
               proposal('insurer_enrollment','yes','X YES'),proposal('waived_deposit','yes','YES')]
        for item in cases:
            self.assertEqual(verify_program_proposals([item],reading(),1)[0]['status'],'needs_review')

    def test_missing_cell_and_unchecked_alternative_are_never_proposal_evidence(self):
        for item in [proposal('waived_deposit','3450.00','$3,450'),proposal(value='35.00')]:
            result=verify_program_proposals([item],reading(),1)[0]
            self.assertEqual(result['reason'],'value_or_quote_not_supported')
            self.assertIsNone(result['anchor'])

    def test_changed_hash_page_method_and_bounds_hold(self):
        for changes in ({'sha256':'b'*64},{'page':2},{'method':'ocr'},
                        {'bbox':[0,0,700,800]},{'bbox':[float('nan'),0,20,20]}):
            source=reading()
            source['pages'][1]['records'][0]['citation'].update(changes)
            self.assertEqual(verify_program_proposals([proposal()],source,1)[0]['status'],'needs_review')

    def test_conflict_unreadable_ambiguity_and_duplicates_hold(self):
        source=reading();source['holds']['program_monthly_fee']='conflicting_document_values'
        self.assertEqual(verify_program_proposals([proposal()],source,1)[0]['reason'],'conflicting_document_values')
        source=reading();source['pages'][1]['readable']=False
        self.assertEqual(verify_program_proposals([proposal()],source,1)[0]['status'],'needs_review')
        source=reading();source['pages'][1]['records']*=2
        self.assertEqual(verify_program_proposals([proposal()],source,1)[0]['reason'],'ambiguous_source_cells')
        result=verify_program_proposals([proposal(),copy.deepcopy(proposal())],reading(),1)
        self.assertEqual(result[1]['reason'],'duplicate_proposal')

    def capture(self,pages,force=None):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.pdf';path.write_bytes(b'captured-source')
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            source=DocumentInput(path=str(path),document_id='waiver',kind='lease',force_ocr_pages=force or [])
            pdf=SimpleNamespace(pages=pages)
            with patch('backend.extraction.program_verification.pdfplumber.open') as opened, \
                 patch('backend.extraction.program_verification.native_words',return_value=[]), \
                 patch('backend.extraction.program_verification.fallback_reason',side_effect=lambda p,w:p.unread):
                opened.return_value.__enter__.return_value=pdf
                result=read_program_document(source,sha)
                with self.assertRaises(ValueError):
                    read_program_document(source,'b'*64)
            return result

    def page(self,fee='$50.00 per month',summary='',unread=False):
        page=waiver(fee=fee)
        page.bbox=(0,0,612,792);page.width=612;page.height=792
        page.extract_text=lambda:summary;page.unread=unread
        return page

    def test_complete_capture_binds_native_characters_to_hash_and_page(self):
        result=self.capture([self.page()])
        self.assertEqual(result['holds'],{})
        records=result['pages'][1]['records']
        self.assertEqual([r['value'] for r in records],['yes','50.00','2850.00'])
        self.assertTrue(all(r['citation']['sha256']==result['sha256'] for r in records))

    def test_other_page_summary_and_other_form_values_force_review(self):
        for pages in ([self.page(),self.page(summary='Zero Deposit Program premium $7.00')],
                      [self.page(),self.page(fee='$45.00 per month')]):
            result=self.capture(pages)
            self.assertEqual(result['holds']['program_monthly_fee'],'conflicting_document_values')
            self.assertNotIn('program_election',result['holds'])

    def test_unread_page_and_forced_ocr_block_program_acceptance(self):
        for pages,forced in [([self.page(),self.page(unread=True)],[]),([self.page()],[1])]:
            result=self.capture(pages,forced)
            self.assertEqual(result['holds']['program_election'],'incomplete_document_context')
            self.assertTrue(all(r['status']=='needs_review' for r in verify_program_proposals([proposal()],result,1)))


if __name__=='__main__':
    unittest.main()

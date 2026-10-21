import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.extraction.models import Document, DocumentInput, Page, Word
from backend.extraction.program_review import review_program_images


class ProgramReviewTests(unittest.TestCase):
    def test_ocr_evidence_is_cited_but_never_accepted_as_a_fact(self):
        for words, errors in (([Word(text='$58.00',bbox=(10,20,50,30),confidence=99)],[]),
                              ([],['ocr_failed:Timeout'])):
            with self.subTest(errors=errors), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'source.pdf';source.write_bytes(b'source-bytes')
                sha=hashlib.sha256(source.read_bytes()).hexdigest()
                request=DocumentInput(path=str(source),document_id='form',kind='lease')
                readings={'pages':{1:{'review_reason':'outlined_context_requires_visual_review'}}}
                document=Document(document_id='form',kind='lease',sha256=sha,original_path=str(source),
                    pages=[Page(number=1,width=612,height=792,method='ocr',state='partial',words=words,errors=errors)])
                with patch('backend.extraction.program_review.read_program_document',return_value=readings), \
                     patch('backend.extraction.program_review.extract_pdf',return_value=document) as extract:
                    result=review_program_images(request,sha,root/'review')
                self.assertEqual(extract.call_args.args[0].force_ocr_pages,[1])
                self.assertEqual(result['accepted_facts'],[])
                self.assertFalse(result['prediction_eligible'])
                self.assertFalse(result['training_eligible'])
                self.assertEqual(result['review_pages'][0]['status'],'needs_review')
                cite=result['review_pages'][0]['citation']
                if words:
                    self.assertEqual((cite['method'],cite['sha256'],cite['page']),('ocr',sha,1))
                else:
                    self.assertIsNone(cite)
                    self.assertEqual(result['review_pages'][0]['errors'],errors)

    def test_changed_source_is_rejected_before_creating_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.pdf';source.write_bytes(b'changed')
            request=DocumentInput(path=str(source),document_id='form',kind='lease')
            with patch('backend.extraction.program_review.read_program_document',return_value={'pages':{}}), \
                 self.assertRaisesRegex(ValueError,'source changed'):
                review_program_images(request,'a'*64,root/'review')
            self.assertFalse((root/'review').exists())

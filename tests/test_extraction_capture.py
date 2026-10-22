import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from backend.extraction import extract_claim
from backend.extraction.capture import CHUNK_BYTES, ExtractionLimitExceeded, capture_pdf
from backend.extraction.models import DocumentInput, Manifest
from backend.extraction.pdf import extract_pdf


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source.pdf'
        self.source.write_bytes(b'captured source')
        self.manifest = Manifest(claim_id='claim', documents=[
            DocumentInput(path=str(self.source), document_id='first', kind='lease')])

    def test_capture_reads_in_bounded_chunks_and_hashes_exact_bytes(self):
        content = b'x' * (CHUNK_BYTES * 2 + 13)
        stream = io.BytesIO(content)
        source = Mock()
        source.open.return_value = stream
        destination = self.root / 'captured.pdf'
        with patch.object(stream, 'read', wraps=stream.read) as read:
            sha256, size = capture_pdf(source, destination)
        self.assertTrue(all(0 < call.args[0] <= CHUNK_BYTES for call in read.call_args_list))
        self.assertGreater(read.call_count, 2)
        self.assertEqual((sha256, size), (hashlib.sha256(content).hexdigest(), len(content)))
        self.assertEqual(destination.read_bytes(), content)

    def test_file_limit_accepts_boundary_and_rejects_one_byte_more(self):
        with patch('backend.extraction.capture.MAX_FILE_BYTES', 8):
            self.source.write_bytes(b'x' * 8)
            self.assertEqual(capture_pdf(self.source, self.root / 'allowed.pdf')[1], 8)
            self.source.write_bytes(b'x' * 9)
            with self.assertRaisesRegex(ExtractionLimitExceeded, 'Each PDF'):
                capture_pdf(self.source, self.root / 'oversize.pdf')

    def test_capture_counts_bytes_if_source_grows_while_reading(self):
        incoming = io.BytesIO(b'1234')
        source = Mock()
        source.open.return_value = incoming
        read = incoming.read

        def grow(size):
            chunk = read(size)
            if incoming.tell() == 4:
                incoming.write(b'56789')
                incoming.seek(4)
            return chunk

        with patch('backend.extraction.capture.MAX_FILE_BYTES', 8), patch.object(incoming, 'read', side_effect=grow):
            with self.assertRaises(ExtractionLimitExceeded):
                capture_pdf(source, self.root / 'growing.pdf')

    def test_document_count_is_validated_before_capture(self):
        docs = [DocumentInput(path=str(self.source), document_id=str(index), kind='lease') for index in range(21)]
        self.assertEqual(len(Manifest(claim_id='claim', documents=docs[:20]).documents), 20)
        with self.assertRaises(ValidationError):
            Manifest(claim_id='claim', documents=docs)
        self.manifest.documents = docs
        with patch('backend.extraction.capture_pdf') as capture:
            with self.assertRaises(ExtractionLimitExceeded):
                extract_claim(self.manifest, self.root / 'store')
        capture.assert_not_called()

    def test_total_limit_rejects_before_parsing_and_cleans_captures(self):
        self.manifest.documents.append(DocumentInput(path=str(self.source), document_id='second', kind='lease'))
        captures = []

        def capture(source, destination, **kwargs):
            captures.append(destination)
            return capture_pdf(source, destination, **kwargs)

        with patch('backend.extraction.MAX_TOTAL_BYTES', 20), \
                patch('backend.extraction.capture_pdf', side_effect=capture), \
                patch('backend.extraction.extract_pdf') as parse:
            with self.assertRaisesRegex(ExtractionLimitExceeded, 'Combined PDFs'):
                extract_claim(self.manifest, self.root / 'store')
        parse.assert_not_called()
        self.assertEqual(len(captures), 2)
        self.assertTrue(all(not path.parent.exists() for path in captures))
        self.assertFalse((self.root / 'store').exists())

    def test_total_limit_accepts_exact_boundary(self):
        self.manifest.documents.append(DocumentInput(path=str(self.source), document_id='second', kind='lease'))
        with patch('backend.extraction.MAX_TOTAL_BYTES', self.source.stat().st_size * 2):
            result = extract_claim(self.manifest, self.root / 'store')
        self.assertEqual(len(json.loads(result.read_text())['documents']), 2)

    def test_file_limit_rejects_claim_before_parsing(self):
        with patch('backend.extraction.capture.MAX_FILE_BYTES', 8), \
                patch('backend.extraction.extract_pdf') as parse:
            with self.assertRaisesRegex(ExtractionLimitExceeded, 'Each PDF'):
                extract_claim(self.manifest, self.root / 'store')
        parse.assert_not_called()

    def test_direct_pdf_extraction_opens_retained_file(self):
        import pdfplumber

        with patch('backend.extraction.pdf.pdfplumber.open', wraps=pdfplumber.open) as open_pdf:
            document = extract_pdf(self.manifest.documents[0], self.root / 'direct')
        open_pdf.assert_called_once_with(self.root / 'direct' / 'original.pdf')
        self.assertEqual(Path(document.original_path).read_bytes(), self.source.read_bytes())

    def test_direct_pdf_extraction_enforces_file_limit_for_paths_and_bytes(self):
        with patch('backend.extraction.capture.MAX_FILE_BYTES', 8):
            with self.assertRaises(ExtractionLimitExceeded):
                extract_pdf(self.manifest.documents[0], self.root / 'direct-path')
        with patch('backend.extraction.pdf.MAX_FILE_BYTES', 8):
            with self.assertRaises(ExtractionLimitExceeded):
                extract_pdf(self.manifest.documents[0], self.root / 'direct-bytes', content=b'x' * 9)

    def test_captures_are_paths_processed_in_order_and_removed_on_cache_hit(self):
        self.manifest.documents.append(DocumentInput(path=str(self.source), document_id='second', kind='lease'))
        captures = []
        processed = []

        def capture(source, destination, **kwargs):
            captures.append(destination)
            return capture_pdf(source, destination, **kwargs)

        def parse(item, directory, *, captured_path, tools):
            self.assertTrue(all(path.is_file() for path in captures))
            self.assertIsInstance(captured_path, Path)
            processed.append(item.document_id)
            return extract_pdf(item, directory, captured_path=captured_path, tools=tools)

        with patch('backend.extraction.capture_pdf', side_effect=capture), \
                patch('backend.extraction.extract_pdf', side_effect=parse):
            result = extract_claim(self.manifest, self.root / 'store')
            self.assertTrue(all(not path.parent.exists() for path in captures))
            self.assertEqual(extract_claim(self.manifest, self.root / 'store'), result)
        self.assertEqual(processed, ['first', 'second'])
        self.assertEqual(len(captures), 4)
        self.assertTrue(all(not path.parent.exists() for path in captures))
        report = json.loads(result.read_text())
        self.assertTrue(all(Path(doc['document']['original_path']).exists() for doc in report['documents']))

    def test_captures_are_removed_on_processing_failure(self):
        captures = []

        def fail(manifest, directory, sources, tools):
            captures.extend(sources)
            self.assertTrue(all(path.is_file() for path in sources))
            raise RuntimeError('processing failed')

        with patch('backend.extraction._extract_claim', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'processing failed'):
                extract_claim(self.manifest, self.root / 'store')
        self.assertTrue(captures)
        self.assertTrue(all(not path.parent.exists() for path in captures))

    def test_missing_source_reports_are_not_reused(self):
        self.source.unlink()
        first = extract_claim(self.manifest, self.root / 'store')
        second = extract_claim(self.manifest, self.root / 'store')
        self.assertNotEqual(first, second)
        document = json.loads(first.read_text())['documents'][0]['document']
        self.assertIsNone(document['sha256'])
        self.assertIsNone(document['original_path'])
        self.assertEqual(document['errors'], ['source_read_failed:OSError'])


if __name__ == '__main__':
    unittest.main()

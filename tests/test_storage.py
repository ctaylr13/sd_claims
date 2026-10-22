import io
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

from reportlab.pdfgen.canvas import Canvas

from backend.extraction import extract_claim
from backend.extraction.models import DocumentInput, Manifest
from backend.storage import JobInProgress, LocalStore, RetryRequired, _file_digest, canonical_bytes, digest


class FileDigestTests(unittest.TestCase):
    def test_hashes_exact_bytes_with_bounded_reads(self):
        chunk_size = 1024 * 1024
        for size in (0, 13, chunk_size, 2 * chunk_size + 13):
            with self.subTest(size=size):
                content = (bytes(range(256)) * (size // 256 + 1))[:size]
                stream = io.BytesIO(content)
                path = Mock()
                path.open.return_value = stream
                with patch.object(stream, 'read', wraps=stream.read) as read:
                    self.assertEqual(_file_digest(path), digest(content))
                path.open.assert_called_once_with('rb')
                self.assertTrue(all(0 < call.args[0] <= chunk_size for call in read.call_args_list))
                self.assertGreaterEqual(read.call_count, size // chunk_size + 1)
                self.assertTrue(stream.closed)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'private'
        self.store = LocalStore(self.root)
        self.request = {'content': 'abc', 'parser_version': '1', 'config_version': '1', 'schema_version': '1',
                        'source': {'id': 'source-1', 'version': 'revision-1'}}
        self.calls = 0

    def produce(self, directory):
        self.calls += 1
        (directory / 'original.pdf').write_bytes(b'original')
        result = directory / 'report.json'
        result.write_text('{"complete": true}')
        return result

    def test_duplicate_requests_reuse_verified_results_across_store_instances(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Unbounded artifact read')):
            result = self.store.run(self.request, self.produce)
            second = LocalStore(self.root).run(dict(reversed(list(self.request.items()))), self.produce)
        self.assertEqual(result, second)
        self.assertEqual(self.calls, 1)
        row = self.store.status(digest(canonical_bytes(self.request)))
        self.assertEqual(row['state'], 'succeeded')
        self.assertEqual(json.loads(row['request_json'])['source'], self.request['source'])
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        for path in (result, result.parent / 'original.pdf', self.store.database):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_content_parser_config_and_source_versions_invalidate(self):
        paths = {self.store.run(self.request, self.produce)}
        for field in ('content', 'parser_version', 'config_version', 'schema_version', 'source'):
            changed = {**self.request, field: 'changed'}
            paths.add(self.store.run(changed, self.produce))
        self.assertEqual(len(paths), 6)
        self.assertEqual(self.calls, 6)

    def test_missing_or_modified_artifacts_require_retry(self):
        for target in ('report.json', 'original.pdf'):
            for damage in ('delete', 'truncate'):
                with self.subTest(target=target, damage=damage):
                    request = {**self.request, 'case': [target, damage]}
                    result = self.store.run(request, self.produce)
                    artifact = result.parent / target
                    if damage == 'delete':
                        artifact.unlink()
                    else:
                        artifact.write_bytes(b'')
                    with self.assertRaises(RetryRequired):
                        self.store.run(request, self.produce)
                    row = self.store.status(digest(canonical_bytes(request)))
                    self.assertEqual(row['state'], 'failed')
                    recovered = self.store.run(request, self.produce, retry=True)
                    self.assertNotEqual(result, recovered)
                    self.assertTrue(recovered.exists())

    def test_failure_does_not_publish_partial_result(self):
        def fail(directory):
            (directory / 'report.json').write_text('{')
            raise RuntimeError('private error details')

        with self.assertRaises(RuntimeError):
            self.store.run(self.request, fail)
        row = self.store.status(digest(canonical_bytes(self.request)))
        self.assertEqual(row['state'], 'failed')
        self.assertIsNone(row['result_path'])
        self.assertIsNone(row['inventory_json'])
        self.assertEqual(row['error'], 'RuntimeError')
        with self.assertRaises(RetryRequired):
            self.store.run(self.request, self.produce)
        self.assertTrue(self.store.run(self.request, self.produce, retry=True).exists())

    def test_running_duplicate_and_retry_cannot_take_over_active_job(self):
        started, finish = Event(), Event()

        def slow(directory):
            started.set()
            if not finish.wait(5):
                raise TimeoutError()
            return self.produce(directory)

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.store.run, self.request, slow)
            try:
                self.assertTrue(started.wait(5))
                row = self.store.status(digest(canonical_bytes(self.request)))
                self.assertEqual(row['state'], 'running')
                self.assertIsNone(row['result_path'])
                self.assertIsNone(row['inventory_json'])
                for retry in (False, True):
                    with self.assertRaises(JobInProgress):
                        LocalStore(self.root).run(self.request, self.produce, retry=retry)
            finally:
                finish.set()
            result = pending.result(timeout=5)
        self.assertEqual(self.store.run(self.request, self.produce), result)
        self.assertEqual(self.calls, 1)

    def test_process_duplicate_cannot_take_over_active_job(self):
        code = """import sys
from pathlib import Path
from backend.storage import LocalStore

def wait(directory):
    print('running', flush=True)
    sys.stdin.readline()
    result = directory / 'report.json'
    result.write_text('{}')
    return result

LocalStore(Path(sys.argv[1])).run({'active': True}, wait)
"""
        with subprocess.Popen([sys.executable, '-c', code, str(self.root)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(), 'running')
                with self.assertRaises(JobInProgress):
                    self.store.run({'active': True}, self.produce, retry=True)
            finally:
                process.communicate('finish\n', timeout=5)
        self.assertEqual(process.returncode, 0)
        result = self.store.run({'active': True}, self.produce)
        self.assertEqual(result.read_text(), '{}')
        self.assertEqual(self.calls, 0)

    def test_process_exit_leaves_running_job_for_explicit_retry(self):
        code = '''import os, sys
from pathlib import Path
from backend.storage import LocalStore

def stop(directory):
    (directory / 'report.json').write_text('{')
    os._exit(23)

LocalStore(Path(sys.argv[1])).run({'interrupted': True}, stop)
'''
        process = subprocess.run([sys.executable, '-c', code, str(self.root)], check=False)
        self.assertEqual(process.returncode, 23)
        request = {'interrupted': True}
        row = self.store.status(digest(canonical_bytes(request)))
        self.assertEqual(row['state'], 'running')
        self.assertIsNone(row['result_path'])
        with self.assertRaises(RetryRequired):
            self.store.run(request, self.produce)
        self.assertTrue(self.store.run(request, self.produce, retry=True).exists())

    def test_missing_result_is_not_published(self):
        with self.assertRaises(ValueError):
            self.store.run(self.request, lambda directory: directory / 'missing.json')
        self.assertEqual(self.store.status(digest(canonical_bytes(self.request)))['state'], 'failed')

    def test_publication_failure_leaves_no_reusable_result(self):
        with patch('backend.storage._sync_directory', side_effect=OSError('sync failed')):
            with self.assertRaises(OSError):
                self.store.run(self.request, self.produce)
        row = self.store.status(digest(canonical_bytes(self.request)))
        self.assertEqual(row['state'], 'failed')
        self.assertIsNone(row['result_path'])


class ExtractionStorageTests(unittest.TestCase):
    def test_extraction_uses_content_and_manifest_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.pdf'
            canvas = Canvas(str(source))
            canvas.drawString(30, 750, 'Amount Due $123.45 for cleaning and repairs')
            canvas.save()
            manifest = Manifest(claim_id='claim-1', documents=[DocumentInput(
                path=str(source), document_id='doc-1', kind='move_out_statement',
                source={'file_id': 'drive-1', 'version': '1'})])
            output = root / 'store'
            original = extract_claim(manifest, output)
            self.assertEqual(extract_claim(manifest, output), original)
            with patch('backend.extraction.PARSER_VERSION', 'new-parser'):
                self.assertNotEqual(extract_claim(manifest, output), original)
            with patch('backend.extraction.CONFIG_VERSION', 'new-config'):
                self.assertNotEqual(extract_claim(manifest, output), original)
            with patch('backend.extraction.extraction_tools', return_value={'pdfplumber': 'changed'}):
                self.assertNotEqual(extract_claim(manifest, output), original)
            manifest.documents[0].source['version'] = '2'
            self.assertNotEqual(extract_claim(manifest, output), original)
            manifest.documents[0].source['version'] = '1'
            manifest.effective_on = '2026-01-01'
            self.assertNotEqual(extract_claim(manifest, output), original)
            manifest.effective_on = None
            source.write_bytes(source.read_bytes() + b'\n')
            changed = extract_claim(manifest, output)
            self.assertNotEqual(changed, original)
            report = json.loads(changed.read_text())
            doc = report['documents'][0]['document']
            self.assertEqual(doc['sha256'], digest(source.read_bytes()))
            self.assertEqual(doc['source'], manifest.documents[0].source)

    def test_extraction_parses_the_bytes_used_in_the_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.pdf'
            source.write_bytes(b'captured bytes')
            manifest = Manifest(claim_id='claim', documents=[DocumentInput(
                path=str(source), document_id='doc', kind='lease')])
            from backend.extraction.pdf import extract_pdf

            def replace_source(item, directory, *, captured_path, tools):
                source.write_bytes(b'new bytes')
                return extract_pdf(item, directory, captured_path=captured_path, tools=tools)

            with patch('backend.extraction.extract_pdf', side_effect=replace_source):
                result = extract_claim(manifest, root / 'store')
            document = json.loads(result.read_text())['documents'][0]['document']
            self.assertEqual(document['sha256'], digest(b'captured bytes'))
            self.assertEqual(Path(document['original_path']).read_bytes(), b'captured bytes')

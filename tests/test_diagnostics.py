import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.batches import BatchRequest, create_batch, process_batch
from backend.diagnostics import failure_context, log_exception
from backend.main import app
from backend.storage import LocalStore


class DiagnosticsTests(unittest.TestCase):
    def test_context_is_inherited_overridden_and_reset_after_failure(self):
        error = RuntimeError('server-only detail')
        with self.assertLogs('backend.diagnostics', level='ERROR') as logs:
            try:
                with failure_context(batch_id='batch', claim_id='outer'):
                    with failure_context(claim_id='inner'):
                        try:
                            raise error
                        except RuntimeError:
                            log_exception('parse_document', document_id='ledger')
                            raise
            except RuntimeError:
                log_exception('after_context')
        nested, after = logs.records
        self.assertEqual((nested.batch_id, nested.claim_id, nested.document_id), ('batch', 'inner', 'ledger'))
        self.assertIs(nested.exc_info[1], error)
        self.assertIsNotNone(nested.exc_info[2])
        self.assertIn('Traceback', logs.output[0])
        self.assertIn('batch', logs.output[0])
        self.assertEqual((after.batch_id, after.claim_id), (None, None))

    def test_batch_failure_logs_identifiers_and_continues_without_context_leaks(self):
        request = BatchRequest.model_validate({'claims': [
            {'claim_id': name, 'documents': [{'document_id': 'ledger', 'kind': 'ledger', 'path': '/unused.pdf'}]} for name in ('first', 'last')]})

        def extract(manifest, root, retry):
            try:
                raise RuntimeError('private-source-path')
            except RuntimeError:
                log_exception('nested_failure')
                raise

        with tempfile.TemporaryDirectory() as temporary:
            store = LocalStore(Path(temporary))
            batch_id = create_batch(store, request)['batch_id']
            with self.assertLogs('backend.diagnostics', level='ERROR') as logs:
                progress = process_batch(batch_id, request, store, extract)
                try:
                    raise RuntimeError('outside batch')
                except RuntimeError:
                    log_exception('outside_batch')
        self.assertEqual((progress['completed'], progress['failed']), (2, 2))
        for record, claim_id in zip(logs.records[:4], ('first', 'first', 'last', 'last')):
            self.assertEqual((record.batch_id, record.claim_id), (batch_id, claim_id))
            self.assertIsNotNone(record.exc_info[2])
        self.assertEqual((logs.records[-1].batch_id, logs.records[-1].claim_id), (None, None))
        self.assertNotIn('private-source-path', str(progress))

    def test_unhandled_request_failure_logs_route_identifiers(self):
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch('backend.main.require_result', side_effect=RuntimeError('server-only detail')):
                with self.assertLogs('backend.diagnostics', level='ERROR') as logs:
                    response = client.get('/api/batches/batch-123/claims/2/result')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('server-only detail', response.text)
        record, = logs.records
        self.assertEqual((record.operation, record.batch_id, record.claim_index),
                         ('http_request', 'batch-123', '2'))
        self.assertIsNotNone(record.exc_info[2])

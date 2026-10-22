import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from backend.batches import BatchRequest, create_batch, process_batch, run_batch
from backend.extraction import extract_claim
from backend.main import app, get_store
from backend.storage import LocalStore


class BatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root / 'source.pdf'
        canvas = Canvas(str(source))
        canvas.drawString(30, 750, 'Amount Due $123.45 for cleaning and repairs')
        canvas.save()
        self.body = {'claims': [
            {'claim_id': name, 'documents': [{'document_id': 'statement', 'kind': 'move_out_statement',
                                             'path': str(source)}]} for name in ('first', 'broken', 'last')]}
        self.request = BatchRequest.model_validate(self.body)
        self.store = LocalStore(self.root / 'store')
        app.dependency_overrides[get_store] = lambda: self.store
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def extract(self, manifest, output_root, retry):
        if manifest.claim_id == 'broken':
            raise RuntimeError('private-source-path')
        return extract_claim(manifest, Path(output_root), retry=retry)

    def test_failure_between_successes_and_verified_api_results(self):
        def execute(batch_id, request, root):
            process_batch(batch_id, request, root, self.extract)

        with patch('backend.main.run_batch', side_effect=execute):
            response = self.client.post('/api/batches', json=self.body)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['state'], 'queued')
        batch_id = response.json()['batch_id']
        progress = self.client.get(f'/api/batches/{batch_id}').json()
        self.assertEqual((progress['state'], progress['completed'], progress['succeeded'], progress['failed']),
                         ('completed', 3, 2, 1))
        self.assertEqual([r['state'] for r in progress['results']], ['succeeded', 'failed', 'succeeded'])
        self.assertNotIn('private-source-path', json.dumps(progress))
        result_url = f'/api/batches/{batch_id}/claims/0/result'
        report = self.client.get(result_url)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()['claim_id'], 'first')
        self.assertTrue(report.json()['review_required'])
        self.assertEqual(self.client.get(f'/api/batches/{batch_id}/claims/1/result').status_code, 409)
        self.assertEqual(self.client.get(f'/api/batches/{batch_id}/claims/-1/result').status_code, 404)
        path = self.store.result_for_attempt(progress['results'][0]['attempt'])
        path.write_text('{}')
        self.assertEqual(self.client.get(result_url).status_code, 409)
        self.assertEqual(LocalStore(self.store.root).batch(batch_id), progress)

    def test_progress_visible_while_claim_is_running(self):
        progress = create_batch(self.store, self.request)
        started, finish = Event(), Event()

        def slow(manifest, root, retry):
            if manifest.claim_id == 'first':
                started.set()
                if not finish.wait(10):
                    raise TimeoutError()
            return self.extract(manifest, root, retry)

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(process_batch, progress['batch_id'], self.request, self.store.root, slow)
            try:
                self.assertTrue(started.wait(10))
                current = self.client.get(f"/api/batches/{progress['batch_id']}").json()
                self.assertEqual(current['state'], 'running')
                self.assertEqual(current['completed'], 0)
                self.assertEqual([r['state'] for r in current['results']], ['running', 'queued', 'queued'])
            finally:
                finish.set()
            self.assertEqual(pending.result(timeout=10)['completed'], 3)

    def test_repeated_batches_reuse_extraction_attempts(self):
        attempts = []
        for _ in range(2):
            progress = create_batch(self.store, self.request)
            result = process_batch(progress['batch_id'], self.request, self.store.root, self.extract)
            attempts.append(result['results'][0]['attempt'])
        self.assertEqual(attempts[0], attempts[1])

    def test_failed_extraction_requires_explicit_batch_retry(self):
        from backend.extraction import _extract_claim

        def fail_one(manifest, *args):
            if manifest.claim_id == 'broken':
                raise RuntimeError('private-source-path')
            return _extract_claim(manifest, *args)

        def extract(manifest, root, retry):
            return extract_claim(manifest, Path(root), retry=retry)

        progress = create_batch(self.store, self.request)
        with patch('backend.extraction._extract_claim', side_effect=fail_one):
            first = process_batch(progress['batch_id'], self.request, self.store.root, extract)
        self.assertEqual(first['results'][1]['error'], 'extraction_failed')
        progress = create_batch(self.store, self.request)
        second = process_batch(progress['batch_id'], self.request, self.store.root, extract)
        self.assertEqual(second['results'][1]['error'], 'retry_required')
        self.request.retry = True
        progress = create_batch(self.store, self.request)
        final = process_batch(progress['batch_id'], self.request, self.store.root, extract)
        self.assertEqual(final['succeeded'], 3)
        self.assertEqual(final['results'][0]['attempt'], first['results'][0]['attempt'])

    def test_bad_batches_and_unknown_batch(self):
        for body in ({'claims': []}, {'claims': self.body['claims'] * 8},
                     {'claims': [self.body['claims'][0]] * 2}, {'claims': [{}]},
                     {**self.body, 'unexpected': True}):
            self.assertEqual(self.client.post('/api/batches', json=body).status_code, 422)
        self.assertEqual(self.client.get('/api/batches/missing').status_code, 404)

    def test_document_count_limit_rejects_batch_before_execution(self):
        claim = self.body['claims'][0]
        documents = [{**claim['documents'][0], 'document_id': str(index)} for index in range(21)]
        with patch('backend.main.run_batch') as run:
            response = self.client.post('/api/batches', json={'claims': [{**claim, 'documents': documents}]})
        self.assertEqual(response.status_code, 422)
        run.assert_not_called()

    def test_flow_failure_is_persisted_and_sanitized(self):
        progress = create_batch(self.store, self.request)
        with patch('backend.flows.claim_batch_flow', side_effect=RuntimeError('private source')):
            run_batch(progress['batch_id'], self.request, self.store.root)
        saved = self.store.batch(progress['batch_id'])
        self.assertEqual(saved['state'], 'failed')
        self.assertEqual(saved['error'], 'batch_execution_failed')
        self.assertNotIn('private source', json.dumps(saved))

    def test_real_prefect_flow_isolates_failure_and_reuses_results(self):
        from prefect.testing.utilities import prefect_test_harness

        self.enterContext(prefect_test_harness())

        def extract(manifest, root, *, retry=False):
            if manifest.claim_id == 'broken':
                raise RuntimeError('private-source-path')
            return extract_claim(manifest, root, retry=retry)

        with patch('backend.flows.extract_claim', side_effect=extract):
            response = self.client.post('/api/batches', json=self.body)
        self.assertEqual(response.status_code, 202)
        saved = self.store.batch(response.json()['batch_id'])
        self.assertEqual(saved['state'], 'completed')
        self.assertEqual((saved['succeeded'], saved['failed'], saved['completed']), (2, 1, 3))
        self.assertEqual(saved['results'][1]['error'], 'extraction_failed')
        self.assertEqual([r['state'] for r in saved['results']], ['succeeded', 'failed', 'succeeded'])
        self.assertNotIn('private-source-path', json.dumps(saved))
        attempt = saved['results'][0]['attempt']
        response = self.client.post('/api/batches', json={'claims': [self.body['claims'][0]]})
        self.assertEqual(response.status_code, 202)
        reused = self.store.batch(response.json()['batch_id'])
        self.assertEqual(reused['state'], 'completed')
        self.assertEqual(reused['results'][0]['attempt'], attempt)

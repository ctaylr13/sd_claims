import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.batches import BatchRequest, create_batch, fail_unfinished_batch
from backend.main import app
from backend.storage import LocalStore


class StorageStartupTests(unittest.TestCase):
    def test_recovery_is_atomic_repeatable_and_preserves_terminal_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalStore(Path(temporary))
            request = BatchRequest.model_validate({'claims': [{
                'claim_id': 'claim', 'documents': [{
                    'document_id': 'ledger', 'kind': 'ledger', 'path': '/unused.pdf',
                }],
            }]})
            queued = create_batch(store, request)
            running = create_batch(store, request)
            running['state'] = 'running'
            store.save_batch(running)
            terminal = create_batch(store, request)
            fail_unfinished_batch(terminal, 'batch_execution_failed')
            store.save_batch(terminal)

            def fail_second(progress, error):
                if progress['batch_id'] == running['batch_id']:
                    raise RuntimeError('Recovery interrupted')
                fail_unfinished_batch(progress, error)

            with self.assertRaisesRegex(RuntimeError, 'Recovery interrupted'):
                store.recover_batches(fail_second)
            self.assertEqual(store.batch(queued['batch_id']), queued)
            self.assertEqual(store.batch(running['batch_id']), running)
            for _ in range(2):
                with patch('backend.main.LocalStore', return_value=store):
                    with TestClient(app):
                        pass
                for original in (queued, running):
                    recovered = store.batch(original['batch_id'])
                    self.assertEqual((recovered['state'], recovered['error'], recovered['completed']),
                                     ('failed', 'batch_interrupted', 1))
                self.assertEqual(store.batch(terminal['batch_id']), terminal)

    def test_startup_initializes_private_storage_and_requests_reuse_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('backend.main.REPO_ROOT', root), patch('backend.main.LocalStore', wraps=LocalStore) as factory:
                with TestClient(app) as client:
                    store = app.state.store
                    factory.assert_called_once_with(root / 'cache/extraction')
                    for directory in (store.root, store.root / 'artifacts', store.root / 'locks'):
                        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE(store.database.stat().st_mode), 0o600)
                    with store.connect() as connection:
                        tables = {row['name'] for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'")}
                    self.assertTrue({'jobs', 'batches'} <= tables)
                    with patch.object(Path, 'mkdir', side_effect=AssertionError('Request created directory')), \
                            patch.object(Path, 'chmod', side_effect=AssertionError('Request changed permissions')), \
                            patch('backend.main.run_batch'):
                        for _ in range(2):
                            response = client.post('/api/batches', json={'claims': [{
                                'claim_id': 'claim', 'documents': [{
                                    'document_id': 'ledger', 'kind': 'ledger', 'path': '/unused.pdf',
                                }],
                            }]})
                            self.assertEqual(response.status_code, 202)
                            batch_id = response.json()['batch_id']
                            self.assertEqual(client.get(f'/api/batches/{batch_id}').status_code, 200)
                    factory.assert_called_once()
                    self.assertIs(app.state.store, store)

    def test_storage_failure_prevents_application_startup(self):
        with patch('backend.main.LocalStore', side_effect=PermissionError('Storage unavailable')):
            with self.assertRaisesRegex(PermissionError, 'Storage unavailable'):
                with TestClient(app):
                    self.fail('Application started without storage')

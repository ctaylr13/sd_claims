import fcntl
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(content):
    return hashlib.sha256(content).hexdigest()


def _file_digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _verify_artifacts(directory, inventory, paths):
    for path in paths:
        name = str(path.relative_to(directory))
        if (name not in inventory or not path.resolve().is_relative_to(directory)
                or _file_digest(path) != inventory[name]):
            return False
    return True


class JobInProgress(RuntimeError):
    pass


class RetryRequired(RuntimeError):
    pass


class LocalStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        for name in ('artifacts', 'locks'):
            directory = self.root / name
            directory.mkdir(exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        self.database = self.root / 'jobs.sqlite3'
        descriptor = os.open(self.database, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.database.chmod(0o600)
        with self.connect() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS jobs (
                key TEXT PRIMARY KEY, request_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
                attempt TEXT NOT NULL, result_path TEXT, inventory_json TEXT, error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )''')
            connection.execute('''CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, progress_json TEXT NOT NULL
            )''')

    def save_batch(self, progress):
        with self.connect() as connection:
            connection.execute('''INSERT INTO batches (id, progress_json) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET progress_json = excluded.progress_json''',
                               (progress['batch_id'], canonical_bytes(progress).decode()))

    def batch(self, batch_id):
        with self.connect() as connection:
            row = connection.execute('SELECT progress_json FROM batches WHERE id = ?', (batch_id,)).fetchone()
        return json.loads(row['progress_json']) if row else None

    def result_for_attempt(self, attempt):
        row = self._attempt(attempt)
        return self._result(row) if row else None

    def artifact_for_attempt(self, attempt, select_artifacts):
        row = self._attempt(attempt)
        result = self._result(row, verify_all=False) if row else None
        if result is None:
            return None
        paths = tuple(dict.fromkeys(select_artifacts(result)))
        if not paths:
            return None
        try:
            inventory = json.loads(row['inventory_json'])
            directory = self.root / 'artifacts' / row['attempt']
            return paths[-1] if _verify_artifacts(directory, inventory, paths) else None
        except (OSError, TypeError, ValueError):
            return None

    def _attempt(self, attempt):
        with self.connect() as connection:
            return connection.execute("SELECT * FROM jobs WHERE attempt = ? AND state = 'succeeded'",
                                      (attempt,)).fetchone()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def status(self, key):
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM jobs WHERE key = ?', (key,)).fetchone()
        return dict(row) if row else None

    def _result(self, row, *, verify_all=True):
        try:
            directory = self.root / 'artifacts' / row['attempt']
            inventory = json.loads(row['inventory_json'])
            result = directory / row['result_path']
            if row['result_path'] not in inventory:
                return None
            paths = (directory / name for name in inventory) if verify_all else (result,)
            return result if _verify_artifacts(directory, inventory, paths) else None
        except (OSError, TypeError, ValueError):
            return None

    def run(self, request, produce, *, retry=False):
        payload = canonical_bytes(request).decode()
        key = digest(payload.encode())
        lock_path = self.root / 'locks' / key
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobInProgress(f'Job {key} is running') from None
            row = self.status(key)
            if row and row['state'] == 'succeeded':
                result = self._result(row)
                if result:
                    return result
                with self.connect() as connection:
                    connection.execute("UPDATE jobs SET state = 'failed', result_path = NULL, "
                                       'inventory_json = NULL, error = ?, '
                                       'updated_at = CURRENT_TIMESTAMP WHERE key = ?',
                                       ('Result artifacts are incomplete or modified', key))
            if row and not retry:
                raise RetryRequired(f'Job {key} needs an explicit retry')
            attempt = uuid.uuid4().hex
            directory = self.root / 'artifacts' / attempt
            directory.mkdir(mode=0o700)
            with self.connect() as connection:
                connection.execute('''INSERT INTO jobs (key, request_json, state, attempt)
                    VALUES (?, ?, 'running', ?) ON CONFLICT(key) DO UPDATE SET
                    state = 'running', attempt = excluded.attempt, result_path = NULL,
                    inventory_json = NULL, error = NULL, updated_at = CURRENT_TIMESTAMP''',
                                   (key, payload, attempt))
            try:
                result = Path(produce(directory)).resolve()
                result_name = str(result.relative_to(directory))
                inventory = {}
                for path in directory.rglob('*'):
                    if path.is_symlink():
                        raise ValueError('Artifact symlinks are not supported')
                    if path.is_file():
                        path.chmod(0o600)
                        with path.open('rb') as stream:
                            os.fsync(stream.fileno())
                        inventory[str(path.relative_to(directory))] = _file_digest(path)
                    elif path.is_dir():
                        path.chmod(0o700)
                if result_name not in inventory:
                    raise ValueError('Producer did not create its result')
                directories = sorted(filter(Path.is_dir, directory.rglob('*')),
                                     key=lambda path: len(path.parts), reverse=True)
                for path in [*directories, directory, directory.parent]:
                    _sync_directory(path)
                with self.connect() as connection:
                    connection.execute("UPDATE jobs SET state = 'succeeded', result_path = ?, "
                                       'inventory_json = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?',
                                       (result_name, canonical_bytes(inventory).decode(), key))
                return result
            except BaseException as error:
                with self.connect() as connection:
                    connection.execute("UPDATE jobs SET state = 'failed', error = ?, "
                                       'updated_at = CURRENT_TIMESTAMP WHERE key = ?',
                                       (type(error).__name__, key))
                raise


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

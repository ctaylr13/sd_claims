import uuid
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .diagnostics import failure_context, log_exception
from .extraction import extract_claim
from .extraction.models import Manifest
from .storage import JobInProgress, RetryRequired

execution_lock = Lock()


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    claims: list[Manifest] = Field(min_length=1, max_length=20)
    retry: bool = False

    @model_validator(mode='after')
    def unique_claims(self):
        ids = [claim.claim_id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError('claim_id must be unique within a batch')
        return self


class ClaimProgress(BaseModel):
    claim_id: str
    state: Literal['queued', 'running', 'succeeded', 'failed']
    attempt: str | None = None
    error: str | None = None


class BatchProgress(BaseModel):
    batch_id: str
    state: Literal['queued', 'running', 'completed', 'failed']
    total: int
    completed: int
    succeeded: int
    failed: int
    results: list[ClaimProgress]
    error: str | None = None


def create_batch(store, request):
    progress = BatchProgress(
        batch_id=uuid.uuid4().hex, state='queued', total=len(request.claims),
        completed=0, succeeded=0, failed=0,
        results=[ClaimProgress(claim_id=claim.claim_id, state='queued') for claim in request.claims],
    ).model_dump()
    store.save_batch(progress)
    return progress


def process_batch(batch_id, request, store, extract):
    progress = store.batch(batch_id)
    progress['state'] = 'running'
    store.save_batch(progress)
    for manifest, result in zip(request.claims, progress['results']):
        result['state'] = 'running'
        store.save_batch(progress)
        try:
            with failure_context(batch_id=batch_id, claim_id=manifest.claim_id):
                path = Path(extract(manifest, store.root, request.retry))
            result.update(state='succeeded', attempt=path.parent.name)
            progress['succeeded'] += 1
        except Exception as error:
            log_exception('extract_claim', batch_id=batch_id, claim_id=manifest.claim_id)
            code = ('job_in_progress' if isinstance(error, JobInProgress) else
                    'retry_required' if isinstance(error, RetryRequired) else 'extraction_failed')
            result.update(state='failed', error=code)
            progress['failed'] += 1
        progress['completed'] += 1
        store.save_batch(progress)
    progress['state'] = 'completed'
    store.save_batch(progress)
    return progress


def run_batch(batch_id, request, store):
    with execution_lock:
        try:
            process_batch(batch_id, request, store,
                          lambda manifest, root, retry: extract_claim(manifest, root, retry=retry))
        except Exception:
            log_exception('run_batch', batch_id=batch_id)
            progress = store.batch(batch_id)
            fail_unfinished_batch(progress, 'batch_execution_failed')
            store.save_batch(progress)


def fail_unfinished_batch(progress, error):
    for result in progress['results']:
        if result['state'] in ('queued', 'running'):
            result.update(state='failed', error=error)
    progress.update(state='failed', error=error,
                    succeeded=sum(result['state'] == 'succeeded' for result in progress['results']),
                    failed=sum(result['state'] == 'failed' for result in progress['results']))
    progress['completed'] = progress['succeeded'] + progress['failed']

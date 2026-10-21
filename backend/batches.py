import uuid
from pathlib import Path
from threading import Lock
from typing import Literal

from prefect.settings import (PREFECT_API_KEY, PREFECT_API_URL, PREFECT_HOME,
                              PREFECT_SERVER_ANALYTICS_ENABLED, PREFECT_SERVER_EPHEMERAL_ENABLED,
                              temporary_settings)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .extraction.models import Manifest
from .storage import JobInProgress, LocalStore, RetryRequired

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


def process_batch(batch_id, request, output_root, extract):
    store = LocalStore(Path(output_root))
    progress = store.batch(batch_id)
    progress['state'] = 'running'
    store.save_batch(progress)
    for manifest, result in zip(request.claims, progress['results']):
        result['state'] = 'running'
        store.save_batch(progress)
        try:
            path = Path(extract(manifest, output_root, request.retry))
            result.update(state='succeeded', attempt=path.parent.name)
            progress['succeeded'] += 1
        except Exception as error:
            code = ('job_in_progress' if isinstance(error, JobInProgress) else
                    'retry_required' if isinstance(error, RetryRequired) else 'extraction_failed')
            result.update(state='failed', error=code)
            progress['failed'] += 1
        progress['completed'] += 1
        store.save_batch(progress)
    progress['state'] = 'completed'
    store.save_batch(progress)
    return progress


def run_batch(batch_id, request, output_root):
    with execution_lock:
        try:
            from .flows import claim_batch_flow

            with temporary_settings(updates={
                PREFECT_API_URL: None, PREFECT_API_KEY: None,
                PREFECT_HOME: output_root / 'prefect',
                PREFECT_SERVER_ANALYTICS_ENABLED: False, PREFECT_SERVER_EPHEMERAL_ENABLED: True,
            }):
                claim_batch_flow(batch_id, request, str(output_root))
        except Exception:
            store = LocalStore(output_root)
            progress = store.batch(batch_id)
            progress.update(state='failed', error='batch_execution_failed')
            store.save_batch(progress)

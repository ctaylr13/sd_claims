from pathlib import Path

from prefect import flow, task

from .batches import process_batch
from .extraction import extract_claim


@task(name='extract-local-claim', persist_result=False, retries=0)
def extract_claim_task(manifest, output_root, retry=False):
    return str(extract_claim(manifest, Path(output_root), retry=retry))


@flow(name='local-claim-batch', persist_result=False, retries=0, log_prints=False)
def claim_batch_flow(batch_id, request, output_root):
    return process_batch(batch_id, request, output_root, extract_claim_task)

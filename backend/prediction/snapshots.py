import hashlib
from pathlib import Path

from .models import ApprovalSnapshot, FrozenModel


def canonical_bytes(model: FrozenModel):
    import json
    return json.dumps(model.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode()


def digest(model: FrozenModel):
    return hashlib.sha256(canonical_bytes(model)).hexdigest()


def save_snapshot(root: Path, snapshot: ApprovalSnapshot):
    snapshot = ApprovalSnapshot.model_validate(snapshot)
    snapshot_id = digest(snapshot)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{snapshot_id}.json'
    payload = canonical_bytes(snapshot)
    try:
        with path.open('xb') as stream:
            stream.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError('Existing snapshot has been modified') from None
    return snapshot_id


def load_snapshot(root: Path, snapshot_id: str):
    import re
    if not re.fullmatch(r'[a-f0-9]{64}', snapshot_id):
        raise ValueError('Invalid snapshot ID')
    payload = (root / f'{snapshot_id}.json').read_bytes()
    if hashlib.sha256(payload).hexdigest() != snapshot_id:
        raise ValueError('Snapshot integrity check failed')
    snapshot = ApprovalSnapshot.model_validate_json(payload)
    if digest(snapshot) != snapshot_id:
        raise ValueError('Snapshot is not canonical')
    return snapshot

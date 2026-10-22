# Configuration and limitations

See the [README](../README.md) for quick startup and the [setup guide](setup.md)
for local development, downloaded files, and Google Drive authorization.

## Claim sources and document review

`CLAIMS_CSV_PATH` selects a specific local CSV. Leave it empty to prefer
Google Drive, falling back to `data/claims.csv` and `data/evidence/` when Drive
is unavailable (`/inputs/claims.csv` and `/inputs/evidence/` in Docker).
`/api/source` reports the selected source or the need for setup. Use
`examples/claims.csv` for the column names. Lookups reread the source, keeping
recorded inputs separate from historical outcomes. CSV versions use a content
hash. Set `CLAIMS_EVIDENCE_ROOT` to attach downloaded PDFs from subfolders named
for each tracking number; see the [downloaded files walkthrough](setup.md#use-downloaded-claims-and-pdfs).
Local PDF links serve files only inside the matching claim folder, skip symlinks,
and are disabled in Drive mode. CSV mode has no Drive evidence links.

Paste a manifest into Document review or submit it to `POST /api/batches`:

```json
{
  "claims": [{
    "claim_id": "example",
    "documents": [{"document_id": "ledger", "kind": "ledger", "path": "/inputs/ledger.pdf"}]
  }],
  "retry": false
}
```

Docker mounts the host's `data/` at `/inputs/`; local execution uses host paths.
Limits: 20 claims/batch, 20 documents/claim, 50 MiB/PDF, 200 MiB combined PDFs/claim.
The backend's `/docs` has endpoint schemas.

## Cache and recovery

Extraction reuses results only when source bytes, manifest settings, tool/code
versions and retained artifact hashes match. Storage lives at `cache/extraction`
locally and `/app/cache` in Docker's persistent volume. Back up the SQLite database
and artifact files together. HTTP responses are not cached.

Failed, interrupted or damaged jobs require explicit retry. After an API restart,
unfinished batches are marked interrupted; resubmit with `retry: true` to reuse
verified successes and retry the rest. Use one API worker on a trusted local
machine. The API reads local paths, and diagnostic logs can contain private paths.

CLI extraction uses the same store:

```sh
.venv/bin/python -m backend.extraction manifest.json --output cache/extraction
# Add --retry for failed, interrupted or damaged results.
```

## Model setup

The model and evaluation JSON files stay outside Git and the Docker image.
Put them in `cache/review/`; Docker mounts that directory read-only at `/models/`.
Set these in `.env`, using each file's `shasum -a 256` value:

```sh
DOCKER_RECOMMENDATION_MODEL_PATH=/models/model.json
DOCKER_RECOMMENDATION_MODEL_SHA256=<model hash>
DOCKER_RECOMMENDATION_EVALUATION_PATH=/models/evaluation.json
DOCKER_RECOMMENDATION_EVALUATION_SHA256=<evaluation hash>
```

For a local API, use the same names without `DOCKER_` and absolute host paths.
Restart the API after changes; for Docker, run `docker compose up -d`.
Existing process variables override `.env`. Missing, modified or incompatible
artifacts produce an unavailable result.

To generate new artifacts, install `requirements-prediction.txt` and run:

```sh
.venv/bin/python -m backend.prediction.recommendations train DEVELOPMENT_ROOT --output model.json
.venv/bin/python -m backend.prediction.recommendations evaluate DEVELOPMENT_ROOT --output evaluation.json
```

`DEVELOPMENT_ROOT` needs the preserved reservation, development/test-context
source exports and `evaluation.json` defining development folds. Use new output
paths and keep reserved claims excluded. Changed inference code requires a
compatible refit and evaluation; never replace stored code hashes to bypass checks.
Serving exported models needs only the backend dependencies.

The Drive Compose override mounts host credentials and a writable token directory.
It runs the backend as container root for owner-only OAuth files; default CSV mode
runs as an unprivileged user.

## Interpretation and known limits

- Approval and decline predictions describe historical outcomes; neither verifies
  coverage or establishes a supported decision. Amounts are conditional on approval.
- A supported decision needs applicable policy terms and cited, reviewed facts.
  Missing evidence means review, not proof supporting denial.
- Metrics measure historical agreement on reused development folds. Independent
  blinded source/policy review and an untouched evaluation remain outstanding.
- Unknown amounts stay unknown. OCR confidence does not establish correct table
  alignment; OCR rows require visual review. Claim 155 remains a known scan failure.
- Native cell/statement verification helpers remain opt-in. Waiver selections and
  fees do not establish insurer enrollment or coverage. Small Gemini studies added
  no accepted coverage beyond local recovery and do not establish general accuracy.
- Approved historical bundles require reviewed source versions, section hashes,
  lineage, receipt timestamps and cutoffs. No real approvals ship. Candidate 53
  remains a robustness fixture outside historical scoring.

Private studies and prior documentation remain under `data/`, `cache/` and
`local-backup/`. The current-code October 22 model/evaluation are in
`cache/review/*-finish-20261022.json`, selected by private `.env` settings.
Use `scripts/capture_environment.py` before freezing another study; retain original
artifacts and their matching source together.

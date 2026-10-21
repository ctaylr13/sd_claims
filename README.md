# Security deposit claims assistant

Work in progress. Public documentation and walkthrough will be added at the end.

Experimental extraction findings and the recommendation evaluation boundary are
documented in [RESEARCH.md](RESEARCH.md).

Local extraction stores versioned results in SQLite and private artifact files:

```sh
.venv/bin/python -m backend.extraction manifest.json --output cache/extraction
# Explicitly retry failed, interrupted, or damaged results:
.venv/bin/python -m backend.extraction manifest.json --output cache/extraction --retry
```

The request key covers document SHA-256 hashes, source references and versions,
claim/document IDs, parser/schema/config versions, extraction tool versions, and
all manifest settings (including forced OCR pages, effective date, and recorded
request citations). Extraction parses the captured bytes used for that key.
Bump `PARSER_VERSION` when rules change, `CONFIG_VERSION` when extraction defaults
change, and `SCHEMA_VERSION` when report structure changes. Tool versions are
collected automatically. Source IDs/versions must be supplied in the manifest;
the local store does not refresh Drive sources.

Each attempt has its own directory. Files and directories are private (0600 and
0700). The SQLite transaction publishes a result pointer and a SHA-256 inventory
only after all artifacts are written and synced. Reuse verifies every inventoried
file; missing or modified files require `--retry`. A completed diagnostic report
may still contain unreadable pages, parsing errors, or review requirements;
completion does not mean the claim is valid or approved. Missing-source reports
are retained but never reused.

Jobs have `running`, `succeeded`, and `failed` states in `jobs.sqlite3`;
`backend.storage.LocalStore.status(key)` returns their metadata. Duplicate
requests reuse successful results, while active duplicates return an in-progress
error. A local OS file lock releases on process exit; interrupted jobs stay
`running` until explicitly retried. Retry cannot replace an active worker.
Attempts left by failures are retained for inspection. This store targets a
single machine with a local filesystem (macOS/Linux). Distributed leases,
fencing, automatic retries, garbage collection, and automated backups are deferred.

Run backend tests with `.venv/bin/python -m unittest discover -s tests -q`.

The first scan integration preserves per-page native/OCR routing and original
observations. Consume `validation.documents[].rows` for explicit row outcomes:
`Needs review` has a null `amount`, reasons, and retained source citations;
`Extracted` only describes supported extraction, not approval or visual accuracy.
OCR rows require visual review even at high word confidence. Unparsed regions,
unreadable pages, and document failures also produce review items when no row
could be recovered. Raw facts remain diagnostic observations, not approved values.

Manifests may include `review_regions` for known source defects:

```json
{
  "document_id": "ledger",
  "sha256": "<exact source SHA-256>",
  "page": 1,
  "bbox": [535, 90, 600, 150],
  "state": "cropped",
  "reason": "Missing right-edge pixels"
}
```

Each array item uses page points with a top-left origin; states are `cropped`,
`unreadable`, or `unreliable`. Regions must match the retained hash, page and
bounds; mismatches force review. Regions persist in the report and affect the
storage request key. Cropped values are never reconstructed from arithmetic.
This slice does not automatically detect every crop or scan defect.

Claim 155 remains a known scan failure: OCR can omit or corrupt values and service
periods, and finding amount text does not establish correct row/column alignment.
Its private baseline is `data/ocr-155-validation.json`; it is not a passing scan
fixture or evidence of reliable table extraction. Universal scan support and
broad OCR comparisons are deferred; only specific blockers for this slice belong
in follow-up fixes.

Local Prefect batches wrap `extract_claim` and use the same `cache/extraction`
store. FastAPI is pinned to 0.150.0 to share one environment with Prefect 3.9:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

The API runs the flow directly in a background thread; Prefect starts its
temporary local server. The flow uses local settings with no Prefect Cloud
credentials/API URL and disables telemetry. No deployment, worker, or separately
managed Prefect service is required.

Submit 1–20 unique claim manifests to `POST /api/batches`:

```json
{
  "claims": [
    {
      "claim_id": "local-example",
      "documents": [
        {"document_id": "ledger", "kind": "ledger", "path": "/absolute/path/ledger.pdf"}
      ]
    }
  ],
  "retry": false
}
```

Use absolute document paths; relative paths resolve from the API working directory.
The API validates the whole request before accepting it. A `202` response returns
`batch_id` and queued progress. Poll `GET /api/batches/{batch_id}` for totals and
ordered per-claim states; fetch a verified report at
`GET /api/batches/{batch_id}/claims/{index}/result` (zero-based index).
Results with missing or modified artifacts return `409`.

Claims run sequentially; an extraction exception fails only that claim and later
claims continue. `completed` means every claim was attempted, including failures.
A successful extraction can still contain diagnostic errors and review items.
Successful requests reuse existing verified artifacts. Resubmit the manifests
with `retry: true` to explicitly retry failed/interrupted/damaged extraction jobs;
Prefect retries and result caching are disabled so the local store owns reuse.

Progress survives API restarts, but background execution is not a durable queue.
Process termination can leave batches queued/running; inspect and resubmit them.
An orchestration failure marks the batch failed with a sanitized error.
One API process serializes batches with a local lock. Run a single API worker on
a trusted local machine: this endpoint can read local manifest paths. Production
hosting, distributed workers, restart recovery and concurrency tuning are deferred.

The batch tests include a real Prefect flow in the main environment. That test
requires local loopback access for Prefect's temporary server.

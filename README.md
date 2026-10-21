# Security deposit claims assistant

Work in progress. Public documentation and walkthrough will be added at the end.

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

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
PDFs are captured and hashed in chunks into a private temporary directory, then
processed sequentially from those files. Temporary captures are removed when the
request finishes, including cache hits and failures. Each claim supports at most
20 documents, each PDF at most 50 MiB, and combined PDFs at most 200 MiB. Size
limits are checked while capturing, before any PDF is parsed; exceeding a limit
rejects the claim. Retained originals and page artifacts remain in the local store.
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
Server-side failures use the `backend.diagnostics` Python logger at ERROR level
with the original traceback, an operation name, and claim/batch IDs when available.
Document failures also include document and page identifiers; shared lookup failures
include the requested claim IDs. The same context is attached as log-record fields
for structured handlers and included in the default text output. Client responses
and saved progress keep their sanitized error codes. Server logs can contain
exception details and local paths; keep them private alongside the extraction store.
One API process serializes batches with a local lock. Run a single API worker on
a trusted local machine: this endpoint can read local manifest paths. Production
hosting, distributed workers, restart recovery and concurrency tuning are deferred.

The batch tests include a real Prefect flow in the main environment. That test
requires local loopback access for Prefect's temporary server.

## Review workspace

Run the API above and `npm run dev`, then open `http://localhost:5173`.
Vite proxies `/api` to the local backend on port 8000. `npm run build` checks
TypeScript and creates the production frontend; `npm run lint` checks React code.
A production host must serve `dist/` and proxy `/api` to the same trusted local API.

- **Claim lookup:** enter up to 100 tracking numbers separated by commas or lines.
  Numeric ranges such as `14-25` expand inclusively; overlapping entries are
  deduplicated. Reversed ranges and requests exceeding 100 unique claims are rejected.
  Results retain individual errors, unverified recorded inputs, evidence links,
  and historical outcomes in a separate collapsed section.
- **Document review:** paste a JSON array of 1–20 claim manifests (or the API's
  `{ "claims": [...] }` envelope). Document paths are on the API machine.
  Progress polls every 1.5 seconds while queued/running. The last batch ID is
  remembered locally; any saved ID can be loaded manually. Polling errors offer
  refresh without resubmission. Explicit retry is a separate checkbox when
  submitting manifests; loading a batch never restarts work.
- **Results and evidence:** select a successful extraction to fetch its verified
  report. Expand fields to inspect raw/normalized values, competing observations,
  limitations, and citations. Citation buttons open the retained page image with the cited region highlighted,
  alongside its quote, hash, and page-point coordinates. Page controls navigate
  the document; a separate link opens the retained PDF at the cited page. Evidence
  endpoints verify the report, source PDF, and requested page image against
  the stored inventory. Only inventoried files inside the selected attempt are served.
  Report loading and extraction reuse still verify every artifact; damage to unrelated
  pages does not block viewing intact evidence. Failed checks and unknown row amounts remain
  explicit. The workspace does not issue fact approvals or claim decisions.

Research recommendations are optional and use the existing current-record model.
Install `requirements-prediction.txt`, train/export a model using the
`backend.prediction.recommendations train` CLI, and generate aggregate metrics
with its `evaluate` CLI (see `backend/prediction/README.md`). Set these environment
variables on the API process, or save them in the repository’s private `.env`
file. The API loads `.env` on startup without overriding existing process variables:

```sh
export RECOMMENDATION_MODEL_PATH=/absolute/path/model.json
export RECOMMENDATION_MODEL_SHA256=<sha256-of-model-json>
export RECOMMENDATION_EVALUATION_PATH=/absolute/path/evaluation.json
export RECOMMENDATION_EVALUATION_SHA256=<sha256-of-evaluation-json>
```

Use `shasum -a 256 /absolute/path/file.json` to inspect an artifact hash. No
model/report is auto-selected from private experiment directories. Missing,
changed, or incompatible artifacts produce an explicit unavailable state.

`POST /api/claims/{tracking_number}/recommendation` freshly reads the claim and
passes only the allowlisted recorded input columns to the hash/code-verified
model. Historical outcomes and evidence filenames are not model inputs. Reserved
claims and robustness candidate 53 are rejected. All outputs remain needs-review,
including research decisions and conditional approved ceilings. Missing ceilings
are displayed as unavailable, never zero; missing limits retain an explicit
unbounded advisory. Extracted document observations are not silently promoted to
approved model inputs.

`GET /api/review/metrics` serves only aggregate fields from a verified
`recommendations evaluate` report with development-only scope and no reserved
outcomes loaded. It exposes historical agreement, decline recall, a confusion
matrix, matched-subset ceiling errors and benchmarks, scoring coverage, and the
independent-review boundary. Per-claim evaluation records are excluded. These
metrics do not establish independent correctness or final-test performance.

Multi-claim lookup results show predictions, recorded status, and conditional
ceilings in a compact table. Generate a recommendation for the selected claim or
all found claims. Expand a claim’s recorded comparison for its approved amount,
decision match, and dollar difference. Generated results persist while opening
other claims; a new lookup starts a new comparison. Both the table and the
selected claim use the same result. Batch generation reads the live source once
and verifies the model once, preserving individual lookup, reserved-claim, and
recommendation failures.

Historical comparisons are attached **after** inference using outcomes from the
same fresh lookup as the recommendation inputs. They never become model inputs.
Only Approved, Approved Revised, Posted, and Declined statuses have a decision
comparison; contradictory decline/positive-amount records stay unscored.
Conditional amount comparisons require an approved outcome with a positive valid
recorded amount within its available recorded limit, plus a non-withheld ceiling.
Absolute percentage error uses the recorded amount as its denominator; zero,
missing, invalid, declined, and inconsistent targets are not assigned zero error.

Model-level agreement and decline recall are available in the Model evaluation
tab. They measure historical agreement on reused development folds, not per-claim
confidence or independently verified correctness. Single-claim lookups open the
claim directly; multiple claims use a compact results table. Recorded inputs,
source provenance, historical comparisons, and recommendation diagnostics remain
available in expandable sections.

Lookup, recommendation requests, and batch submission show animated loading bars
while awaiting an API response. Completed recommendation requests show ready and
failed counts. Extraction batches show actual processed counts and percentages,
with succeeded and failed counts listed separately. Loading bars do not invent intermediate percentages for
requests that return all results at once. Motion is disabled when the device
requests reduced animation.

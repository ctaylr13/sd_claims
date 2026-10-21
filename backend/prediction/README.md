# Approved prediction and evaluation inputs

`backend.prediction` builds historical initial-decision inputs without reading Drive,
extraction reports, whole sheet rows, PDFs, filenames, or answer keys. It does not
approve existing evidence or make any current claim training-eligible. The existing
lookup API remains a research view with historical context, not a prediction API.

## Approval contract

A trusted review process supplies an `ApprovalSnapshot` and `CandidateFact` records.
No real source or fact approvals ship with this implementation. `FACT_KEYS` and
`SOURCE_KINDS` define the supported vocabulary; membership alone grants no approval.

Each snapshot records the claim, reviewer, review time, verified decision cutoff and
its evidence hash, exact source IDs/versions/content hashes, independently reviewed
section IDs/content hashes, and approved hashes of entire candidate fact records.
The fact hash binds its ID, key, state, value, and every lineage reference. Replacing
a value, source version, section, or lineage requires a new review and snapshot.
All contributing sections must pass; a permitted lease cannot launder a fact derived
from a denial letter. Archive members need their own source and section approvals.

Sources and sections each need verified receipt evidence: an aware timestamp,
receipt type, and receipt artifact hash. The review binds that receipt to the exact
source version and section bytes. Document dates, signatures, Drive timestamps,
folder membership, and OCR success cannot establish receipt. Both receipt timestamps
must be strictly before the cutoff. Unknown/date-only cutoffs and unverified receipts
remain excluded. The proof fields record reviewed evidence; this module does not
fetch or authenticate receipts. Future adapters must verify those links before
issuing approvals.

Decision letters/rationale, outcomes, collection demands, and retrospective reports
have no permitted source kind. Answer-bearing or unreviewed sections are rejected
even inside an otherwise permitted source. Raw text, filenames, and arbitrary extra
metadata are forbidden by the schemas. Content classification is a trusted review
input, not a keyword detector.

`save_snapshot(root, snapshot)` writes canonical, content-addressed JSON exclusively;
it never overwrites an existing snapshot. `load_snapshot(root, snapshot_id)` verifies
the bytes and validates the complete schema. Nested records are frozen and collections
are tuples. Updates create a new ID, and edits to retained files fail integrity checks.
This is local write-once artifact handling; durable storage and storage permissions
remain separate integration work.

## Build a bundle

```python
from backend.prediction import build_prediction_bundle, load_snapshot

snapshot = load_snapshot(snapshot_root, snapshot_id)
bundle = build_prediction_bundle(snapshot, candidates)
```

Or, with a JSON array of candidate records:

```sh
.venv/bin/python -m backend.prediction SNAPSHOT_ROOT SNAPSHOT_ID candidates.json --output bundle.json
```

The output file must not already exist. Each supported key has an explicit state.
Absent candidates remain `unreviewed`; rejected availability becomes
`availability_unverified`; other rejected candidates become `excluded`. Approved
`missing`, `conflicting`, `unreadable`, `unreviewed`, `failed`, and `not_applicable`
states remain distinct and have null values. A missing or unknown amount never
becomes zero. Duplicate candidates for a key are excluded instead of selecting one.
Conflicting raw observations remain in upstream review artifacts, outside the bundle.
Rejection records contain only candidate indexes and fixed reason codes.

`eligible` means the bundle contains at least one permitted present fact. It does
not mean the evidence is complete, policy criteria are verified, the claim has a
valid training label, or a final evaluation set has been selected. Claim IDs,
snapshot IDs, and cutoff metadata are audit/join fields; model features are the
approved fact records only.

## Evaluation boundary and candidate 53

Answer-key loading and joining live only in `backend.prediction.evaluation`, outside
the prediction exports and CLI. `load_answer_key` checks an explicit
`AccessPurpose.EVALUATION` before reading a file; prediction, robustness, missing,
and string-valued access are denied. `build_evaluation_inputs` requires the same
access, verifies claim identity, and builds predictions through the identical
allowlist path before pairing them with the reviewed key. Unknown decision and
ceiling labels stay explicit. Nonfinal statuses are not converted to final labels.

This is an in-process purpose boundary for trusted local callers. It is not user
authentication or a sandbox against Python code with filesystem access. A future
service must derive evaluation access from server-owned authorization and keep key
storage unreadable to prediction workers. There is no answer-key HTTP endpoint or
prediction CLI flag to reveal answers.

Candidate 53 (including padded numeric IDs) is rejected by historical snapshots
and answer keys. Keep it in separate robustness fixtures for placeholder and
missing-evidence behavior; it cannot enter historical prediction or scoring.
Storage and OCR adapters can follow without weakening these checks. Until verified
receipt/version approvals exist, their inputs remain excluded.

## Verification

Run `.venv/bin/python -m unittest discover -s tests -q`. The synthetic prediction
fixtures exercise allowed facts, rejected fields/sources/sections, every lineage
reference, receipt/cutoff uncertainty, changed versions/values, unknown states,
snapshot mutation, evaluator access, and isolation of candidate 53. They confer no
approvals on live evidence.

## Local experiments

Install `requirements-prediction.txt` for the planned scikit-learn experiments.
Neither experiment runner is imported by the lookup API or prediction CLI.

`python -m backend.prediction.experiment PLAN --output REPORT` uses reviewed
snapshots and evaluation-only keys. `DevelopmentPlan` requires explicit reserved
IDs, disjoint train/validation claim groups, and claim-specific artifact paths.
It never loads a final test split. The review-only baseline in `baseline.py`
provides no approval or covered-loss rule.

`python -m backend.prediction.retrospective ROOT --output REPORT` is a separate
exploratory current-sheet workflow. ROOT contains `reservation.json`,
`development-source.json` and `test-context-source.json`. The loader checks exact
header positions, bounded development-only ranges and claim identities against
the reservation before building features. Policy text is used only to exclude
test contexts. Property addresses define group hashes, never features. Outcome
fields are labels only. Source and code hashes, fold assignments, metrics and
interpretations are retained in the report. Output files must not already exist.

The retrospective runner does not issue fact approvals, change historical
eligibility, or establish pre-decision availability. It performs schema/status
and amount-consistency review, not independent re-adjudication. Both runners
keep recommendations advisory. Private datasets, research and approach decisions
remain under the ignored data directory; no model is served by the API.

`python -m backend.prediction.tuning ROOT --protocol PROTOCOL` freezes a tuning
protocol against the existing `evaluation.json` and source fingerprints.
Then run the same command with `--output REPORT` to compare parameters,
duration/calendar features and decision cutoffs inside nested property-group
development folds. The outer folds and later-date development claims retain
their earlier assignments; only inner training folds select settings/cutoffs.
Changing the frozen protocol or source bytes fails validation. This workflow
has no reserved-data loader and reports scoring coverage separately from the
advisory-use policy. Protocol and report output paths must not already exist
when they are created.

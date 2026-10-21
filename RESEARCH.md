# Extraction findings and recommendation readiness

The useful extraction changes are opt-in helpers:

- `backend/extraction/program_cells.py` reads explicit waiver selections and
  monetary fields from independently located native table cells. It retains raw
  characters and coordinates while excluding underline and white signature tags.
- `backend/extraction/program_verification.py` captures exact PDF bytes, checks
  source identity and complete-document context, and verifies proposals against
  those readings. Model values and boxes never determine the source read.
  Blank, ambiguous, conflicting, unsupported and image-only fields require review.
- `backend/extraction/statement_rows.py` recovers dated charges after a charge
  heading and aligned columns, with row and originating-header citations.

These helpers are not enabled in the main API and do not issue fact approvals.
Program elections and fees do not establish actual insurer enrollment or premium
payment. Source receipt/cutoff approvals still control prediction eligibility.

## Evidence retained

On the known development set, cell recovery resolved five correct proposals that
had remained under review: accepted coverage rose from 12/17 to 17/17 with matching
source regions. This was development replay, not independent validation.

The next method was frozen before source selection. Six fresh pages from four
claims were annotated before six Gemini calls. Both extended local extraction
and local plus verified Gemini recovered 9/9 annotated facts with matching source
regions, zero wrong/extra acceptances and zero unsafe acceptances. Gemini proposed
one unsupported waived-deposit amount from an unchecked alternative; verification
withheld it. Two negative pages returned no proposals. Two image-based forms were
excluded before calls. Raw model coordinates failed localization for the nine
correct identities; accepted anchors came from the source reader.

These small, selected, mostly shared-template studies do not establish general
accuracy, increased supported-denial recall or prediction improvement. Gemini
added no accepted coverage beyond local recovery. Historical outcomes were not
used in the extraction trials, and reserved evidence remained closed.

Private source annotations, responses, frozen code and detailed findings remain
in `cache/gemini-cells-v4/` and earlier study folders. Pre-cleanup source and tests
are preserved with hashes in
`local-backup/research-before-slim-2026-10-21/manifest.json`; its `files/` tree uses
original repository-relative paths. Restore those files to reproduce the original
runners and their exact code-hash checks. Live source paths changed during cleanup;
the old studies must not be represented as newly frozen against the slim code.

## Next recommendation evaluation

Ready to build and evaluate a review-first recommendation baseline; improved
extraction alone does not validate denial rules. The retained
`backend/prediction/recommendations.py`, `artifacts.py`, `outcomes.py` and `quality.py`
provide current-record research models, benchmarks and exploratory data/label
sensitivity checks. They do not consume these new source facts or prove decision
correctness. Existing approval and reserved-set boundaries remain unchanged.

1. Document each applicable rule and its required facts. Return a cited candidate
   reason or needs-review result when evidence is missing or conflicting.
2. Independently review a claim set using source evidence and governing terms,
   without historical decisions. Include supported, unsupported and unresolved
   reasons; missing evidence must not become evidence of noncompliance.
3. Compare identical claims with baseline facts versus verified enriched facts.
   Measure supported denial reasons recovered, unsupported denial flags, review
   coverage and amount errors separately. More denials alone is not success.
4. Compare documented rules and simple benchmarks first. Train a model only if
   it adds value on development data, then evaluate a frozen method on an untouched
   set. Report historical agreement separately from independently reviewed correctness.

Image-only forms, different form templates, positive authenticated enrollment and
cross-document reconciliation still need evaluation. The new facts may support
or weaken a denial hypothesis; the net effect has not been measured.

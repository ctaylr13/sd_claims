"""Provisional outcome review and development-only target sensitivity."""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .experiment import classification_metrics
from .models import FrozenModel
from .parsing import canonical_id
from .retrospective import evaluate_task, file_hash
from .tuning import dataset

DenialFamily = Literal['payment_requirement', 'program_enrollment', 'charge_coverage',
                       'lease_term', 'possession', 'unknown']
Disposition = Literal['duplicate', 'tenant_payment_or_settlement', 'withdrawn',
                       'incomplete_evidence', 'unknown']
CONTEXT_NAMES = frozenset({'decline-context-source.json', 'decline-complete-comment-context.json'})


class OutcomeAnnotation(FrozenModel):
    claim_id: str
    denial_family: DenialFamily = 'unknown'
    dispositions: tuple[Disposition, ...] = ('unknown',)
    conflicts: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = Field(min_length=1)
    interpretation: str = Field(min_length=1)

    @model_validator(mode='after')
    def explicit_disposition(self):
        if not self.dispositions or len(set(self.dispositions)) != len(self.dispositions):
            raise ValueError('Explicit unique dispositions required')
        if 'unknown' in self.dispositions and len(self.dispositions) != 1:
            raise ValueError('Unknown cannot coexist with known dispositions')
        return self


class OutcomeReview(FrozenModel):
    version: Literal['provisional-outcomes-1'] = 'provisional-outcomes-1'
    reviewed_by: str = Field(min_length=1)
    evidence_status: Literal['historical_context_provisional'] = 'historical_context_provisional'
    source_hashes: dict[str, str]
    annotations: tuple[OutcomeAnnotation, ...]

    @model_validator(mode='after')
    def valid_inventory(self):
        ids = [canonical_id(item.claim_id) for item in self.annotations]
        if len(set(ids)) != len(ids) or '53' in ids:
            raise ValueError('Duplicate or robustness annotation')
        if not self.source_hashes or not set(self.source_hashes) <= CONTEXT_NAMES:
            raise ValueError('Only captured development context sources are supported')
        return self


def target(annotation):
    if annotation.conflicts or annotation.dispositions != ('unknown',) or annotation.denial_family == 'unknown':
        return 'unknown'
    return 'documented_denial_hypothesis'


def load_review(root, path, cohort):
    review = OutcomeReview.model_validate_json(path.read_bytes())
    for name, expected in review.source_hashes.items():
        if file_hash(root / name) != expected:
            raise ValueError('Historical context source changed')
    expected = {item['claim_id'] for item in cohort if item['decision'] == 'declined'}
    actual = {canonical_id(item.claim_id) for item in review.annotations}
    if actual != expected:
        raise ValueError('Review must cover exactly the qualified development declines')
    return review


def protocol(root, review_path):
    cohort, _, previous, _ = dataset(root)
    load_review(root, review_path, cohort)
    return {'version': 'provisional-target-sensitivity-1',
        'review_sha256': file_hash(review_path), 'source_hashes': previous['source_hashes'],
        'method_code_hashes': {name: file_hash(Path(__file__).with_name(name)) for name in (
            'outcomes.py', 'retrospective.py', 'tuning.py', 'experiment.py', 'baseline.py', 'models.py',
            'parsing.py', 'features.py')},
        'baseline_sha256': file_hash(root / 'recommendation-evaluation-v2.json'),
        'target': 'historical approvals versus documented denial hypotheses; administrative/mixed/unknown dispositions excluded',
        'methods': ['majority', 'logistic', 'logistic_balanced', 'shallow_tree', 'boosted_tree'],
        'selection': 'No deployment selection; exploratory label sensitivity only',
        'splits': 'Existing development property-group folds, restricted without reassignment',
        'limitation': 'Historical-context labels are provisional. Approved controls are not independently adjudicated. Current inputs lack verified pre-decision timing. No correctness claim.'}


def compare(root, review_path, frozen):
    if frozen != protocol(root, review_path):
        raise ValueError('Frozen outcome protocol or sources changed')
    cohort, splits, _, reservation = dataset(root)
    review = load_review(root, review_path, cohort)
    annotations = {canonical_id(item.claim_id): item for item in review.annotations}
    retained = [i for i, item in enumerate(cohort) if item['decision'] == 'approved' or
                target(annotations[item['claim_id']]) == 'documented_denial_hypothesis']
    index = {original: updated for updated, original in enumerate(retained)}
    selected = [cohort[i] for i in retained]
    restricted = [([index[i] for i in train if i in index], [index[i] for i in valid if i in index])
                  for train, valid in splits]
    for train, valid in restricted:
        if {selected[i]['group'] for i in train} & {selected[i]['group'] for i in valid}:
            raise ValueError('Property group overlap')
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1):
        result = evaluate_task(selected, restricted, 'decision')
    original = json.loads((root / 'recommendation-evaluation-v2.json').read_text())
    prior = {item['claim_id']: item for item in original['out_of_fold']}
    comparison = classification_metrics([item['decision'] for item in selected],
                                        [prior[item['claim_id']]['research_decision'] for item in selected])
    return {'version': 'provisional-target-sensitivity-1', 'protocol': frozen,
        'reserved_outcomes_loaded': False, 'reserved_count': len(reservation['reserved_claim_ids']),
        'original_outcome_count': len(cohort), 'sensitivity_outcome_count': len(selected),
        'historical_approvals': sum(item['decision'] == 'approved' for item in selected),
        'documented_denial_hypotheses': sum(item['decision'] == 'declined' for item in selected),
        'excluded_declines': len(cohort) - len(selected),
        'disposition_counts': dict(Counter(value for item in review.annotations for value in item.dispositions)),
        'denial_family_counts': dict(Counter(item.denial_family for item in review.annotations)),
        'target_state_counts': dict(Counter(target(item) for item in review.annotations)),
        'existing_tree_on_same_subset': comparison, 'refitted_sensitivity': result,
        'annotations': [item.model_dump(mode='json') | {'training_target_state': target(item)}
                        for item in review.annotations],
        'review_queue': [{'claim_id': item.claim_id, 'denial_family': item.denial_family,
                          'dispositions': list(item.dispositions), 'conflicts': list(item.conflicts),
                          'evidence_references': list(item.evidence_references)}
                         for item in review.annotations if target(item) == 'unknown'],
        'deployment_status': 'No serving model or existing historical labels changed. No independent correctness established.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output is None:
        result, output = protocol(args.root, args.review), args.protocol
    else:
        result = compare(args.root, args.review, json.loads(args.protocol.read_bytes()))
        output = args.output
    with output.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()

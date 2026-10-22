"""Source-backed date repairs and fixed-model data-quality experiments."""

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .features import DATES, MONEY, row_features
from .models import FrozenModel
from .parsing import canonical_id, recorded_date, recorded_money
from .retrospective import evaluate_task, file_hash, load_rows
from .tuning import dataset

MODES = ('recorded', 'no_dates', 'masked_date_relations', 'source_repaired')
DURATIONS = ('lease_days', 'days_after_move_out', 'lease_days_remaining')


class DateRepair(FrozenModel):
    claim_id: str
    column: Literal['Claim Date', 'Lease Start Date', 'Lease End Date', 'Move-Out Date']
    original_value: str | None
    corrected_value: str
    source_id: str = Field(min_length=1)
    source_path: Path
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    page: int = Field(ge=1)
    locator: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    review_method: Literal['visual_source_page'] = 'visual_source_page'
    availability: Literal['pre_decision_unverified'] = 'pre_decision_unverified'

    @model_validator(mode='after')
    def real_date(self):
        if date.fromisoformat(self.corrected_value).isoformat() != self.corrected_value:
            raise ValueError('Correction requires an ISO date')
        if canonical_id(self.claim_id) == '53':
            raise ValueError('Robustness case cannot enter date repairs')
        return self


class RepairSet(FrozenModel):
    version: Literal['source-date-repairs-1'] = 'source-date-repairs-1'
    dataset_source_hashes: dict[str, str]
    repairs: tuple[DateRepair, ...]

    @model_validator(mode='after')
    def unique_cells(self):
        cells = [(canonical_id(item.claim_id), item.column) for item in self.repairs]
        if len(set(cells)) != len(cells):
            raise ValueError('Duplicate date repair cell')
        return self


def apply_repairs(rows, review, source_hashes):
    review = RepairSet.model_validate(review)
    if review.dataset_source_hashes != source_hashes:
        raise ValueError('Repair set belongs to another dataset version')
    indexed = {canonical_id(str(row['Tracking Number'])): row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError('Duplicate source claim')
    changes = {}
    for repair in review.repairs:
        identity = canonical_id(repair.claim_id)
        if identity not in indexed:
            raise ValueError('Repair is outside the development inventory')
        if indexed[identity].get(repair.column) != repair.original_value:
            raise ValueError('Recorded date differs from reviewed original')
        if file_hash(repair.source_path) != repair.source_sha256:
            raise ValueError('Reviewed document bytes changed')
        changes.setdefault(identity, {})[repair.column] = repair.corrected_value
    return [row | changes[identity] if identity in changes else row
            for row in rows for identity in [canonical_id(str(row['Tracking Number']))]]


def date_issues(row):
    dates = {name: recorded_date(row.get(name))[0] for name in DATES}
    reasons = []
    for start, end, reason in (
            ('Lease Start Date', 'Lease End Date', 'reversed_lease_term'),
            ('Move-Out Date', 'Claim Date', 'claim_before_move_out'),
            ('Lease Start Date', 'Claim Date', 'claim_before_lease_start'),
            ('Lease Start Date', 'Move-Out Date', 'move_out_before_lease_start')):
        if dates[start] and dates[end] and dates[start] > dates[end]:
            reasons.append(reason)
    return reasons


def quality_features(row, mode):
    if mode not in MODES:
        raise ValueError('Unknown quality feature mode')
    values = row_features(row)
    if mode == 'no_dates':
        return {key: value for key, value in values.items()
                if key not in DURATIONS and not any(key.startswith(name + ':') for name in DATES)}
    if mode == 'masked_date_relations':
        issues = date_issues(row)
        suppressed = set()
        if 'reversed_lease_term' in issues:
            suppressed.update(('lease_days', 'lease_days_remaining'))
        if 'claim_before_move_out' in issues:
            suppressed.add('days_after_move_out')
        for key in suppressed:
            values.pop(key, None)
            values[f'{key}:state=conflicting_order'] = 1.0
    return values


def audit(rows):
    records = [{'claim_id': str(row['Tracking Number']), 'issues': date_issues(row)} for row in rows]
    return {'rows': len(rows), 'date_states': dict(Counter(
                f'{name}:{recorded_date(row.get(name))[1]}' for row in rows for name in DATES)),
            'money_states': dict(Counter(f'{name}:{recorded_money(row.get(name))[1]}'
                                         for row in rows for name in MONEY)),
            'termination_states': dict(Counter(str(row.get('Termination Type') or 'missing').strip() for row in rows)),
            'date_issue_counts': dict(Counter(reason for item in records for reason in item['issues'])),
            'date_review_queue': [item for item in records if item['issues']],
            'interpretation': 'Chronology flags require interpretation; they are not automatic typo classifications.'}


def prepare(root, repair_path):
    cohort, splits, previous, reservation = dataset(root)
    for train, valid in splits:
        if {cohort[i]['group'] for i in train} & {cohort[i]['group'] for i in valid}:
            raise ValueError('Property groups overlap')
    rows, _ = load_rows(root)
    review = RepairSet.model_validate_json(repair_path.read_bytes())
    cleaned = apply_repairs(rows, review, previous['source_hashes'])
    return cohort, splits, previous, reservation, rows, cleaned, review


def protocol(root, repair_path):
    _, _, previous, _, _, _, _ = prepare(root, repair_path)
    return {'version': 'data-quality-comparison-1', 'repair_sha256': file_hash(repair_path),
            'baseline_sha256': file_hash(root / 'evaluation.json'),
            'source_hashes': previous['source_hashes'], 'modes': list(MODES),
            'code_hashes': {name: file_hash(Path(__file__).with_name(name)) for name in (
                'quality.py', 'retrospective.py', 'tuning.py', 'experiment.py', 'baseline.py', 'models.py',
                'parsing.py', 'features.py')},
            'selection': 'Fixed existing estimators and property-group folds; no parameter tuning or reserved data.',
            'scope': 'Exploratory current-record reconstruction; document repairs do not verify pre-decision availability or labels.'}


def compare(root, repair_path, frozen):
    if frozen != protocol(root, repair_path):
        raise ValueError('Frozen quality protocol or evidence changed')
    cohort, splits, _, reservation, rows, cleaned, review = prepare(root, repair_path)
    row_index = {str(row['Tracking Number']): row for row in rows}
    clean_index = {str(row['Tracking Number']): row for row in cleaned}
    results = {}
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1):
        for mode in MODES:
            selected = [item | {'features': quality_features(
                (clean_index if mode == 'source_repaired' else row_index)[item['claim_id']], mode)}
                        for item in cohort]
            results[mode] = {task: evaluate_task(selected, splits, task) for task in ('decision', 'ceiling')}
    return {'version': 'data-quality-comparison-1', 'protocol': frozen,
            'reserved_outcomes_loaded': False, 'reserved_count': len(reservation['reserved_claim_ids']),
            'audit_recorded': audit(rows), 'audit_repaired': audit(cleaned),
            'repair_count': len(review.repairs), 'repaired_claims': len({item.claim_id for item in review.repairs}),
            'repairs': [item.model_dump(mode='json') for item in review.repairs],
            'results': results,
            'folds': [{'train_ids': [cohort[i]['claim_id'] for i in train],
                       'validation_ids': [cohort[i]['claim_id'] for i in valid]} for train, valid in splits],
            'conclusion_boundary': 'True source corrections are retained regardless of fit quality. No corrected target labels, source timing approvals, or prospective accuracy claimed.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--repairs', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cleaned-output', type=Path)
    args = parser.parse_args()
    if args.output is None:
        result, path = protocol(args.root, args.repairs), args.protocol
    else:
        result, path = compare(args.root, args.repairs, json.loads(args.protocol.read_bytes())), args.output
    with path.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    if args.cleaned_output:
        _, _, previous, _, raw, cleaned, review = prepare(args.root, args.repairs)
        with args.cleaned_output.open('x') as stream:
            json.dump({'scope': 'current_record_research_only', 'source_hashes': previous['source_hashes'],
                       'recorded_rows': raw, 'effective_rows': cleaned,
                       'repairs': [item.model_dump(mode='json') for item in review.repairs]}, stream, indent=2)


if __name__ == '__main__':
    main()

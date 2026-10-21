"""Current-record research recommendations; no adjudication or reserved-data reader."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .artifacts import ResearchModels, export_models, predict
from .experiment import amount_metrics, canonical_id, classification_metrics
from .models import FrozenModel
from .retrospective import (DATES, MONEY, TOLERANCE, estimators, file_hash, load_rows,
                            recorded_date, recorded_money, row_features)
from .snapshots import digest
from .tuning import dataset, pipeline_for

VERSION = 'current-record-recommendations-3'
INPUT_COLUMNS = frozenset((*DATES, *MONEY, 'Termination Type'))
RULES = {
    'decision': 'Needs review: applicable governing coverage and adjudication rules are unverified.',
    'unknowns': 'Missing and invalid amounts are explicit; neither is a zero label.',
    'ceiling': 'Conditional approved ceiling, never covered loss or disbursement.',
    'bounds': 'Use a positive recorded limit when present. With a missing limit, display a nonnegative unbounded advisory and an explicit unavailable bound state. Neither verifies the policy limit.',
    'abstention': 'Withhold for invalid money/dates, nonpositive rent/limit, or no usable inputs. Missing money and inconsistent date order remain explicit review reasons, not prediction failures.',
    'comparisons': 'Majority, median, recorded limit and min(request, limit) are hypotheses, not policy rules.',
    'correctness': 'Historical agreement does not prove correctness. Independent review must use applicable policy and source evidence without historical answers.',
}


class ResearchInput(FrozenModel):
    claim_id: str = Field(min_length=1)
    values: dict[str, str | None]
    source_reference: str = Field(min_length=1)


class ModelArtifact(FrozenModel):
    version: Literal['current-record-recommendations-3'] = VERSION
    models: ResearchModels
    source_hashes: dict[str, str]
    code_hashes: dict[str, str]
    development_ids: tuple[str, ...]
    reserved_ids: tuple[str, ...]
    environment: dict[str, str]
    training_parameters: dict
    scope: Literal['current_sheet_research_only'] = 'current_sheet_research_only'

    @model_validator(mode='after')
    def isolated_inventory(self):
        development = {canonical_id(value) for value in self.development_ids}
        reserved = {canonical_id(value) for value in self.reserved_ids}
        if not development or not reserved or development & reserved or '53' in development:
            raise ValueError('Invalid model development/reservation inventory')
        if len(development) != len(self.development_ids) or len(reserved) != len(self.reserved_ids):
            raise ValueError('Duplicate inventory claim')
        return self


def code_hashes():
    return {name: file_hash(Path(__file__).with_name(name)) for name in (
        'recommendations.py', 'artifacts.py', 'retrospective.py', 'tuning.py',
        'experiment.py', 'baseline.py')}


def fit_models(cohort, train):
    decision = pipeline_for(estimators('decision')['shallow_tree'])
    decision.fit([cohort[i]['features'] for i in train], [cohort[i]['decision'] for i in train])
    accepted = [i for i in train if cohort[i]['ceiling_reason'] is None]
    if len(accepted) < 20:
        raise ValueError('Too few primary ceiling labels')
    ceiling = pipeline_for(estimators('ceiling')['boosted_tree'])
    ceiling.steps[:2] = decision.steps[:2]
    labels = [cohort[i]['ceiling'] for i in accepted]
    ceiling[-1].fit(ceiling[:-1].transform([cohort[i]['features'] for i in accepted]), labels)
    majority = Counter(cohort[i]['decision'] for i in train).most_common(1)[0][0]
    return export_models(decision, ceiling, labels, majority)


def recommend(models, request):
    request = ResearchInput.model_validate(request)
    unexpected = set(request.values) - INPUT_COLUMNS
    if unexpected:
        raise ValueError('Only allowlisted input columns are accepted')
    if canonical_id(request.claim_id) == '53':
        raise ValueError('Candidate 53 is reserved for robustness fixtures')
    values = request.values
    reasons = ['governing_policy_unverified', 'pre_decision_availability_unverified']
    blockers = []
    for column, name in MONEY.items():
        value, state = recorded_money(values.get(column))
        if state == 'missing':
            reasons.append(f'{name}_{state}')
        elif state != 'present':
            blockers.append(f'{name}_{state}')
        elif name in {'rent', 'limit'} and value == 0:
            blockers.append(f'{name}_nonpositive')
    dates = {}
    for column in DATES:
        dates[column], state = recorded_date(values.get(column))
        if state != 'present':
            reasons.append(f'{column}:{state}')
        if state == 'invalid':
            blockers.append(f'{column}:invalid')
    for start, end, reason in (('Lease Start Date', 'Lease End Date', 'reversed_lease_term'),
                               ('Move-Out Date', 'Claim Date', 'claim_before_move_out')):
        if dates[start] and dates[end] and dates[start] > dates[end]:
            reasons.append(reason)
    if not str(values.get('Termination Type') or '').strip():
        reasons.append('termination_missing')
    feature_values = row_features(values)
    if not any(name in feature_values for name in MONEY.values()) and not any(dates.values()) and not str(
            values.get('Termination Type') or '').strip():
        blockers.append('no_usable_inputs')
    if set(feature_values) - set(models.feature_names):
        reasons.append('unseen_feature_state_or_category')
    decision, raw = predict(models, feature_values)
    limit = recorded_money(values.get('Max Benefit'))[0]
    requested = recorded_money(values.get('Amount of Claim'))[0]
    estimate = round(float(np.clip(raw, 0, limit if limit is not None else np.inf)), 2) if not blockers else None
    bound_state = 'recorded_limit' if limit is not None and limit > 0 else 'unavailable'
    return {'version': VERSION, 'claim_id': request.claim_id, 'scope': 'current_sheet_research_only',
            'decision': 'needs_review', 'research_decision': decision,
            'conditional_ceiling': estimate, 'raw_research_ceiling': round(raw, 2),
            'ceiling_state': 'withheld' if blockers else 'advisory',
            'ceiling_bound_state': bound_state,
            'ceiling_condition': ('If approved; capped at an unverified recorded limit; not a payment estimate.'
                                  if bound_state == 'recorded_limit' else
                                  'If approved; policy limit unavailable, estimate has no policy cap; not a payment estimate.'),
            'review_reasons': reasons + blockers, 'withholding_reasons': blockers,
            'requires_review': True, 'correctness_verified': False,
            'benchmarks': {'majority_decision': models.majority_decision,
                           'training_median_ceiling': models.training_median,
                           'recorded_limit': limit,
                           'minimum_request_limit': min(requested, limit)
                           if requested is not None and limit is not None else None},
            'inputs': [{'column': column, 'value': values.get(column),
                        'source_reference': request.source_reference,
                        'availability': 'decision_time_unverified'} for column in sorted(INPUT_COLUMNS)]}


def _request(row, source_reference):
    return ResearchInput(claim_id=str(row['Tracking Number']),
                         values={column: row.get(column) for column in INPUT_COLUMNS},
                         source_reference=source_reference)


def _metrics(records, prediction_key):
    selected = [record for record in records if record['actual_ceiling'] is not None and
                record[prediction_key] is not None]
    return {'count': len(selected), 'metrics': amount_metrics(
        [r['actual_ceiling'] for r in selected], [r[prediction_key] for r in selected], TOLERANCE)
        if selected else None}


def evaluate(root):
    cohort, splits, previous, reservation = dataset(root)
    rows, _ = load_rows(root)
    by_id = {canonical_id(str(row['Tracking Number'])): row for row in rows}
    outputs, fold_records = [], []
    seen = set()
    for fold, (train, valid) in enumerate(splits):
        train_groups = {cohort[i]['group'] for i in train}
        if train_groups & {cohort[i]['group'] for i in valid}:
            raise ValueError('Property groups overlap')
        models = fit_models(cohort, train)
        fold_records.append({'fold': fold, 'model_sha256': digest(models),
                             'train_ids': [cohort[i]['claim_id'] for i in train],
                             'validation_ids': [cohort[i]['claim_id'] for i in valid]})
        for i in valid:
            item = cohort[i]
            if item['claim_id'] in seen:
                raise ValueError('Repeated validation claim')
            seen.add(item['claim_id'])
            result = recommend(models, _request(by_id[item['claim_id']],
                               f'development-source.json sha256:{previous["source_hashes"]["development-source.json"]}; claim:{item["claim_id"]}; source columns'))
            outputs.append({'claim_id': item['claim_id'], 'fold': fold, 'actual_decision': item['decision'],
                'actual_ceiling': item['ceiling'] if item['ceiling_reason'] is None else None,
                'research_decision': result['research_decision'], 'conditional_ceiling': result['conditional_ceiling'],
                'ceiling_bound_state': result['ceiling_bound_state'],
                'majority_decision': models.majority_decision, 'median': models.training_median,
                'limit': result['benchmarks']['recorded_limit'],
                'minimum_request_limit': result['benchmarks']['minimum_request_limit'],
                'withholding_reasons': result['withholding_reasons'], 'recommendation': result})
    if seen != {item['claim_id'] for item in cohort}:
        raise ValueError('Incomplete validation coverage')
    matched = [record for record in outputs if record['conditional_ceiling'] is not None and
               record['actual_ceiling'] is not None]
    false_approvals = [r for r in outputs if r['research_decision'] == 'approved' and r['actual_decision'] == 'declined']
    return {'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'development_only_historical_agreement', 'reserved_outcomes_loaded': False,
        'reserved_count': len(reservation['reserved_claim_ids']), 'rules': RULES,
        'source_hashes': previous['source_hashes'], 'code_hashes': code_hashes(),
        'baseline_evaluation_sha256': file_hash(root / 'evaluation.json'),
        'method': 'Fixed selected models; existing development folds, training-only preprocessing. Prior model selection used this development data; estimates are exploratory.',
        'decision': {name: classification_metrics([r['actual_decision'] for r in outputs],
                                                [r[name] for r in outputs])
                     for name in ('research_decision', 'majority_decision')},
        'ceiling_on_same_approved_advisory_subset': {name: _metrics(matched, name) for name in (
            'conditional_ceiling', 'median')},
        'recorded_limit_matched_subset': {name: _metrics([r for r in matched if r['limit'] is not None], name)
                                         for name in ('conditional_ceiling', 'limit')},
        'ceiling_by_bound_state': {state: {name: _metrics(
            [r for r in matched if r['ceiling_bound_state'] == state], name)
            for name in ('conditional_ceiling', 'median')} for state in ('recorded_limit', 'unavailable')},
        'minimum_request_limit_matched_subset': {name: _metrics(
            [r for r in matched if r['minimum_request_limit'] is not None], name)
            for name in ('conditional_ceiling', 'minimum_request_limit')},
        'coverage': {'development_rows': len(rows), 'qualified_outcomes': len(outputs),
                     'conditional_estimates': sum(r['conditional_ceiling'] is not None for r in outputs),
                     'scoreable_approved_advisories': len(matched),
                     'withheld': sum(r['conditional_ceiling'] is None for r in outputs),
                     'withholding_reasons': dict(Counter(reason for r in outputs for reason in r['withholding_reasons']))},
        'combined_research_risk': {'historical_declines_called_approved': len(false_approvals),
            'historical_declines_called_approved_with_amount': sum(r['conditional_ceiling'] is not None for r in false_approvals),
            'interpretation': 'No zero ceiling imputed for declines. Conditional ceiling error does not measure end-to-end correctness.'},
        'independent_correctness': {'status': 'not_evaluated', 'reviewed_claim_count': 0,
            'required': 'Independent blinded source/policy adjudication with citations; unresolved cases remain unknown. Historical statuses cannot establish correctness.'},
        'folds': fold_records, 'out_of_fold': outputs}


def train(root):
    import sklearn

    cohort, _, previous, reservation = dataset(root)
    return ModelArtifact(models=fit_models(cohort, list(range(len(cohort)))),
        source_hashes=previous['source_hashes'], code_hashes=code_hashes(),
        development_ids=tuple(item['claim_id'] for item in cohort),
        reserved_ids=tuple(reservation['reserved_claim_ids']),
        environment={'sklearn': sklearn.__version__, 'numpy': np.__version__},
        training_parameters={task: estimators(task)[name].get_params() for task, name in (
            ('decision', 'shallow_tree'), ('ceiling', 'boosted_tree'))})


def load_model(path, expected_sha256):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Model artifact hash changed')
    artifact = ModelArtifact.model_validate_json(raw)
    if artifact.code_hashes != code_hashes():
        raise ValueError('Model implementation changed; refit and reevaluate')
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('train', 'evaluate'):
        sub = commands.add_parser(command)
        sub.add_argument('root', type=Path)
        sub.add_argument('--output', type=Path, required=True)
    sub = commands.add_parser('recommend')
    sub.add_argument('model', type=Path)
    sub.add_argument('request', type=Path)
    sub.add_argument('--model-sha256', required=True)
    sub.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1):
        if args.command == 'recommend':
            artifact = load_model(args.model, args.model_sha256)
            request = ResearchInput.model_validate_json(args.request.read_bytes())
            if canonical_id(request.claim_id) in {canonical_id(value) for value in artifact.reserved_ids}:
                raise ValueError('Reserved claim requires a separate frozen final evaluation')
            result = recommend(artifact.models, request)
            result['model_sha256'] = args.model_sha256
        else:
            result = train(args.root).model_dump(mode='json') if args.command == 'train' else evaluate(args.root)
    with args.output.open('x') as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write('\n')


if __name__ == '__main__':
    main()

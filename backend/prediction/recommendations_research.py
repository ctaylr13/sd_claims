"""Current-record model fitting and development evaluation."""

from collections import Counter
from datetime import datetime, timezone

import numpy as np

from .artifacts import export_models
from .experiment import amount_metrics, classification_metrics
from .parsing import canonical_id
from .recommendations import INPUT_COLUMNS, VERSION, ModelArtifact, ResearchInput, code_hashes, recommend
from .retrospective import TOLERANCE, estimators, file_hash, load_rows
from .snapshots import digest
from .tuning import dataset, pipeline_for

RULES = {
    'decision': 'Needs review: applicable governing coverage and adjudication rules are unverified.',
    'unknowns': 'Missing and invalid amounts are explicit; neither is a zero label.',
    'ceiling': 'Conditional approved ceiling, never covered loss or disbursement.',
    'bounds': 'Use a positive recorded limit when present. With a missing limit, display a nonnegative unbounded advisory and an explicit unavailable bound state. Neither verifies the policy limit.',
    'abstention': 'Withhold for invalid money/dates, nonpositive rent/limit, or no usable inputs. Missing money and inconsistent date order remain explicit review reasons, not prediction failures.',
    'comparisons': 'Majority, median, recorded limit and min(request, limit) are hypotheses, not policy rules.',
    'correctness': 'Historical agreement does not prove correctness. Independent review must use applicable policy and source evidence without historical answers.',
}


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

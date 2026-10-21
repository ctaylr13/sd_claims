"""Development comparisons only; reserved examples have no loader here."""

import argparse
import json
import platform
from collections import Counter
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import Field, model_validator

from .baseline import BASELINE_VERSION, MONEY_KEYS, money_value, policy_limit, rules_baseline
from .builder import build_prediction_bundle
from .evaluation import AccessPurpose, load_answer_key
from .models import FACT_KEYS, CandidateFact, FrozenModel
from .snapshots import digest, load_snapshot


def canonical_id(value):
    value = value.strip()
    if not value:
        raise ValueError('Empty claim ID')
    return str(int(value)) if value.isdecimal() else value


class DevelopmentClaim(FrozenModel):
    claim_id: str
    group_id: str = Field(min_length=1)
    split: Literal['train', 'validation']
    snapshot_root: Path
    snapshot_id: str
    candidates_path: Path
    answer_key_path: Path


class DevelopmentPlan(FrozenModel):
    version: Literal['local-development-1'] = 'local-development-1'
    reserved_claim_ids: tuple[str, ...]
    claims: tuple[DevelopmentClaim, ...] = ()
    dollar_tolerance: float = Field(default=100, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def isolated_splits(self):
        reserved = {canonical_id(value) for value in self.reserved_claim_ids}
        if not reserved or len(reserved) != len(self.reserved_claim_ids):
            raise ValueError('An explicit, unique reserved ID inventory is required')
        seen, groups = set(), {}
        for claim in self.claims:
            identity = canonical_id(claim.claim_id)
            if identity in reserved or identity == '53' or identity in seen:
                raise ValueError('Reserved, robustness, or duplicate development claim')
            seen.add(identity)
            group = claim.group_id.strip()
            if not group or groups.setdefault(group, claim.split) != claim.split:
                raise ValueError('Related claims must stay in one split')
        return self


def load_development(plan):
    plan = DevelopmentPlan.model_validate(plan)
    rows = []
    for claim in plan.claims:
        snapshot = load_snapshot(claim.snapshot_root, claim.snapshot_id)
        if snapshot.claim_id != claim.claim_id:
            raise ValueError('Snapshot identity differs from split inventory')
        candidates = tuple(CandidateFact.model_validate(item)
                           for item in json.loads(claim.candidates_path.read_bytes()))
        bundle = build_prediction_bundle(snapshot, candidates)
        key = load_answer_key(claim.answer_key_path, access=AccessPurpose.EVALUATION)
        if key.claim_id != claim.claim_id:
            raise ValueError('Answer key identity differs from split inventory')
        rows.append((claim, bundle, key))
    return rows


def features(bundle):
    result = {}
    for fact in bundle.facts:
        if fact.key not in FACT_KEYS:
            raise ValueError('Feature outside the approved vocabulary')
        result[f'{fact.key}:state={fact.state}'] = 1.0
        if fact.state != 'present':
            continue
        if fact.key in MONEY_KEYS:
            value = money_value(fact.value)
            if value is None:
                raise ValueError('Malformed approved money fact requires review')
            result[fact.key] = float(value)
        elif fact.key.endswith('_date') or fact.key in {'confirmed_possession_end', 'key_return', 'rent_paid_through'}:
            try:
                result[fact.key] = float(date.fromisoformat(fact.value).toordinal())
            except (TypeError, ValueError):
                raise ValueError('Malformed approved date fact requires review') from None
        else:
            result[f'{fact.key}:value={fact.value}'] = 1.0
    return result


def classification_metrics(actual, predicted):
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, recall_score

    return {'accuracy': accuracy_score(actual, predicted),
            'balanced_accuracy': balanced_accuracy_score(actual, predicted),
            'decline_recall': recall_score(actual, predicted, pos_label='declined', zero_division=0),
            'confusion_labels': ['declined', 'approved'],
            'confusion_matrix': confusion_matrix(actual, predicted, labels=['declined', 'approved']).tolist()}


def amount_metrics(actual, predicted, tolerance):
    import numpy as np

    errors = np.asarray(predicted) - np.asarray(actual)
    absolute = np.abs(errors)
    return {'mae_dollars': float(absolute.mean()), 'median_absolute_error_dollars': float(np.median(absolute)),
            'p90_absolute_error_dollars': float(np.quantile(absolute, .9)),
            'signed_error_dollars': float(errors.mean()),
            'over_tolerance_rate': float((errors > tolerance).mean()),
            'under_tolerance_rate': float((errors < -tolerance).mean()),
            'within_tolerance_rate': float((absolute <= tolerance).mean())}


def _candidates(task):
    from sklearn.dummy import DummyClassifier, DummyRegressor
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

    if task == 'decision':
        return {'majority': DummyClassifier(strategy='most_frequent'),
                'logistic': LogisticRegression(C=1, max_iter=2000, random_state=0),
                'shallow_tree': DecisionTreeClassifier(max_depth=3, min_samples_leaf=5, random_state=0)}
    return {'median': DummyRegressor(strategy='median'), 'ridge': Ridge(alpha=10),
            'shallow_tree': DecisionTreeRegressor(max_depth=3, min_samples_leaf=5, random_state=0)}


def compare(plan: DevelopmentPlan):
    started = perf_counter()
    rows = load_development(plan)
    report = {'schema_version': 'local-comparison-1', 'plan_sha256': digest(plan),
              'python': platform.python_version(), 'baseline_version': BASELINE_VERSION,
              'reserved_claims_loaded': False, 'scope': 'development_only',
              'automatic_decision_coverage': 0.0, 'automatic_amount_coverage': 0.0,
              'human_review_rate': 1.0,
              'choice': 'review_first_rules; ML advisory experiments only', 'tasks': {}}
    report['inputs'] = [{'claim_id': claim.claim_id, 'split': claim.split,
                         'bundle_sha256': digest(bundle), 'answer_key_sha256': digest(key)}
                        for claim, bundle, key in rows]
    report['rules'] = dict(Counter(reason for _, bundle, _ in rows
                                   for reason in rules_baseline(bundle)['reasons']))
    for task in ('decision', 'ceiling'):
        selected = {'train': [], 'validation': []}
        excluded = Counter()
        for claim, bundle, key in rows:
            reason = None
            if not bundle.eligible:
                reason = 'no_eligible_facts'
            elif key.decision == 'unknown':
                reason = 'unknown_label'
            elif task == 'ceiling' and (key.decision != 'approved' or key.ceiling_state != 'present'):
                reason = 'no_approved_ceiling_label'
            elif task == 'ceiling' and policy_limit(bundle) is None:
                reason = 'policy_limit_unavailable'
            elif task == 'ceiling' and money_value(key.approved_ceiling) > policy_limit(bundle):
                reason = 'ceiling_label_above_limit_requires_review'
            try:
                values = features(bundle) if reason is None else None
            except ValueError:
                reason = 'malformed_fact'
            if reason:
                excluded[f'{claim.split}:{reason}'] += 1
                continue
            target = key.decision if task == 'decision' else float(key.approved_ceiling)
            selected[claim.split].append((values, target, bundle))
        train, validation = selected['train'], selected['validation']
        train_counts = Counter(row[1] for row in train) if task == 'decision' else None
        validation_counts = Counter(row[1] for row in validation) if task == 'decision' else None
        result = {'train_count': len(train), 'validation_count': len(validation),
                  'excluded': dict(excluded), 'models': {}, 'status': 'insufficient_reviewed_data',
                  'train_class_counts': dict(train_counts) if train_counts else None,
                  'validation_class_counts': dict(validation_counts) if validation_counts else None}
        report['tasks'][task] = result
        enough = len(train) >= 20 and len(validation) >= 5
        if task == 'decision':
            enough = enough and all(train_counts[label] >= 5 and validation_counts[label] >= 2
                                    for label in ('approved', 'declined'))
        if not enough:
            continue
        import sklearn
        import numpy as np
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.tree import export_text

        report['sklearn_version'] = sklearn.__version__
        result['status'] = 'exploratory_comparison'
        train_x, train_y = [r[0] for r in train], [r[1] for r in train]
        valid_x, valid_y = [r[0] for r in validation], [r[1] for r in validation]
        denominator = sum(claim.split == 'validation' for claim, _, _ in rows)
        for name, estimator in _candidates(task).items():
            pipeline = make_pipeline(DictVectorizer(sparse=False), StandardScaler(), estimator)
            tick = perf_counter()
            pipeline.fit(train_x, train_y)
            fit_seconds = perf_counter() - tick
            tick = perf_counter()
            predicted = pipeline.predict(valid_x)
            predict_seconds = perf_counter() - tick
            metrics = {'fit_seconds': fit_seconds, 'predict_seconds': predict_seconds,
                       'predicted_count': len(validation), 'advisory_coverage': len(validation) / denominator,
                       'requires_human_review': True, 'parameters': estimator.get_params()}
            names = pipeline[0].get_feature_names_out().tolist()
            if hasattr(estimator, 'coef_'):
                weights = np.asarray(estimator.coef_).reshape(-1)
                metrics['standardized_coefficients'] = dict(zip(names, weights.tolist()))
                metrics['intercept'] = np.asarray(estimator.intercept_).tolist()
                if task == 'decision':
                    metrics['positive_coefficient_class'] = estimator.classes_[1]
            elif hasattr(estimator, 'tree_'):
                metrics['tree_standardized_features'] = export_text(estimator, feature_names=names)
            if task == 'decision':
                metrics.update(classification_metrics(valid_y, predicted))
            else:
                limits = np.asarray([float(policy_limit(r[2])) for r in validation])
                bounded = np.round(np.clip(predicted, 0, limits), 2)
                metrics['raw'] = amount_metrics(valid_y, predicted, plan.dollar_tolerance)
                metrics['bounded'] = amount_metrics(valid_y, bounded, plan.dollar_tolerance)
                metrics['raw_limit_violation_rate'] = float((predicted > limits).mean())
            result['models'][name] = metrics
    report['total_seconds'] = perf_counter() - started
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = DevelopmentPlan.model_validate_json(args.plan.read_bytes())
    report = compare(plan)
    with args.output.open('x') as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write('\n')


if __name__ == '__main__':
    main()

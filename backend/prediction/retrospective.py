"""Exploratory current-sheet comparisons, isolated from approved historical inputs."""

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np

from .experiment import amount_metrics, classification_metrics
from .features import DATES, MONEY, row_features
from .parsing import APPROVAL_STATUSES, canonical_id, recorded_date, recorded_money

COLUMNS = {0: 'Tracking Number', 1: 'Claim Date', 3: 'Lease Street Address', 4: 'Lease City',
           5: 'Lease State', 6: 'Lease Zip', 7: 'Lease Start Date', 8: 'Lease End Date',
           9: 'Move-Out Date', 10: 'Monthly Rent', 11: 'Amount of Claim',
           12: 'Termination Type', 26: 'Policy', 27: 'Max Benefit', 28: 'Status',
           30: 'Approved Benefit Amount'}
TOLERANCE = 100


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _column_index(value):
    result = 0
    for character in value:
        result = result * 26 + ord(character) - 64
    return result - 1


def load_rows(root):
    reservation = json.loads((root / 'reservation.json').read_text())
    reserved = {canonical_id(value) for value in reservation['reserved_claim_ids']}
    expected = {item['row']: canonical_id(item['claim_id']) for item in reservation['development_claims']}
    if len(set(expected.values())) != len(expected) or reserved & set(expected.values()) or '53' in expected.values():
        raise ValueError('Invalid development reservation')
    if not reserved or len(expected) != len(reservation['development_claims']):
        raise ValueError('Missing reservation or duplicate source rows')
    if len(reserved) != len(reservation['reserved_claim_ids']):
        raise ValueError('Duplicate reserved identities')
    source = json.loads((root / 'development-source.json').read_text())
    for index, name in COLUMNS.items():
        if source['header'][index] != name:
            raise ValueError('Source columns changed')
    context = json.loads((root / 'test-context-source.json').read_text())
    rows, cells = {}, set()
    for part in source['ranges'] + context['ranges']:
        match = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)', part['requested_range'])
        if not match:
            raise ValueError('Unbounded source range')
        column, first, last = _column_index(match[1]), int(match[2]), int(match[4])
        width = _column_index(match[3]) - column + 1
        if (match[1], match[3]) not in {('A', 'M'), ('AB', 'AC'), ('AE', 'AE'), ('AA', 'AA')}:
            raise ValueError('Unexpected source rectangle')
        if not set(range(first, last + 1)) <= set(expected):
            raise ValueError('Source range crosses a reserved or unlisted row')
        if len(part.get('values', [])) > last - first + 1:
            raise ValueError('Source response exceeds requested rows')
        for offset, values in enumerate(part.get('values', [])):
            number = first + offset
            if len(values) > width:
                raise ValueError('Source response exceeds requested columns')
            record = rows.setdefault(number, {})
            for index, value in enumerate(values):
                position = (number, column + index)
                if position in cells:
                    raise ValueError('Overlapping source ranges')
                cells.add(position)
                if column + index in COLUMNS:
                    record[COLUMNS[column + index]] = value
    if set(rows) != set(expected):
        raise ValueError('Development rows missing from snapshot')
    for number, record in rows.items():
        identity = canonical_id(str(record.get('Tracking Number', '')))
        if identity != expected[number] or identity in reserved:
            raise ValueError('Development source identity changed')
    return list(rows.values()), reservation


def property_group(row):
    parts = [re.sub(r'[^a-z0-9]', '', str(row.get(name) or '').lower())
             for name in ('Lease Street Address', 'Lease City', 'Lease State')]
    if not all(parts):
        return None
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()


def qualify(row):
    identity = canonical_id(str(row['Tracking Number']))
    if identity == '53' or row.get('Status') == 'Test Claim' or re.search(r'\btest\b', str(row.get('Policy') or ''), re.I):
        return None, 'test_context'
    status = str(row.get('Status') or '').strip()
    if status in APPROVAL_STATUSES:
        decision = 'approved'
    elif status == 'Declined':
        decision = 'declined'
    else:
        return None, 'nonfinal_or_unknown_status'
    ceiling, ceiling_state = recorded_money(row.get('Approved Benefit Amount'))
    if decision == 'declined' and ceiling is not None and ceiling > 0:
        return None, 'decline_with_positive_ceiling'
    group = property_group(row)
    if group is None:
        return None, 'property_group_missing'
    limit, _ = recorded_money(row.get('Max Benefit'))
    ceiling_reason = None
    if decision != 'approved':
        ceiling_reason = 'not_approved'
    elif ceiling_state != 'present':
        ceiling_reason = f'ceiling_{ceiling_state}'
    elif ceiling == 0:
        ceiling_reason = 'approved_zero_ceiling_requires_review'
    elif limit is not None and ceiling > limit:
        ceiling_reason = 'ceiling_above_recorded_limit_requires_review'
    return {'claim_id': identity, 'group': group, 'decision': decision, 'ceiling': ceiling,
            'ceiling_reason': ceiling_reason, 'limit': limit, 'features': row_features(row),
            'without_limit': row_features(row, without_limit=True),
            'claim_date': recorded_date(row.get('Claim Date'))[0]}, None


def estimators(task):
    from sklearn.dummy import DummyClassifier, DummyRegressor
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

    if task == 'decision':
        return {'majority': DummyClassifier(strategy='most_frequent'),
                'logistic': LogisticRegression(C=1, max_iter=2000, random_state=0),
                'logistic_balanced': LogisticRegression(C=1, class_weight='balanced', max_iter=2000, random_state=0),
                'shallow_tree': DecisionTreeClassifier(max_depth=3, min_samples_leaf=15, random_state=0),
                'boosted_tree': GradientBoostingClassifier(n_estimators=100, max_depth=2, min_samples_leaf=15, random_state=0)}
    return {'median': DummyRegressor(strategy='median'), 'ridge': Ridge(alpha=10),
            'shallow_tree': DecisionTreeRegressor(max_depth=3, min_samples_leaf=15, random_state=0),
            'boosted_tree': GradientBoostingRegressor(loss='absolute_error', n_estimators=100,
                                                      max_depth=2, min_samples_leaf=15, random_state=0)}


def _interpretation(pipeline):
    from sklearn.tree import export_text

    estimator = pipeline[-1]
    names = pipeline[0].get_feature_names_out().tolist()
    scaling = {'feature_means': dict(zip(names, pipeline[1].mean_.tolist())),
               'feature_scales': dict(zip(names, pipeline[1].scale_.tolist()))}
    if hasattr(estimator, 'coef_'):
        return {'standardized_coefficients': dict(zip(names, np.asarray(estimator.coef_).reshape(-1).tolist())),
                'intercept': np.asarray(estimator.intercept_).tolist(), **scaling}
    if hasattr(estimator, 'tree_'):
        return {'standardized_tree': export_text(estimator, feature_names=names), **scaling}
    if hasattr(estimator, 'feature_importances_'):
        return {'training_impurity_importance_not_causal': dict(zip(names, estimator.feature_importances_.tolist()))}
    return {}


def evaluate_task(cohort, splits, task, *, feature_key='features', include_anomalies=False):
    from sklearn.base import clone
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.metrics import precision_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    labels = [item[task] for item in cohort]
    allowed = [task == 'decision' or item['ceiling_reason'] is None or
               (include_anomalies and item['decision'] == 'approved' and item['ceiling'] is not None)
               for item in cohort]
    validation_indexes = {int(i) for _, validation in splits for i in validation}
    score_indexes = sorted(i for i in validation_indexes if allowed[i])
    result = {'eligible_count': sum(allowed), 'evaluation_count': len(score_indexes),
              'coverage_of_final_labeled_cohort': len(score_indexes) / len(validation_indexes),
              'excluded': dict(Counter(item['ceiling_reason'] for item, accepted in zip(cohort, allowed) if not accepted)),
              'models': {}}
    for name, estimator in estimators(task).items():
        predictions = np.full(len(cohort), np.nan, dtype=object if task == 'decision' else float)
        confidence = np.full(len(cohort), np.nan)
        fit_seconds, predict_seconds, fold_scores, interpretations = 0, 0, [], []
        for train, valid in splits:
            train = [i for i in train if allowed[i]]
            valid = [i for i in valid if allowed[i]]
            if not valid or len(train) < 20:
                raise ValueError('Insufficient labeled examples in a development fold')
            if task == 'decision' and len({labels[i] for i in train}) != 2:
                raise ValueError('Development fold lacks a decision class')
            pipeline = make_pipeline(DictVectorizer(sparse=False), StandardScaler(), clone(estimator))
            tick = perf_counter()
            pipeline.fit([cohort[i][feature_key] for i in train], [labels[i] for i in train])
            fit_seconds += perf_counter() - tick
            tick = perf_counter()
            predicted = pipeline.predict([cohort[i][feature_key] for i in valid])
            predict_seconds += perf_counter() - tick
            predictions[valid] = predicted
            if task == 'decision':
                confidence[valid] = np.max(pipeline.predict_proba([cohort[i][feature_key] for i in valid]), axis=1)
            interpretations.append(_interpretation(pipeline))
            metrics = classification_metrics([labels[i] for i in valid], predicted) if task == 'decision' else amount_metrics(
                [labels[i] for i in valid], predicted, TOLERANCE)
            fold_scores.append(metrics)
        indexes = score_indexes
        actual = [labels[i] for i in indexes]
        predicted = predictions[indexes].tolist()
        metrics = classification_metrics(actual, predicted) if task == 'decision' else amount_metrics(actual, predicted, TOLERANCE)
        metrics.update({'fit_seconds_all_folds': fit_seconds, 'predict_seconds_all_folds': predict_seconds,
                        'predict_ms_per_claim': predict_seconds * 1000 / len(indexes),
                        'fold_metrics': fold_scores, 'parameters': estimator.get_params(),
                        'fold_interpretations': interpretations})
        if task == 'decision':
            metrics['decline_precision'] = precision_score(actual, predicted, pos_label='declined', zero_division=0)
            metrics['confidence_slices_not_calibrated'] = []
            for threshold in (.8, .9, .95, .99):
                kept = [i for i in indexes if confidence[i] >= threshold]
                metrics['confidence_slices_not_calibrated'].append({
                    'threshold': threshold, 'count': len(kept), 'coverage': len(kept) / len(indexes),
                    'errors': sum(labels[i] != predictions[i] for i in kept),
                    'declines_in_slice': sum(labels[i] == 'declined' for i in kept)})
        else:
            bounded_indexes = [i for i in indexes if cohort[i]['limit'] is not None]
            metrics['known_limit_count'] = len(bounded_indexes)
            metrics['raw_negative_rate'] = float(np.mean(np.asarray(predicted) < 0))
            if bounded_indexes:
                raw = np.asarray([float(predictions[i]) for i in bounded_indexes])
                limits = np.asarray([cohort[i]['limit'] for i in bounded_indexes])
                bounded = np.round(np.clip(raw, 0, limits), 2)
                metrics['bounded_known_limit_subset'] = amount_metrics([labels[i] for i in bounded_indexes], bounded, TOLERANCE)
                metrics['raw_limit_violation_rate'] = float(np.mean(raw > limits))
        result['models'][name] = metrics
        result.setdefault('out_of_fold', {})[name] = [
            {'claim_id': cohort[i]['claim_id'], 'actual': labels[i], 'prediction': predictions[i]}
            for i in indexes]
    return result


def rule_benchmarks(cohort, ceiling_result):
    result = {}
    for name in ('recorded_limit', 'minimum_request_limit'):
        actual, predicted, identities = [], [], []
        for item in cohort:
            if item['ceiling_reason'] is not None or item['limit'] is None:
                continue
            request = item['features'].get('request')
            if name == 'minimum_request_limit' and request is None:
                continue
            actual.append(item['ceiling'])
            identities.append(item['claim_id'])
            predicted.append(item['limit'] if name == 'recorded_limit' else min(request, item['limit']))
        result[name] = {'kind': 'explicit_hypothesis_not_policy', 'count': len(actual),
                        'coverage_of_final_labeled_cohort': len(actual) / len(cohort),
                        'metrics': amount_metrics(actual, predicted, TOLERANCE) if actual else None}
        matched = {}
        for model, outputs in ceiling_result['out_of_fold'].items():
            lookup = {row['claim_id']: row['prediction'] for row in outputs}
            matched[model] = amount_metrics(actual, [lookup[identity] for identity in identities], TOLERANCE) if actual else None
        result[name]['matched_raw_model_metrics'] = matched
    return result


def uncertainty(cohort, result, task):
    indexes = {item['claim_id']: item for item in cohort}
    outputs = result['out_of_fold']
    names = list(outputs)
    ids = [row['claim_id'] for row in outputs[names[0]]]
    grouped = {}
    for index, identity in enumerate(ids):
        grouped.setdefault(indexes[identity]['group'], []).append(index)
    groups = list(grouped.values())
    statistics = {name: [] for name in names}
    paired_gain = []
    rng = np.random.default_rng(20261021)
    arrays = {name: np.asarray([row['prediction'] for row in outputs[name]]) for name in names}
    actual = np.asarray([row['actual'] for row in outputs[names[0]]])
    for _ in range(1000):
        sample = np.concatenate([groups[i] for i in rng.integers(0, len(groups), size=len(groups))])
        for name, values in arrays.items():
            if task == 'decision':
                declines = actual[sample] == 'declined'
                statistic = float(np.mean(values[sample][declines] == 'declined')) if declines.any() else 0
            else:
                statistic = float(np.mean(np.abs(values[sample].astype(float) - actual[sample].astype(float))))
            statistics[name].append(statistic)
        if task == 'ceiling':
            paired_gain.append(statistics['median'][-1] - statistics['boosted_tree'][-1])
    report = {'method': '1000 paired property-group bootstrap resamples of fixed out-of-fold predictions',
              'limitation': 'Conditional sampling intervals; excludes retraining, label and source-timing uncertainty.',
              'metric': 'decline_recall' if task == 'decision' else 'mae_dollars',
              'intervals_95': {name: np.quantile(values, [.025, .975]).tolist() for name, values in statistics.items()}}
    if paired_gain:
        report['median_minus_boosted_mae_gain_95'] = np.quantile(paired_gain, [.025, .975]).tolist()
    return report


def learning_curve(cohort, splits):
    from sklearn.base import clone
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    results = []
    for fraction in (.25, .5, 1.0):
        record = {'training_group_fraction': fraction, 'tasks': {}}
        for task, model_name in (('decision', 'logistic'), ('ceiling', 'boosted_tree')):
            actual, predicted, train_counts = [], [], []
            for fold, (train, valid) in enumerate(splits):
                groups = sorted({cohort[i]['group'] for i in train})
                np.random.default_rng(20261021 + fold).shuffle(groups)
                chosen = set(groups[:max(1, int(len(groups) * fraction))])
                train = [i for i in train if cohort[i]['group'] in chosen and
                         (task == 'decision' or cohort[i]['ceiling_reason'] is None)]
                valid = [i for i in valid if task == 'decision' or cohort[i]['ceiling_reason'] is None]
                pipeline = make_pipeline(DictVectorizer(sparse=False), StandardScaler(), clone(estimators(task)[model_name]))
                pipeline.fit([cohort[i]['features'] for i in train], [cohort[i][task] for i in train])
                predicted.extend(pipeline.predict([cohort[i]['features'] for i in valid]).tolist())
                actual.extend(cohort[i][task] for i in valid)
                train_counts.append(len(train))
            record['tasks'][task] = {'model': model_name, 'train_rows_per_fold': train_counts,
                                    'metrics': classification_metrics(actual, predicted) if task == 'decision'
                                    else amount_metrics(actual, predicted, TOLERANCE)}
        results.append(record)
    return results


def run(root):
    import sklearn
    from sklearn.model_selection import StratifiedGroupKFold
    from threadpoolctl import threadpool_limits

    started = perf_counter()
    rows, reservation = load_rows(root)
    cohort, exclusions, review_queue = [], Counter(), []
    for row in rows:
        item, reason = qualify(row)
        if reason:
            exclusions[reason] += 1
        else:
            cohort.append(item)
        if reason or item['ceiling_reason'] not in (None, 'not_approved'):
            review_queue.append({'claim_id': row['Tracking Number'], 'decision_reason': reason,
                                 'ceiling_reason': item['ceiling_reason'] if item else None})
    counts = Counter(item['decision'] for item in cohort)
    if min(counts.get(label, 0) for label in ('approved', 'declined')) < 25:
        raise ValueError('Too few examples for five-fold decision comparison')
    splits = list(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=20261021).split(
        cohort, [item['decision'] for item in cohort], [item['group'] for item in cohort]))
    folds = []
    for train, validation in splits:
        if {cohort[i]['group'] for i in train} & {cohort[i]['group'] for i in validation}:
            raise ValueError('Property group crosses a development fold')
        folds.append({'train_ids': [cohort[i]['claim_id'] for i in train],
                      'validation_ids': [cohort[i]['claim_id'] for i in validation]})
    with threadpool_limits(limits=1):
        decision = evaluate_task(cohort, splits, 'decision')
        decision_without_limit = evaluate_task(cohort, splits, 'decision', feature_key='without_limit')
        ceiling = evaluate_task(cohort, splits, 'ceiling')
        without_limit = evaluate_task(cohort, splits, 'ceiling', feature_key='without_limit')
        anomalies = evaluate_task(cohort, splits, 'ceiling', include_anomalies=True)
        curve = learning_curve(cohort, splits)
        dated = sorted((i for i in range(len(cohort)) if cohort[i]['claim_date'] is not None),
                       key=lambda i: cohort[i]['claim_date'])
        boundary = cohort[dated[int(len(dated) * .8)]]['claim_date']
        later = [i for i in dated if cohort[i]['claim_date'] >= boundary]
        later_groups = {cohort[i]['group'] for i in later}
        earlier = [i for i in dated if cohort[i]['claim_date'] < boundary and cohort[i]['group'] not in later_groups]
        temporal_splits = [(earlier, later)]
        temporal = {'boundary_claim_date': boundary.isoformat(), 'train_count': len(earlier),
                    'validation_count': len(later), 'decision': evaluate_task(cohort, temporal_splits, 'decision'),
                    'ceiling': evaluate_task(cohort, temporal_splits, 'ceiling')}
    report = {'schema': 'retrospective-comparison-1', 'created_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'exploratory_current_sheet_reconstruction_not_prospective_accuracy',
              'sklearn_version': sklearn.__version__, 'numpy_version': np.__version__, 'threads': 1,
              'reserved_count': len(reservation['reserved_claim_ids']), 'reserved_outcomes_loaded': False,
              'source_hashes': {name: file_hash(root / name) for name in (
                  'reservation.json', 'development-source.json', 'test-context-source.json')},
              'source_read_at': json.loads((root / 'development-source.json').read_text())['read_at'],
              'data': {'development_rows': len(rows), 'label_eligible': len(cohort), 'class_counts': dict(counts),
                       'property_groups': len({item['group'] for item in cohort}), 'excluded': dict(exclusions),
                       'feature_states': dict(Counter(key for item in cohort for key in item['features'] if ':state=' in key)),
                       'limit_states_by_decision': dict(Counter(
                           f'{item["decision"]}:{"missing" if item["limit"] is None else "zero" if item["limit"] == 0 else "positive"}'
                           for item in cohort)),
                       'review_queue': review_queue,
                       'label_review': 'Schema/status/amount consistency review; no independent re-adjudication. Declined is a literal historical status; its business subtypes remain unverified.'},
              'protocol': {'folds': 5, 'seed': 20261021, 'grouping': 'normalized street/city/state; hashes excluded from features',
                           'tolerance_dollars': TOLERANCE, 'tolerance_status': 'illustrative_reporting_only',
                           'intended_use': 'Advisory suggestions; human checks before use. This is a product choice, not a measured prediction or rejection rate.',
                           'split_limitations': 'Property grouping misses aliases and cross-property tenants. Reserved property overlap is not yet audited. Development estimates only.'},
              'folds': folds, 'rules_baseline': {'decision': 'review_only_no_verified_universal_decision_rule',
                  'decision_coverage': 0, 'ceiling': rule_benchmarks(cohort, ceiling),
                  'diagnostic_limit_presence': {'kind': 'retrospective_association_not_policy_do_not_deploy',
                      'definition': 'Predict declined when the recorded limit is missing or zero, approved otherwise.',
                      'metrics': classification_metrics([item['decision'] for item in cohort],
                          ['declined' if item['limit'] in (None, 0) else 'approved' for item in cohort])}},
              'decision': decision, 'ceiling': ceiling,
              'uncertainty': {'decision': uncertainty(cohort, decision, 'decision'),
                              'ceiling': uncertainty(cohort, ceiling, 'ceiling')},
              'learning_curve': curve,
              'later_development_claims_sensitivity': temporal,
              'decision_sensitivity_without_recorded_limit': decision_without_limit,
              'sensitivity_without_recorded_limit': without_limit,
              'sensitivity_including_zero_and_above_limit_ceilings': anomalies}
    report['total_seconds'] = perf_counter() - started
    report['code_hashes'] = {name: file_hash(Path(__file__).with_name(name)) for name in (
        'retrospective.py', 'baseline.py', 'experiment.py', 'parsing.py', 'features.py')}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.root)
    with args.output.open('x') as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write('\n')


if __name__ == '__main__':
    main()

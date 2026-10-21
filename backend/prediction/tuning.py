"""Nested development tuning; no reserved claim reader or inference service."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np

from .experiment import amount_metrics, classification_metrics
from .retrospective import (DATES, TOLERANCE, _interpretation, file_hash, load_rows,
                            qualify, recorded_date)

SEED = 20261022
THRESHOLDS = (.05, .1, .15, .2, .3, .4, .5, .6, .7, .8, .9)


def calendar_features(row, base):
    values = dict(base)
    for name in DATES:
        value, _ = recorded_date(row.get(name))
        if value is not None:
            values[f'{name}:ordinal'] = float(value.toordinal())
            values[f'{name}:month_sin'] = float(np.sin(2 * np.pi * (value.month - 1) / 12))
            values[f'{name}:month_cos'] = float(np.cos(2 * np.pi * (value.month - 1) / 12))
    return values


def candidates(task):
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor, RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

    result = []
    if task == 'decision':
        for strength in (.1, 1, 10):
            for weight in (None, 'balanced'):
                result.append(LogisticRegression(C=strength, class_weight=weight, max_iter=3000, random_state=0))
        for depth in (2, 4):
            for leaf in (10, 25):
                result.append(DecisionTreeClassifier(max_depth=depth, min_samples_leaf=leaf, random_state=0))
        for depth in (1, 2):
            for rate in (.05, .1):
                result.append(GradientBoostingClassifier(n_estimators=100, learning_rate=rate,
                              max_depth=depth, min_samples_leaf=15, random_state=0))
        for leaf in (5, 15):
            result.append(RandomForestClassifier(n_estimators=150, max_depth=6, min_samples_leaf=leaf,
                          class_weight='balanced_subsample', n_jobs=1, random_state=0))
    else:
        result.extend(Ridge(alpha=value) for value in (1, 10, 100))
        for depth in (2, 4):
            for leaf in (10, 25):
                result.append(DecisionTreeRegressor(max_depth=depth, min_samples_leaf=leaf, random_state=0))
        for depth in (1, 2):
            for count in (100, 250):
                for loss in ('absolute_error', 'squared_error'):
                    result.append(GradientBoostingRegressor(loss=loss, n_estimators=count, learning_rate=.05,
                                  max_depth=depth, min_samples_leaf=15, random_state=0))
        for leaf in (5, 15):
            result.append(RandomForestRegressor(n_estimators=150, max_depth=8, min_samples_leaf=leaf,
                          n_jobs=1, random_state=0))
    return result


def pipeline_for(estimator):
    from sklearn.base import clone
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(DictVectorizer(sparse=False), StandardScaler(), clone(estimator))


def inner_splits(items, task):
    from sklearn.model_selection import GroupKFold, StratifiedGroupKFold

    groups = [item['group'] for item in items]
    if task == 'decision':
        splitter = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=SEED)
        splits = list(splitter.split(items, [item['decision'] for item in items], groups))
    else:
        splits = list(GroupKFold(n_splits=3, shuffle=True, random_state=SEED).split(items, groups=groups))
    for train, valid in splits:
        if {groups[i] for i in train} & {groups[i] for i in valid}:
            raise ValueError('Inner property groups overlap')
        if task == 'decision' and len({items[i]['decision'] for i in train}) != 2:
            raise ValueError('Inner training fold lacks a class')
    return splits


def threshold_for(actual, probabilities):
    actual = np.asarray(actual)
    if set(actual) != {'approved', 'declined'}:
        raise ValueError('Threshold tuning requires both decisions')
    scores = []
    for threshold in THRESHOLDS:
        predicted = np.where(np.asarray(probabilities) >= threshold, 'declined', 'approved')
        correct_declines = np.mean(predicted[actual == 'declined'] == 'declined')
        correct_approvals = np.mean(predicted[actual == 'approved'] == 'approved')
        scores.append((float((correct_declines + correct_approvals) / 2), -abs(threshold - .5), threshold))
    score, _, threshold = max(scores)
    return threshold, score


def search(items, task, mode, *, estimators=None):
    started = perf_counter()
    splits = inner_splits(items, task)
    actual = np.asarray([item[task] for item in items])
    summaries, best = [], None
    pool = candidates(task) if estimators is None else estimators
    for position, estimator in enumerate(pool):
        predicted = np.full(len(items), np.nan)
        for train, valid in splits:
            pipeline = pipeline_for(estimator)
            pipeline.fit([items[i][mode] for i in train], actual[train])
            if task == 'decision':
                decline_column = list(pipeline.classes_).index('declined')
                predicted[valid] = pipeline.predict_proba([items[i][mode] for i in valid])[:, decline_column]
            else:
                predicted[valid] = pipeline.predict([items[i][mode] for i in valid])
        if not np.isfinite(predicted).all():
            raise ValueError('Incomplete or invalid inner predictions')
        if task == 'decision':
            threshold, score = threshold_for(actual, predicted)
        else:
            threshold, score = None, -float(np.mean(np.abs(actual.astype(float) - predicted)))
        summary = {'candidate_index': position, 'estimator': type(estimator).__name__,
                   'parameters': estimator.get_params(), 'inner_selection_score': score,
                   'decline_threshold': threshold}
        summaries.append(summary)
        if best is None or score > best['inner_selection_score']:
            best = summary
    return {'selected': best, 'candidates': summaries, 'search_seconds': perf_counter() - started}


def metrics(actual, predicted, task):
    from sklearn.metrics import precision_score

    if task == 'ceiling':
        return amount_metrics(actual, predicted, TOLERANCE)
    result = classification_metrics(actual, predicted)
    result['decline_precision'] = precision_score(actual, predicted, pos_label='declined', zero_division=0)
    return result


def evaluate(cohort, splits, task, mode, *, estimators=None):
    pool = candidates(task) if estimators is None else estimators
    outputs, folds = [], []
    fit_seconds, predict_seconds = 0, 0
    for number, (train, valid) in enumerate(splits):
        train = [i for i in train if task == 'decision' or cohort[i]['ceiling_reason'] is None]
        valid = [i for i in valid if task == 'decision' or cohort[i]['ceiling_reason'] is None]
        train_items, valid_items = [cohort[i] for i in train], [cohort[i] for i in valid]
        if {item['group'] for item in train_items} & {item['group'] for item in valid_items}:
            raise ValueError('Outer property groups overlap')
        selection = search(train_items, task, mode, estimators=pool)
        chosen = selection['selected']
        pipeline = pipeline_for(pool[chosen['candidate_index']])
        started = perf_counter()
        pipeline.fit([item[mode] for item in train_items], [item[task] for item in train_items])
        fit_seconds += perf_counter() - started
        started = perf_counter()
        predicted = pipeline.predict([item[mode] for item in valid_items])
        default = predicted.copy()
        probabilities = None
        if task == 'decision':
            column = list(pipeline.classes_).index('declined')
            probabilities = pipeline.predict_proba([item[mode] for item in valid_items])[:, column]
            predicted = np.where(probabilities >= chosen['decline_threshold'], 'declined', 'approved')
        predict_seconds += perf_counter() - started
        actual = [item[task] for item in valid_items]
        folds.append({'fold': number, 'train_ids': [item['claim_id'] for item in train_items],
                      'validation_ids': [item['claim_id'] for item in valid_items],
                      'selection': selection, 'metrics': metrics(actual, predicted, task),
                      'interpretation': _interpretation(pipeline)})
        for index, item in enumerate(valid_items):
            output = {'claim_id': item['claim_id'], 'actual': item[task], 'prediction': predicted[index],
                      'default_threshold_prediction': default[index]}
            if probabilities is not None:
                output['decline_probability_uncalibrated'] = float(probabilities[index])
            outputs.append(output)
    if len({item['claim_id'] for item in outputs}) != len(outputs):
        raise ValueError('Repeated validation claim')
    result = {'folds': folds, 'out_of_fold': outputs,
              'metrics': metrics([item['actual'] for item in outputs], [item['prediction'] for item in outputs], task),
              'prediction_count': len(outputs), 'fit_seconds_selected_models': fit_seconds,
              'predict_seconds_selected_models': predict_seconds,
              'search_seconds': sum(fold['selection']['search_seconds'] for fold in folds)}
    if task == 'decision':
        result['default_threshold_metrics'] = metrics([item['actual'] for item in outputs],
                                                      [item['default_threshold_prediction'] for item in outputs], task)
    return result


def dataset(root):
    previous = json.loads((root / 'evaluation.json').read_text())
    for name, expected in previous['source_hashes'].items():
        if file_hash(root / name) != expected:
            raise ValueError('Frozen development source changed')
    rows, reservation = load_rows(root)
    cohort = []
    for row in rows:
        item, reason = qualify(row)
        if reason is None:
            item['durations'] = item['features']
            item['calendar'] = calendar_features(row, item['features'])
            cohort.append(item)
    indexes = {item['claim_id']: i for i, item in enumerate(cohort)}
    splits = []
    for fold in previous['folds']:
        if set(fold['train_ids']) | set(fold['validation_ids']) != set(indexes):
            raise ValueError('Previous development cohort changed')
        if set(fold['train_ids']) & set(fold['validation_ids']):
            raise ValueError('Development split overlaps')
        splits.append(([indexes[i] for i in fold['train_ids']], [indexes[i] for i in fold['validation_ids']]))
    return cohort, splits, previous, reservation


def protocol(root):
    _, _, previous, reservation = dataset(root)
    return {'schema': 'nested-tuning-protocol-1', 'seed': SEED, 'outer_folds': 5, 'inner_folds': 3,
            'feature_modes': ['durations', 'calendar'], 'thresholds': list(THRESHOLDS),
            'decision_objective': 'inner balanced accuracy; ties prefer threshold nearest 0.5, then earlier candidate',
            'ceiling_objective': 'inner raw dollar MAE on primary approved ceiling labels',
            'prior_evaluation_sha256': file_hash(root / 'evaluation.json'),
            'source_hashes': previous['source_hashes'], 'reserved_count': len(reservation['reserved_claim_ids']),
            'code_sha256': file_hash(Path(__file__)),
            'candidates': {task: [{'estimator': type(model).__name__, 'parameters': model.get_params()}
                                  for model in candidates(task)] for task in ('decision', 'ceiling')},
            'scope': 'Development tuning only. No inference integration or reserved outcome reads.',
            'human_review': 'Advisory use is a product choice, not a measured rejection or prediction coverage rate.'}


def run(root, frozen):
    import sklearn
    from threadpoolctl import threadpool_limits

    if frozen != protocol(root):
        raise ValueError('Frozen tuning protocol or inputs changed')
    started = perf_counter()
    cohort, splits, previous, reservation = dataset(root)
    temporal = previous['later_development_claims_sensitivity']
    later_ids = {item['claim_id'] for item in temporal['decision']['out_of_fold']['majority']}
    boundary = datetime.fromisoformat(temporal['boundary_claim_date']).date()
    later = [i for i, item in enumerate(cohort) if item['claim_id'] in later_ids]
    groups = {cohort[i]['group'] for i in later}
    train = [i for i, item in enumerate(cohort) if item['claim_date'] is not None and
             item['claim_date'] < boundary and item['group'] not in groups]
    results = {}
    with threadpool_limits(limits=1):
        for task in ('decision', 'ceiling'):
            results[task] = {}
            for mode in ('durations', 'calendar'):
                print(f'Comparing {task}, {mode}: nested development folds', flush=True)
                nested = evaluate(cohort, splits, task, mode)
                print(f'Checking {task}, {mode}: later-date development claims', flush=True)
                chronological = evaluate(cohort, [(train, later)], task, mode)
                eligible = [item for item in cohort if task == 'decision' or item['ceiling_reason'] is None]
                selection = search(eligible, task, mode)
                results[task][mode] = {'nested': nested, 'later_date': chronological,
                                       'full_development_selection': selection}
    return {'schema': 'nested-tuning-results-1', 'created_at': datetime.now(timezone.utc).isoformat(),
            'protocol': frozen, 'sklearn_version': sklearn.__version__, 'numpy_version': np.__version__,
            'results': results, 'reserved_outcomes_loaded': False,
            'prediction_coverage': {'decision': len(cohort) / previous['data']['development_rows'],
                                    'ceiling_conditional_on_known_approval': sum(item['ceiling_reason'] is None for item in cohort) /
                                    sum(item['decision'] == 'approved' for item in cohort)},
            'limitations': 'Exploratory current-sheet labels and source timing; earlier development results were already seen. Thresholds optimize a research metric, not a business cost or automation policy.',
            'total_seconds': perf_counter() - started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output is None:
        with args.protocol.open('x') as output:
            json.dump(protocol(args.root), output, indent=2, allow_nan=False)
    else:
        report = run(args.root, json.loads(args.protocol.read_text()))
        with args.output.open('x') as output:
            json.dump(report, output, indent=2, allow_nan=False)


if __name__ == '__main__':
    main()

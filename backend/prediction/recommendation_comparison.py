"""Paired development comparison of spreadsheet and cited document features."""

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .experiment import amount_metrics, canonical_id, classification_metrics
from .recommendations import INPUT_COLUMNS, ResearchInput, code_hashes, fit_models, recommend
from .research_documents import ResearchFact, document_features
from .retrospective import TOLERANCE, estimators, file_hash, load_rows
from .snapshots import digest
from .tuning import dataset

ARMS = ('spreadsheet', 'documents')


def load_evidence(path, development_ids, reserved_ids):
    evidence = json.loads(path.read_text())
    if set(evidence) != {'sources', 'facts', 'excluded_claim_ids'}:
        raise ValueError('Unexpected evidence fields')
    sources, facts = {}, {}
    excluded = {canonical_id(v) for v in evidence['excluded_claim_ids']}
    for source in evidence['sources']:
        claim = canonical_id(source['claim_id'])
        if claim not in development_ids or claim in reserved_ids or claim == '53' or claim in excluded:
            raise ValueError('Evidence is outside included development claims')
        if source['source_id'] in sources or source['role'] not in {'lease', 'ledger', 'waiver'}:
            raise ValueError('Duplicate or unsupported source')
        if file_hash(Path(source['path'])) != source['sha256']:
            raise ValueError('Source bytes changed')
        sources[source['source_id']] = source
    for candidate in evidence['facts']:
        fact = ResearchFact.model_validate(candidate)
        claim = canonical_id(fact.claim_id)
        if claim not in development_ids or claim in reserved_ids or claim in excluded:
            raise ValueError('Fact is outside included development claims')
        for citation in fact.citations:
            source = sources.get(citation.document_id)
            if not source or (canonical_id(source['claim_id']), source['sha256'], source['role']) != (
                    claim, citation.sha256, fact.source_role):
                raise ValueError('Citation source identity mismatch')
        facts.setdefault(claim, []).append(fact)
    return facts, evidence


def summary(records):
    actual = [r['actual_decision'] for r in records]
    result = {'count': len(records), 'decision': {}, 'amount': {}}
    if not records:
        return result
    for name in (*ARMS, 'majority'):
        predicted = [r[name]['research_decision'] if name in ARMS else r['majority'] for r in records]
        metrics = classification_metrics(actual, predicted) if len(set(actual)) == 2 else {
            'accuracy': sum(a == p for a, p in zip(actual, predicted)) / len(actual),
            'balanced_accuracy': None,
            'decline_recall': (sum(a == p == 'declined' for a, p in zip(actual, predicted)) /
                               actual.count('declined')) if 'declined' in actual else None,
            'confusion_labels': ['declined', 'approved'],
            'confusion_matrix': [[sum(a == label and p == guess for a, p in zip(actual, predicted))
                                  for guess in ('declined', 'approved')] for label in ('declined', 'approved')],
        }
        result['decision'][name] = {
            **metrics,
            'historical_declines_recovered': sum(a == p == 'declined' for a, p in zip(actual, predicted)),
            'historical_approvals_flagged_declined': sum(a == 'approved' and p == 'declined' for a, p in zip(actual, predicted)),
        }
    matched = [r for r in records if r['actual_ceiling'] is not None and all(
        r[arm]['conditional_ceiling'] is not None for arm in ARMS)]
    for name in (*ARMS, 'median', 'limit', 'minimum_request_limit'):
        subset = matched if name in (*ARMS, 'median') else [r for r in matched if r[name] is not None]
        result['amount'][name] = {'count': len(subset), 'metrics': amount_metrics(
            [r['actual_ceiling'] for r in subset],
            [r[name]['conditional_ceiling'] if name in ARMS else r[name] for r in subset], TOLERANCE
        ) if subset else None}
        if name not in (*ARMS, 'median'):
            result['amount'][name]['matched_models'] = {arm: amount_metrics(
                [r['actual_ceiling'] for r in subset], [r[arm]['conditional_ceiling'] for r in subset], TOLERANCE
            ) for arm in ARMS} if subset else {}
    result['coverage'] = {arm: {
        'advisory_amounts': sum(r[arm]['conditional_ceiling'] is not None for r in records),
        'withheld_amounts': sum(r[arm]['conditional_ceiling'] is None for r in records),
        'requires_review': sum(r[arm]['requires_review'] for r in records),
        'approved_predictions_with_scoreable_amount': sum(
            r[arm]['research_decision'] == 'approved' for r in matched),
    } for arm in ARMS}
    result['amount_on_historical_and_predicted_approvals'] = {}
    for arm in ARMS:
        subset = [r for r in matched if r[arm]['research_decision'] == 'approved']
        result['amount_on_historical_and_predicted_approvals'][arm] = {
            'count': len(subset), 'metrics': amount_metrics(
                [r['actual_ceiling'] for r in subset], [r[arm]['conditional_ceiling'] for r in subset], TOLERANCE
            ) if subset else None}
    result['changed'] = {
        'decision': sum(r['spreadsheet']['research_decision'] != r['documents']['research_decision'] for r in records),
        'amount': sum(r['spreadsheet']['conditional_ceiling'] != r['documents']['conditional_ceiling'] for r in records),
    }
    return result


def compare(cohort, splits, rows, facts):
    enriched = [{**item, 'features': document_features(item['features'], item['claim_id'], facts.get(item['claim_id'], ()))
                } for item in cohort]
    by_id = {canonical_id(str(row['Tracking Number'])): row for row in rows}
    records, folds, seen = [], [], set()
    for fold, (train, valid) in enumerate(splits):
        train_ids = {cohort[i]['claim_id'] for i in train}
        valid_ids = {cohort[i]['claim_id'] for i in valid}
        if train_ids & valid_ids or seen & valid_ids or {
                cohort[i]['group'] for i in train} & {cohort[i]['group'] for i in valid}:
            raise ValueError('Fold overlap')
        seen.update(valid_ids)
        models = {arm: fit_models(items, train) for arm, items in zip(ARMS, (cohort, enriched))}
        folds.append({'fold': fold, 'train_ids': sorted(train_ids), 'validation_ids': sorted(valid_ids),
                      'model_hashes': {arm: digest(model) for arm, model in models.items()}})
        for i in valid:
            item = cohort[i]
            row = by_id[item['claim_id']]
            request = ResearchInput(claim_id=item['claim_id'], values={key: row.get(key) for key in INPUT_COLUMNS},
                                    source_reference=f'frozen development row; claim:{item["claim_id"]}')
            outputs = {arm: recommend(models[arm], request, document_facts=(
                facts.get(item['claim_id'], ()) if arm == 'documents' else None)) for arm in ARMS}
            benchmarks = outputs['spreadsheet']['benchmarks']
            records.append({'claim_id': item['claim_id'], 'fold': fold,
                'actual_decision': item['decision'],
                'actual_ceiling': item['ceiling'] if item['ceiling_reason'] is None else None,
                'document_processed': item['claim_id'] in facts,
                'present_document_facts': sum(f.state == 'present' for f in facts.get(item['claim_id'], ())),
                'majority': benchmarks['majority_decision'], 'median': benchmarks['training_median_ceiling'],
                'limit': benchmarks['recorded_limit'], 'minimum_request_limit': benchmarks['minimum_request_limit'],
                **outputs})
    if seen != {item['claim_id'] for item in cohort}:
        raise ValueError('Incomplete validation coverage')
    return {'overall': summary(records),
            'document_processed_subset': summary([r for r in records if r['document_processed']]),
            'document_present_subset': summary([r for r in records if r['present_document_facts']]),
            'folds': folds, 'out_of_fold': records}


def protocol(root, evidence_path, previous, reservation):
    return {'version': 'recommendation-comparison-1', 'scope': 'current_record_development_historical_agreement',
        'source_hashes': previous['source_hashes'], 'reservation_sha256': file_hash(root / 'reservation.json'),
        'folds_sha256': file_hash(root / 'evaluation.json'), 'evidence_sha256': file_hash(evidence_path),
        'code_hashes': {**code_hashes(), 'recommendation_comparison.py': file_hash(Path(__file__))},
        'reserved_count': len(reservation['reserved_claim_ids']),
        'parameters': {task: estimators(task)[name].get_params() for task, name in (
            ('decision', 'shallow_tree'), ('ceiling', 'boosted_tree'))},
        'selection': 'Retain spreadsheet model unless documents improve balanced accuracy and amount MAE without increasing false decline flags, with at least 50 processed validation claims.',
        'limitations': ['Previously used development data; no fresh final-test claim.',
            'Source sample selected for extraction studies, not representative outcome evaluation.',
            'Current-record sources have unverified decision-time availability.',
            'Historical agreement is scoreable; insurance correctness remains unscored.']}


def selected_arm(result):
    overall, subset = result['overall'], result['document_processed_subset']
    baseline, enriched = (overall['decision'][arm] for arm in ARMS)
    base_amount, enriched_amount = (overall['amount'][arm]['metrics'] for arm in ARMS)
    if (subset['count'] >= 50 and base_amount and enriched_amount and
            enriched['balanced_accuracy'] > baseline['balanced_accuracy'] and
            enriched['historical_approvals_flagged_declined'] <= baseline['historical_approvals_flagged_declined'] and
            enriched_amount['mae_dollars'] < base_amount['mae_dollars']):
        return 'documents'
    return 'spreadsheet'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    cohort, splits, previous, reservation = dataset(args.root)
    rows, _ = load_rows(args.root)
    facts, evidence = load_evidence(args.evidence,
        {canonical_id(v['claim_id']) for v in reservation['development_claims']},
        {canonical_id(v) for v in reservation['reserved_claim_ids']})
    frozen = protocol(args.root, args.evidence, previous, reservation)
    if args.output is None:
        with args.protocol.open('x') as output:
            json.dump(frozen, output, indent=2, allow_nan=False)
        return
    if json.loads(args.protocol.read_text()) != frozen:
        raise ValueError('Frozen protocol changed')
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1):
        result = compare(cohort, splits, rows, facts)
        selected = selected_arm(result)
        training = cohort if selected == 'spreadsheet' else [
            {**item, 'features': document_features(item['features'], item['claim_id'], facts.get(item['claim_id'], ()))}
            for item in cohort]
        models = fit_models(training, list(range(len(training))))
    result.update({'protocol': frozen, 'protocol_sha256': file_hash(args.protocol),
        'created_at': datetime.now(timezone.utc).isoformat(), 'reserved_outcomes_loaded': False,
        'selected_arm': selected, 'selected_model': models.model_dump(mode='json'),
        'completion': 'recommendation comparison and evaluation are complete',
        'evidence_coverage': {'processed_claims': len(facts), 'sources': len(evidence['sources']),
            'states': dict(Counter(f.state for values in facts.values() for f in values)),
            'qualified_processed_claims': result['document_processed_subset']['count'],
            'unqualified_processed_claims': sorted(set(facts) - {item['claim_id'] for item in cohort}),
            'excluded_claim_ids': evidence['excluded_claim_ids']}})
    with args.output.open('x') as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write('\n')


if __name__ == '__main__':
    main()

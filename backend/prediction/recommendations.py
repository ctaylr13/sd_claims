"""Current-record research recommendations; no adjudication or reserved-data reader."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .artifacts import ResearchModels, predict
from .features import DATES, MONEY, row_features
from .models import FrozenModel
from .parsing import canonical_id, recorded_date, recorded_money
from .research_documents import document_features

VERSION = 'current-record-recommendations-3'
INPUT_COLUMNS = frozenset((*DATES, *MONEY, 'Termination Type'))


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
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in (
        'recommendations.py', 'recommendations_research.py', 'artifacts.py', 'retrospective.py', 'tuning.py',
        'experiment.py', 'baseline.py', 'research_documents.py', 'parsing.py', 'features.py')}


def recommend(models, request, *, document_facts=None):
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
    if document_facts is not None:
        feature_values = document_features(feature_values, request.claim_id, document_facts)
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
            from .recommendations_research import evaluate, train

            result = train(args.root).model_dump(mode='json') if args.command == 'train' else evaluate(args.root)
    with args.output.open('x') as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write('\n')


if __name__ == '__main__':
    main()

"""Cited current-record features -- no historical evidence approvals."""

from datetime import date
from math import isfinite
from typing import Literal

from pydantic import Field, model_validator

from .experiment import canonical_id
from .models import FrozenModel

KEY_ROLES = {
    'program_election': 'waiver', 'program_monthly_fee': 'waiver',
    'waived_deposit': 'waiver', 'lease_start_date': 'lease',
    'lease_end_date': 'lease', 'monthly_rent': 'lease',
    'balance': 'ledger', 'balance_as_of': 'ledger', 'deposit_held': 'ledger',
}
DATE_KEYS = frozenset({'lease_start_date', 'lease_end_date', 'balance_as_of'})


class ResearchCitation(FrozenModel):
    document_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    page: int = Field(ge=1)
    quote: str = Field(min_length=1)
    bbox: tuple[float, float, float, float]
    method: Literal['native_text']
    coordinate_system: Literal['points_top_left'] = 'points_top_left'

    @model_validator(mode='after')
    def coordinates(self):
        x0, y0, x1, y1 = self.bbox
        if not all(isfinite(v) for v in self.bbox) or min(x0, y0) < 0 or x1 <= x0 or y1 <= y0:
            raise ValueError('Invalid citation coordinates')
        return self


class ResearchFact(FrozenModel):
    claim_id: str = Field(min_length=1)
    key: str
    source_role: Literal['lease', 'ledger', 'waiver']
    state: Literal['present', 'unreviewed', 'needs_review', 'missing', 'conflicting', 'unreadable']
    value: str | None = None
    citations: tuple[ResearchCitation, ...] = ()

    @model_validator(mode='after')
    def allowed_value(self):
        if canonical_id(self.claim_id) == '53':
            raise ValueError('Candidate 53 is excluded')
        if KEY_ROLES.get(self.key) != self.source_role:
            raise ValueError('Unsupported fact or source role')
        if self.state != 'present':
            if self.value is not None:
                raise ValueError('Unknown facts must have null values')
        elif self.value is None or not self.citations:
            raise ValueError('Present facts require a value and citations')
        elif self.key in DATE_KEYS:
            date.fromisoformat(self.value)
        elif self.key == 'program_election':
            if self.value not in {'yes', 'no'}:
                raise ValueError('Invalid program election')
        else:
            value = float(self.value)
            if not isfinite(value) or (self.key != 'balance' and value < 0):
                raise ValueError('Invalid monetary fact')
        return self


def document_features(base, claim_id, facts):
    by_key = {}
    for candidate in facts:
        fact = ResearchFact.model_validate(candidate)
        if canonical_id(fact.claim_id) != canonical_id(claim_id):
            raise ValueError('Fact claim mismatch')
        by_key.setdefault(fact.key, []).append(fact)
    result, dates = dict(base), {}
    for key in KEY_ROLES:
        candidates = by_key.get(key, [])
        state = candidates[0].state if len(candidates) == 1 else 'conflicting' if candidates else 'unreviewed'
        result[f'document.{key}_state={state}'] = 1
        if state != 'present':
            continue
        value = candidates[0].value
        if key in DATE_KEYS:
            dates[key] = date.fromisoformat(value)
        elif key == 'program_election':
            result[f'document.{key}={value}'] = 1
        else:
            result[f'document.{key}'] = float(value)
    if 'lease_start_date' in dates and 'lease_end_date' in dates:
        result['document.lease_days'] = (dates['lease_end_date'] - dates['lease_start_date']).days
    return result

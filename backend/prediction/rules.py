"""Opt-in source review rules; no loaders, approvals, or claim decisions."""

from typing import Literal

from pydantic import Field, model_validator

from .models import FrozenModel, UnknownState


class RuleCitation(FrozenModel):
    source_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    page: int = Field(ge=1)
    quote: str = Field(min_length=1)


class ReviewedFact(FrozenModel):
    claim_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    state: Literal['present'] | UnknownState
    value: str | None = Field(default=None, min_length=1)
    reviewed_by: str | None = Field(default=None, min_length=1)
    citations: tuple[RuleCitation, ...] = ()

    @model_validator(mode='after')
    def explicit_value(self):
        if (self.state == 'present') != (self.value is not None):
            raise ValueError('Only present facts carry values')
        return self


class ReviewRule(FrozenModel):
    rule_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    candidate_reason: str = Field(min_length=1)
    required_facts: tuple[str, ...] = Field(min_length=1)
    matches: dict[str, str] = Field(default_factory=dict)
    terms: tuple[RuleCitation, ...] = ()
    reviewed_by: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def complete_requirements(self):
        if len(set(self.required_facts)) != len(self.required_facts):
            raise ValueError('Duplicate required facts')
        if not set(self.matches) <= set(self.required_facts):
            raise ValueError('Every condition requires a documented fact')
        return self


def _identity(value):
    value = value.strip()
    if not value:
        raise ValueError('Empty claim identity')
    return str(int(value)) if value.isdecimal() else value


def evaluate_rule(rule: ReviewRule, *, claim_id: str, reserved_ids: tuple[str, ...],
                  applicability: ReviewedFact, facts: tuple[ReviewedFact, ...]):
    identity = _identity(claim_id)
    if not reserved_ids or identity in {_identity(item) for item in reserved_ids} | {'53'}:
        raise ValueError('Explicit reserved inventory required; reserved/robustness claims excluded')
    rule = ReviewRule.model_validate(rule.model_dump())
    facts = tuple(ReviewedFact.model_validate(fact.model_dump()) for fact in facts)
    applicability = ReviewedFact.model_validate(applicability.model_dump())
    if any(_identity(fact.claim_id) != identity for fact in (*facts, applicability)):
        raise ValueError('Evidence belongs to a different claim')
    issues, citations = [], []
    if not rule.reviewed_by or not rule.terms or not rule.matches:
        issues.append('governing_rule_unverified')
    citations.extend(rule.terms)
    grouped = {}
    for fact in facts:
        grouped.setdefault(fact.key, []).append(fact)
    for key in rule.required_facts:
        values = grouped.get(key, [])
        for fact in values:
            citations.extend(fact.citations)
        if len(values) != 1:
            issues.append(f'{key}:duplicate' if values else f'{key}:missing')
        else:
            fact = values[0]
            if fact.state != 'present':
                issues.append(f'{key}:{fact.state}')
            elif not fact.reviewed_by or not fact.citations:
                issues.append(f'{key}:source_review_required')
    citations.extend(applicability.citations)
    if (applicability.key != f'{rule.rule_id}:applicable' or applicability.state != 'present'
            or not applicability.reviewed_by or not applicability.citations
            or applicability.value not in ('yes', 'no')):
        issues.append('claim_specific_applicability_unverified')
    status = 'needs_review'
    if not issues:
        if applicability.value == 'no':
            status = 'not_applicable'
        elif all(grouped[key][0].value == value for key, value in rule.matches.items()):
            status = 'candidate_reason'
        else:
            status = 'reason_not_supported'
    return {'version': 'source-review-rules-1', 'claim_id': identity,
            'rule_id': rule.rule_id, 'rule_version': rule.version, 'status': status,
            'candidate_reason': rule.candidate_reason if status == 'candidate_reason' else None,
            'review_reasons': issues, 'citations': [cite.model_dump() for cite in citations],
            'decision': 'needs_review', 'approved_ceiling': None,
            'prediction_eligible': False, 'training_eligible': False}

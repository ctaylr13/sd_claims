from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

FACT_KEYS = frozenset({
    'claim_date', 'lease_start_date', 'lease_end_date', 'move_out_date',
    'monthly_rent', 'requested_amount', 'policy_limit', 'termination_type',
    'confirmed_possession_end', 'key_return', 'notice_date', 'rent_paid_through',
    'deposit_held', 'balance', 'total_charges', 'total_credits', 'total_deposits',
})
SOURCE_KINDS = frozenset({'claim_intake', 'lease', 'renewal', 'ledger',
                          'move_out_statement', 'policy', 'possession_evidence'})
UnknownState = Literal['missing', 'conflicting', 'unreadable', 'unreviewed',
                       'failed', 'not_applicable', 'availability_unverified', 'excluded']


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')


class Availability(FrozenModel):
    state: Literal['verified', 'unverified'] = 'unverified'
    received_at: AwareDatetime | None = None
    proof_kind: Literal['intake_receipt', 'submission_email', 'upload_audit'] | None = None
    proof_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def verified_receipt(self):
        if self.state == 'verified' and not all((self.received_at, self.proof_kind, self.proof_sha256)):
            raise ValueError('Verified availability requires a timestamp and exact-version receipt evidence')
        return self


class Section(FrozenModel):
    section_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    content: Literal['neutral', 'answer_revealing', 'unreviewed'] = 'unreviewed'
    availability: Availability = Field(default_factory=Availability)


class Source(FrozenModel):
    source_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    kind: str
    availability: Availability = Field(default_factory=Availability)
    sections: tuple[Section, ...] = ()

    @model_validator(mode='after')
    def unique_sections(self):
        if len({section.section_id for section in self.sections}) != len(self.sections):
            raise ValueError('Duplicate section IDs')
        return self


class Reference(FrozenModel):
    source_id: str
    version: str
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    section_id: str
    section_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class CandidateFact(FrozenModel):
    fact_id: str = Field(min_length=1)
    key: str
    state: Literal['present'] | UnknownState
    value: str | None = None
    references: tuple[Reference, ...] = ()

    @model_validator(mode='after')
    def explicit_value(self):
        if self.state == 'present' and (not self.value or not self.references):
            raise ValueError('Present facts require a value and source lineage')
        if self.state != 'present' and self.value is not None:
            raise ValueError('Unknown facts cannot carry a value')
        return self


class FactApproval(FrozenModel):
    fact_id: str
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ApprovalSnapshot(FrozenModel):
    schema_version: Literal['prediction-inputs-1'] = 'prediction-inputs-1'
    claim_id: str = Field(min_length=1)
    purpose: Literal['historical_prediction'] = 'historical_prediction'
    cutoff: AwareDatetime | None = None
    cutoff_evidence_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    approved_by: str = Field(min_length=1)
    approved_at: AwareDatetime
    sources: tuple[Source, ...] = ()
    facts: tuple[FactApproval, ...] = ()

    @field_validator('claim_id')
    @classmethod
    def real_claim(cls, value):
        value = value.strip()
        if not value or (value.isdecimal() and int(value) == 53):
            raise ValueError('Candidate 53 is reserved for robustness fixtures')
        return value

    @model_validator(mode='after')
    def unique_approvals(self):
        if self.cutoff is not None and self.cutoff_evidence_sha256 is None:
            raise ValueError('A cutoff requires decision-event evidence')
        if len({s.source_id for s in self.sources}) != len(self.sources):
            raise ValueError('Duplicate source IDs')
        if len({f.fact_id for f in self.facts}) != len(self.facts):
            raise ValueError('Duplicate fact approvals')
        return self


class InputFact(FrozenModel):
    key: str
    state: Literal['present'] | UnknownState
    value: str | None = None
    references: tuple[Reference, ...] = ()


class Rejection(FrozenModel):
    candidate_index: int
    reason: Literal['fact_not_allowed', 'fact_not_approved', 'cutoff_unknown',
                    'source_not_allowed', 'section_not_allowed', 'availability_unverified',
                    'post_cutoff', 'version_mismatch', 'duplicate_fact']


class PredictionBundle(FrozenModel):
    purpose: Literal['historical_prediction'] = 'historical_prediction'
    claim_id: str
    snapshot_id: str
    cutoff: datetime | None
    facts: tuple[InputFact, ...]
    rejections: tuple[Rejection, ...]
    eligible: bool

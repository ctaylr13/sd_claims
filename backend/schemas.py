import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class RecordedInput(BaseModel):
    recorded_value: str
    source_column: str
    availability: Literal['decision_time_unverified']


class HistoricalOutcome(BaseModel):
    status: str = Field(alias='Status')
    approved_benefit_amount: str = Field(alias='Approved Benefit Amount')
    approval_date: str = Field(alias='Approval Date')
    posted_date: str = Field(alias='Posted Date')


class Evidence(BaseModel):
    folder: dict[str, Any] | None
    items: list[dict[str, Any]]
    state: Literal['present', 'missing']


class ClaimResponse(BaseModel):
    tracking_number: str
    claims_source: dict[str, Any]
    prediction_inputs: dict[str, RecordedInput]
    historical_outcome: HistoricalOutcome
    evidence: Evidence
    training_eligible: Literal[False]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class BatchLookupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tracking_numbers: list[StrictStr] = Field(min_length=1, max_length=100)

    @field_validator('tracking_numbers', mode='before', json_schema_input_type=str | list[StrictStr])
    @classmethod
    def split_numbers(cls, value):
        if isinstance(value, str):
            return [number.strip() for number in re.split(r'[,\n]', value) if number.strip()]
        return value

    @field_validator('tracking_numbers')
    @classmethod
    def normalize_numbers(cls, value):
        return list(dict.fromkeys(number.strip() for number in value))


class FoundClaim(BaseModel):
    tracking_number: str
    state: Literal['found']
    http_status: Literal[200]
    claim: ClaimResponse


class FailedClaim(BaseModel):
    tracking_number: str
    state: Literal['error']
    http_status: int
    error: ErrorDetail


class BatchLookupResponse(BaseModel):
    results: list[FoundClaim | FailedClaim]
    total: int
    found: int
    failed: int


class DecisionMetrics(BaseModel):
    accuracy: float | None = Field(ge=0, le=1)
    balanced_accuracy: float | None = Field(ge=0, le=1)
    decline_recall: float | None = Field(ge=0, le=1)
    confusion_matrix: list[list[int]]

    @field_validator('confusion_matrix')
    @classmethod
    def two_classes(cls, value):
        if len(value) != 2 or any(len(row) != 2 or any(count < 0 for count in row) for row in value):
            raise ValueError('Expected declined/approved confusion matrix')
        return value


class DecisionComparison(BaseModel):
    research_decision: DecisionMetrics
    majority_decision: DecisionMetrics


class CeilingMetrics(BaseModel):
    mae_dollars: float = Field(ge=0, allow_inf_nan=False)
    within_tolerance_rate: float = Field(ge=0, le=1)


class ScoredCeiling(BaseModel):
    count: int = Field(ge=0)
    metrics: CeilingMetrics | None


class EvaluationCoverage(BaseModel):
    development_rows: int = Field(ge=0)
    qualified_outcomes: int = Field(ge=0)
    conditional_estimates: int = Field(ge=0)
    scoreable_approved_advisories: int = Field(ge=0)
    withheld: int = Field(ge=0)


class IndependentReview(BaseModel):
    status: str
    reviewed_claim_count: int = Field(ge=0)
    required: str


class EvaluationMetrics(BaseModel):
    version: str
    created_at: str
    scope: Literal['development_only_historical_agreement']
    method: str
    decision: DecisionComparison
    ceiling_on_same_approved_advisory_subset: dict[str, ScoredCeiling]
    coverage: EvaluationCoverage
    independent_correctness: IndependentReview

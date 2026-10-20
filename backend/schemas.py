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

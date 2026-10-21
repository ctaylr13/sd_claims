from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import ApprovalSnapshot, CandidateFact, FrozenModel, PredictionBundle
from .builder import build_prediction_bundle


class AccessPurpose(Enum):
    PREDICTION = 'prediction'
    EVALUATION = 'evaluation'
    ROBUSTNESS = 'robustness'


def require_evaluation(access):
    if access is not AccessPurpose.EVALUATION:
        raise PermissionError('Answer keys require evaluation-only access')


class AnswerKey(FrozenModel):
    claim_id: str
    decision: Literal['approved', 'declined', 'unknown']
    ceiling_state: Literal['present', 'unknown', 'not_applicable']
    approved_ceiling: str | None = Field(default=None, pattern=r'^\d+\.\d{2}$')
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    reviewed_by: str = Field(min_length=1)

    @model_validator(mode='after')
    def reviewed_answer(self):
        claim_id = self.claim_id.strip()
        if not claim_id or (claim_id.isdecimal() and int(claim_id) == 53):
            raise ValueError('Candidate 53 cannot enter historical evaluation')
        if (self.ceiling_state == 'present') != (self.approved_ceiling is not None):
            raise ValueError('Ceiling value must match its explicit state')
        if self.decision == 'unknown' and self.ceiling_state == 'present':
            raise ValueError('Unknown decisions cannot supply a ceiling label')
        return self


class EvaluationInputs(FrozenModel):
    purpose: Literal['evaluation_only'] = 'evaluation_only'
    prediction: PredictionBundle
    answer_key: AnswerKey


def load_answer_key(path: Path, *, access: AccessPurpose):
    require_evaluation(access)
    return AnswerKey.model_validate_json(path.read_bytes())


def build_evaluation_inputs(snapshot: ApprovalSnapshot, candidates: tuple[CandidateFact, ...],
                            answer_key: AnswerKey, *, access: AccessPurpose):
    require_evaluation(access)
    answer_key = AnswerKey.model_validate(answer_key)
    prediction = build_prediction_bundle(snapshot, candidates)
    if prediction.claim_id != answer_key.claim_id:
        raise ValueError('Answer key belongs to another claim')
    return EvaluationInputs(prediction=prediction, answer_key=answer_key)

import hashlib
import json
import os
from pathlib import Path

from .claims import LookupError
from .diagnostics import failure_context, log_exception
from .schemas import EvaluationMetrics


def configured_artifact(name):
    path, expected = os.getenv(f'{name}_PATH'), os.getenv(f'{name}_SHA256')
    if not path or not expected:
        raise LookupError('review_unavailable', 503, f'{name.lower().replace("_", " ").capitalize()} is not configured')
    try:
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Artifact changed')
        return json.loads(raw)
    except (OSError, ValueError):
        log_exception('verify_review_artifact')
        raise LookupError('review_unavailable', 503, 'Configured review artifact failed verification') from None


def load_recommendation_model():
    payload = configured_artifact('RECOMMENDATION_MODEL')
    try:
        from .prediction.recommendations import ModelArtifact, code_hashes

        artifact = ModelArtifact.model_validate(payload)
        if artifact.code_hashes != code_hashes():
            raise ValueError('Model code changed')
        return artifact, os.environ['RECOMMENDATION_MODEL_SHA256']
    except ImportError:
        log_exception('import_recommendation_model')
        raise LookupError('review_unavailable', 503, 'Install requirements-prediction.txt to enable research recommendations') from None
    except (ValueError, KeyError, TypeError):
        log_exception('validate_recommendation_model')
        raise LookupError('review_unavailable', 503, 'Research model is incompatible; refit and verify it before use') from None


def historical_comparison(claim, recommendation):
    from .prediction.parsing import APPROVAL_STATUSES, recorded_money

    history = claim.get('historical_outcome', {})
    status = history.get('Status', '').strip()
    decision = 'approved' if status in APPROVAL_STATUSES else 'declined' if status == 'Declined' else None
    raw_amount = history.get('Approved Benefit Amount', '')
    amount, state = recorded_money(raw_amount)
    reasons = []
    if decision is None:
        reasons.append('Recorded status is not a final approved or declined outcome.')
    if decision == 'declined' and amount is not None and amount > 0:
        decision = None
        reasons.append('Declined status conflicts with a positive recorded amount.')
    limit = recommendation['benchmarks']['recorded_limit']
    if decision != 'approved':
        reasons.append('Conditional ceilings are compared only with approved outcomes; declines have no zero ceiling imputed.')
    elif state != 'present' or amount == 0:
        reasons.append('A positive, valid recorded approved amount is required for percentage error.')
    elif limit is not None and amount > limit:
        reasons.append('Recorded approved amount exceeds the recorded limit; resolve this before scoring.')
    ceiling = recommendation['conditional_ceiling']
    scoreable = decision == 'approved' and state == 'present' and amount > 0 and (limit is None or amount <= limit)
    if ceiling is None:
        reasons.append('The research ceiling is withheld.')
    difference = round(ceiling - amount, 2) if scoreable and ceiling is not None else None
    return {'recorded_status': status or None, 'recorded_decision': decision,
            'recorded_amount_raw': raw_amount or None, 'recorded_ceiling': amount if scoreable else None,
            'decision_match': recommendation['research_decision'] == decision if decision else None,
            'difference_dollars': difference,
            'absolute_error_percent': round(abs(difference) / amount * 100, 2) if difference is not None else None,
            'reasons': reasons, 'scope': 'current_record_historical_comparison',
            'interpretation': 'Current-record comparison may include training claims. Historical agreement is not validation accuracy, calibrated confidence, or independently verified correctness.'}


def recommend_claim(claim, artifact, model_sha256):
    from .prediction.parsing import canonical_id
    from .prediction.recommendations import ResearchInput, recommend

    claim_id = claim['tracking_number']
    if canonical_id(claim_id) in {'53', *(canonical_id(value) for value in artifact.reserved_ids)}:
        raise LookupError('claim_excluded', 409, 'This claim is reserved and cannot receive a research recommendation')
    values = {item['source_column']: item['recorded_value'] for item in claim['prediction_inputs'].values()}
    source = claim['claims_source']
    reference = json.dumps({key: source.get(key) for key in ('id', 'version', 'modifiedTime', 'read_at')}, sort_keys=True)
    result = recommend(artifact.models, ResearchInput(claim_id=claim_id, values=values, source_reference=reference))
    result['model_sha256'] = model_sha256
    result['historical_comparison'] = historical_comparison(claim, result)
    return result


def claim_recommendation(claim):
    return recommend_claim(claim, *load_recommendation_model())


def batch_recommendations(lookups):
    found = any(item['state'] == 'found' for item in lookups)
    try:
        with failure_context(claim_ids=[item['tracking_number'] for item in lookups if item['state'] == 'found']):
            model = load_recommendation_model() if found else None
    except LookupError as error:
        model = error
    results = []
    for item in lookups:
        number = item['tracking_number']
        if item['state'] != 'found':
            results.append({'tracking_number': number, 'state': 'error', 'error': item['error']})
            continue
        try:
            if isinstance(model, LookupError):
                raise model
            result = recommend_claim(item['claim'], *model)
            results.append({'tracking_number': number, 'state': 'succeeded', 'recommendation': result})
        except LookupError as error:
            results.append({'tracking_number': number, 'state': 'error',
                            'error': {'code': error.code, 'message': str(error)}})
        except Exception:
            log_exception('recommend_claim', claim_id=number)
            results.append({'tracking_number': number, 'state': 'error',
                            'error': {'code': 'recommendation_failed', 'message': 'Recommendation failed; retry this claim'}})
    return {'results': results}


def evaluation_metrics():
    report = configured_artifact('RECOMMENDATION_EVALUATION')
    try:
        if report['scope'] != 'development_only_historical_agreement' or report['reserved_outcomes_loaded'] is not False:
            raise ValueError('Unsupported evaluation scope')
        return EvaluationMetrics.model_validate(report).model_dump()
    except (ValueError, KeyError, TypeError):
        log_exception('validate_evaluation_metrics')
        raise LookupError('review_unavailable', 503, 'Configure a recommendations evaluate report with development-only aggregate metrics') from None

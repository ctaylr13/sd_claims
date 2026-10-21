from decimal import Decimal, InvalidOperation

from .models import PredictionBundle

BASELINE_VERSION = 'review-first-1'
MONEY_KEYS = frozenset({'monthly_rent', 'requested_amount', 'policy_limit', 'deposit_held',
                        'balance', 'total_charges', 'total_credits', 'total_deposits'})


def money_value(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal('.01')):
            return None
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount


def policy_limit(bundle):
    values = [fact for fact in bundle.facts if fact.key == 'policy_limit']
    if len(values) != 1 or values[0].state != 'present':
        return None
    return money_value(values[0].value)


def rules_baseline(bundle: PredictionBundle):
    reasons = ['adjudication_rules_unverified']
    if not bundle.eligible:
        reasons.append('no_eligible_facts')
    limit = policy_limit(bundle)
    if limit is None:
        reasons.append('policy_limit_unavailable')
    return {'version': BASELINE_VERSION, 'decision': 'needs_review',
            'approved_ceiling': None, 'policy_limit': str(limit) if limit is not None else None,
            'reasons': reasons, 'snapshot_id': bundle.snapshot_id}


def constrain_ceiling(value, bundle):
    limit = policy_limit(bundle)
    amount = money_value(value)
    if limit is None or amount is None:
        return None
    return min(amount, limit)

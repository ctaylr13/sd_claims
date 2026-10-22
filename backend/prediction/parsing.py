"""Shared claim identity and recorded-value parsing."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

APPROVAL_STATUSES = frozenset({'Posted', 'Approved', 'Approved Revised'})


def canonical_id(value):
    value = value.strip()
    if not value:
        raise ValueError('Empty claim ID')
    return str(int(value)) if value.isdecimal() else value


def money_value(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal('.01')):
            return None
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount


def recorded_money(value):
    if value is None or not str(value).strip():
        return None, 'missing'
    value = str(value).strip()
    if not re.fullmatch(r'\$?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?', value):
        return None, 'invalid'
    parsed = money_value(value.replace('$', '').replace(',', ''))
    return (float(parsed), 'present') if parsed is not None else (None, 'invalid')


def recorded_date(value):
    if value is None or not str(value).strip():
        return None, 'missing'
    for pattern in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value).strip(), pattern).date(), 'present'
        except ValueError:
            pass
    return None, 'invalid'

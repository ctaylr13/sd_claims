"""Current-record feature preparation shared by inference and research."""

import numpy as np

from .parsing import recorded_date, recorded_money

MONEY = {'Monthly Rent': 'rent', 'Amount of Claim': 'request', 'Max Benefit': 'limit'}
DATES = ('Claim Date', 'Lease Start Date', 'Lease End Date', 'Move-Out Date')


def row_features(row, *, without_limit=False):
    result = {}
    for column, name in MONEY.items():
        if without_limit and name == 'limit':
            continue
        value, state = recorded_money(row.get(column))
        result[f'{name}:state={state}'] = 1.0
        if value is not None:
            result[name] = value
    dates = {}
    for column in DATES:
        value, state = recorded_date(row.get(column))
        dates[column] = value
        result[f'{column}:state={state}'] = 1.0
    for name, start, end in (
            ('lease_days', 'Lease Start Date', 'Lease End Date'),
            ('days_after_move_out', 'Move-Out Date', 'Claim Date'),
            ('lease_days_remaining', 'Move-Out Date', 'Lease End Date')):
        if dates[start] is not None and dates[end] is not None:
            result[name] = float((dates[end] - dates[start]).days)
    termination = str(row.get('Termination Type') or '').strip()
    result[f'termination={termination if termination else "missing"}'] = 1.0
    return result


def calendar_features(row, base):
    values = dict(base)
    for name in DATES:
        value, _ = recorded_date(row.get(name))
        if value is not None:
            values[f'{name}:ordinal'] = float(value.toordinal())
            values[f'{name}:month_sin'] = float(np.sin(2 * np.pi * (value.month - 1) / 12))
            values[f'{name}:month_cos'] = float(np.cos(2 * np.pi * (value.month - 1) / 12))
    return values

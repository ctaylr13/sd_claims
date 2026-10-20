import argparse
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .drive_source import add_drive_arguments, connect_from_args, read_claims, write_json

MONEY_FIELDS = (
    'Monthly Rent', 'Amount of Claim', 'Max Benefit', 'Approved Benefit Amount',
)
REQUIRED_FIELDS = ('Tracking Number', 'Claim Date', 'Status', *MONEY_FIELDS)


def parse_money(value):
    value = value.strip()
    if not value:
        return None
    amount = Decimal(value.replace('$', '').replace(',', ''))
    if not amount.is_finite():
        raise InvalidOperation
    return amount


def audit_claims(fields, rows, source):
    missing_fields = set(REQUIRED_FIELDS) - set(fields)
    if missing_fields:
        raise ValueError(f'Missing required columns: {sorted(missing_fields)}')
    if any(None in row or None in row.values() for row in rows):
        raise ValueError('CSV contains rows with an unexpected number of columns')

    money = {}
    money_summary = {}
    for field in MONEY_FIELDS:
        parsed, invalid = [], 0
        for row in rows:
            try:
                parsed.append(parse_money(row[field]))
            except InvalidOperation:
                parsed.append(None)
                invalid += 1
        money[field] = parsed
        present = [amount for amount in parsed if amount is not None]
        money_summary[field] = {
            'missing': sum(not row[field].strip() for row in rows),
            'invalid': invalid,
            'zero': sum(amount == 0 for amount in present),
            'negative': sum(amount < 0 for amount in present),
        }

    dates, missing_dates, invalid_dates = [], 0, 0
    for row in rows:
        value = row['Claim Date'].strip()
        if not value:
            missing_dates += 1
            continue
        try:
            dates.append(datetime.strptime(value, '%m/%d/%y').date())
        except ValueError:
            invalid_dates += 1

    identifiers = Counter(row['Tracking Number'].strip() for row in rows)
    statuses = Counter(row['Status'].strip() for row in rows)
    ceilings = money['Approved Benefit Amount']
    requests = money['Amount of Claim']
    limits = money['Max Benefit']
    status_summary = {}
    for status in sorted(statuses):
        indices = [i for i, row in enumerate(rows) if row['Status'].strip() == status]
        status_summary[status] = {
            'rows': len(indices),
            'ceiling_missing_or_invalid': sum(ceilings[i] is None for i in indices),
            'ceiling_zero': sum(ceilings[i] == 0 for i in indices),
            'ceiling_positive': sum(ceilings[i] is not None and ceilings[i] > 0 for i in indices),
        }

    return {
        'source': source,
        'rows': len(rows),
        'columns': len(fields),
        'unnamed_columns': sum(not field.strip() for field in fields),
        'tracking_numbers': {
            'missing': identifiers.get('', 0),
            'unique_nonempty': len(identifiers.keys() - {''}),
            'duplicate_nonempty_rows': sum(count - 1 for key, count in identifiers.items() if key),
        },
        'claim_dates': {
            'earliest': min(dates).isoformat() if dates else None,
            'latest': max(dates).isoformat() if dates else None,
            'missing': missing_dates,
            'invalid': invalid_dates,
        },
        'status_summary': status_summary,
        'money_fields': money_summary,
        'ceiling_checks': {
            'exceeds_policy_limit': sum(ceiling is not None and limit is not None and ceiling > limit
                                        for ceiling, limit in zip(ceilings, limits)),
        },
        'ceiling_request_comparison': {
            'ceiling_above_requested': sum(ceiling is not None and request is not None and ceiling > request
                                           for ceiling, request in zip(ceilings, requests)),
            'interpretation': 'A ceiling above the requested reimbursement is not a payout discrepancy.',
        },
        'missing_by_column': {
            field or '<unnamed>': sum(not row[field].strip() for row in rows)
            for field in fields
        },
        'target_definitions': {
            'Max Benefit': 'Policy limit (confirmed by user); input and payout constraint.',
            'Amount of Claim': 'Reimbursement requested by the landlord (confirmed by user); candidate pre-decision input, subject to availability checks.',
            'Approved Benefit Amount': 'Approved reimbursement ceiling (confirmed by user); candidate ceiling target, not actual payout.',
            'Actual Payout': 'Not established by these fields. A capped reimbursement calculation is derived, not a verified historical payment.',
        },
        'confirmed_status_labels': {
            'Posted': 'approved', 'Approved': 'approved', 'Approved Revised': 'approved',
        },
        'label_policy': 'Posted, Approved, and Approved Revised are final approvals (confirmed by user). Missing ceilings remain missing; a positive ceiling does not establish approval or payment. Other status mappings require review.',
    }


def main():
    parser = argparse.ArgumentParser(description='Audit live Drive claims without exporting claim records.')
    add_drive_arguments(parser)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        service, sources = connect_from_args(args)
        fields, rows, source = read_claims(service, sources['claims_file_id'])
        report = audit_claims(fields, rows, source)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    if args.output:
        write_json(args.output, report)
    else:
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

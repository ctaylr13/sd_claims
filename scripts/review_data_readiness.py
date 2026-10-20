import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import InvalidOperation
from pathlib import Path

from .audit_claims import parse_money
from .drive_source import FOLDER_MIME, add_drive_arguments, connect_from_args, list_children, read_claims, write_json

APPROVED_STATUSES = {'Posted', 'Approved', 'Approved Revised'}
NONFINAL_STATUSES = {'New', 'In Process', 'HOLD', 'Pending Docs from PM', 'Test Claim'}


def money_state(value):
    try:
        amount = parse_money(value)
    except InvalidOperation:
        return None, 'invalid'
    if amount is None:
        return None, 'missing'
    return amount, 'negative' if amount < 0 else 'zero' if amount == 0 else 'positive'


def parse_date(value):
    if not value.strip():
        return None
    for pattern in ('%m/%d/%y', '%m/%d/%Y'):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            pass
    raise ValueError('Unrecognized date')


def review_claims(fields, rows, source, root_items, inventory):
    folders = defaultdict(list)
    for item in root_items:
        if item['mimeType'] == FOLDER_MIME:
            folders[item['name'].strip()].append(item['id'])
    money_cases, missing, date_cases = [], [], []
    dates_by_status = defaultdict(Counter)
    date_fields = [field for field in ('Claim Date', 'Approval Date', 'Posted Date') if field in fields]
    for number, row in enumerate(rows, 2):
        tracking, status = row['Tracking Number'].strip(), row['Status'].strip()
        ref = {'sheet_row': number, 'tracking_number': tracking, 'status': status}
        if tracking not in folders:
            missing.append(ref)
        ceiling, ceiling_state = money_state(row['Approved Benefit Amount'])
        limit, limit_state = money_state(row['Max Benefit'])
        reasons = []
        if ceiling is not None and limit is not None and ceiling > limit:
            reasons.append('ceiling_above_recorded_policy_limit')
        if status in APPROVED_STATUSES and ceiling_state != 'positive':
            reasons.append(f'final_approval_ceiling_{ceiling_state}')
        if status == 'Declined' and ceiling_state == 'positive':
            reasons.append('declined_with_positive_ceiling')
        if status in APPROVED_STATUSES and limit_state != 'positive':
            reasons.append(f'final_approval_policy_limit_{limit_state}')
        if reasons:
            money_cases.append({**ref, 'reasons': reasons,
                                'recorded_ceiling': row['Approved Benefit Amount'],
                                'recorded_policy_limit': row['Max Benefit'],
                                'recorded_explanation': row.get('PM Explanation', ''),
                                'recorded_hold_reason': row.get('Hold Reason', ''),
                                'action': 'Preserve original values; resolve semantics before using anomalous ceiling labels.'})
        dates = {}
        for field in date_fields:
            try:
                dates[field] = parse_date(row[field])
                state = 'present' if dates[field] else 'missing'
            except ValueError:
                dates[field], state = None, 'invalid'
                date_cases.append({**ref, 'reason': f'invalid_{field}', 'recorded_value': row[field]})
            dates_by_status[status][f'{field}:{state}'] += 1
        claim, approval, posted = (dates.get(k) for k in ('Claim Date', 'Approval Date', 'Posted Date'))
        for reason, earlier, later in (
            ('approval_before_claim', claim, approval), ('posting_before_claim', claim, posted),
            ('posting_before_approval', approval, posted),
        ):
            if earlier and later and later < earlier:
                date_cases.append({**ref, 'reason': reason})
    files = [(claim['folder']['name'].strip(), item)
             for claim in inventory['claims'].values() for item in claim['items'] if item['mimeType'] != FOLDER_MIME]
    checksums, repeated = defaultdict(list), []
    for claim in inventory['claims'].values():
        by_name = defaultdict(list)
        for item in claim['items']:
            if item['mimeType'] == FOLDER_MIME:
                continue
            by_name[item['name']].append(item['id'])
            if item.get('md5Checksum'):
                checksums[item['md5Checksum']].append({'tracking_number': claim['folder']['name'], 'file_id': item['id']})
        repeated.extend({'tracking_number': claim['folder']['name'], 'filename': name, 'file_ids': ids}
                        for name, ids in by_name.items() if len(ids) > 1)
    live_ids = {fid for ids in folders.values() for fid in ids}
    snapshot_ids = {item['id'] for item in inventory['root_items'] if item['mimeType'] == FOLDER_MIME}
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(), 'claims_source': source,
        'claim_rows': len(rows), 'status_counts': dict(Counter(row['Status'].strip() for row in rows)),
        'evidence': {
            'live_folder_count': len(live_ids), 'snapshot_root_ids_match_live': live_ids == snapshot_ids,
            'metadata_snapshot_updated_at': inventory['updated_at'],
            'snapshot_complete': inventory['complete'], 'snapshot_errors': inventory['errors'],
            'limitation': 'Root listing is live; nested metadata is the timestamped inventory snapshot. No whole-Drive content search or ZIP inspection.',
            'missing_claims': missing, 'missing_by_status': dict(Counter(row['status'] for row in missing)),
            'duplicate_folder_names': {name: ids for name, ids in folders.items() if len(ids) > 1},
            'empty_claims': [{'tracking_number': c['folder']['name'], 'folder_id': c['folder']['id']}
                             for c in inventory['claims'].values() if not any(f['mimeType'] != FOLDER_MIME for f in c['items'])],
            'identical_content_groups': [group for group in checksums.values() if len(group) > 1],
            'repeated_filenames': repeated,
            'zip_file_ids': [item['id'] for _, item in files if item['mimeType'] == 'application/zip'],
        },
        'ceiling_review_cases': money_cases,
        'ceiling_reason_counts': dict(Counter(reason for case in money_cases for reason in case['reasons'])),
        'dates_by_status': {status: dict(counts) for status, counts in dates_by_status.items()},
        'date_anomalies': date_cases,
        'payment_candidate_columns': [field for field in fields if any(term in field.lower() for term in ('collect', 'settlement', 'paid', 'payment', 'payout'))],
        'training_review': {
            'confirmed_approval_statuses': sorted(APPROVED_STATUSES),
            'excluded_nonfinal_statuses': sorted(NONFINAL_STATUSES),
            'ceiling_policy': 'Missing/invalid ceilings stay missing. Do not convert declines to zero. Quarantine anomalous ceiling labels pending reconciliation; approval labels stay independent of dollar values.',
            'decline_policy': 'Review Declined records for cancellations/withdrawals before treating them all as final denials.',
            'payment_policy': 'No verified insurer-paid amount column. Collection/settlement fields are not established insurer payout labels.',
            'timing_policy': 'Approval Date and Posted Date are candidates, not verified decision timestamps. Current values and Drive modifiedTime do not prove decision-time availability; status and field history are required.',
        },
    }


def main():
    parser = argparse.ArgumentParser(description='Review data readiness using live Drive claims and a metadata inventory.')
    add_drive_arguments(parser)
    parser.add_argument('--inventory', type=Path, default=Path('data/drive-inventory.json'))
    parser.add_argument('--output', type=Path, default=Path('data/step1-review.json'))
    args = parser.parse_args()
    service, sources = connect_from_args(args)
    fields, rows, source = read_claims(service, sources['claims_file_id'])
    inventory = json.loads(args.inventory.read_text())
    if inventory['root_folder_id'] != sources['evidence_root_folder_id']:
        parser.error('Inventory belongs to a different evidence root')
    report = review_claims(fields, rows, source, list_children(service, sources['evidence_root_folder_id']), inventory)
    write_json(args.output, report)
    print(json.dumps({'report': str(args.output), 'claim_rows': report['claim_rows'],
                      'missing_evidence': len(report['evidence']['missing_claims']),
                      'ceiling_reason_counts': report['ceiling_reason_counts'],
                      'date_anomalies': len(report['date_anomalies'])}, indent=2))


if __name__ == '__main__':
    main()

from collections import defaultdict

from .drive import FOLDER_MIME, inventory_claim, list_children, read_claims

INPUT_FIELDS = {
    'Claim Date': 'claim_date', 'Lease Start Date': 'lease_start_date',
    'Lease End Date': 'lease_end_date', 'Move-Out Date': 'move_out_date',
    'Monthly Rent': 'monthly_rent', 'Amount of Claim': 'requested_amount',
    'Max Benefit': 'policy_limit', 'Termination Type': 'termination_type',
}
OUTCOME_FIELDS = ('Status', 'Approved Benefit Amount', 'Approval Date', 'Posted Date')
SOURCE_UNAVAILABLE_MESSAGE = 'Claim source unavailable; retry or check server credentials'


class LookupError(ValueError):
    def __init__(self, code, status, message):
        super().__init__(message)
        self.code, self.status = code, status


def validate_tracking_number(tracking_number):
    tracking_number = tracking_number.strip()
    if not tracking_number or len(tracking_number) > 128:
        raise LookupError('invalid_tracking_number', 400, 'Supply a tracking number of 1–128 characters')
    return tracking_number


def load_claim_index(service, sources, tracking_number=None):
    _, rows, source = read_claims(service, sources['claims_file_id'])
    records, folders = defaultdict(list), defaultdict(list)
    for row in rows:
        records[row['Tracking Number'].strip()].append(row)
    if tracking_number is not None:
        find_record(records, tracking_number)
    for item in list_children(service, sources['evidence_root_folder_id']):
        if item['mimeType'] == FOLDER_MIME:
            folders[item['name'].strip()].append(item)
    return records, source, folders


def find_record(records, tracking_number):
    matches = records.get(tracking_number, [])
    if not matches:
        raise LookupError('claim_not_found', 404, 'Tracking number is not in the live claims source')
    if len(matches) != 1:
        raise LookupError('duplicate_claim_records', 409, 'Multiple claim records require review')
    return matches[0]


def find_claim(service, tracking_number, records, source, folders):
    row = find_record(records, tracking_number)
    matches_folders = folders.get(tracking_number, [])
    if len(matches_folders) > 1:
        raise LookupError('duplicate_evidence_folders', 409, 'Multiple evidence folders require review')
    evidence = inventory_claim(service, matches_folders[0]) if matches_folders else {'folder': None, 'items': []}
    return {
        'tracking_number': tracking_number, 'claims_source': source,
        'prediction_inputs': {name: {'recorded_value': row.get(column, ''), 'source_column': column,
                                     'availability': 'decision_time_unverified'}
                              for column, name in INPUT_FIELDS.items()},
        'historical_outcome': {column: row.get(column, '') for column in OUTCOME_FIELDS},
        'evidence': {**evidence, 'state': 'present' if matches_folders else 'missing'},
        'training_eligible': False,
    }


def lookup_claim(service, sources, tracking_number):
    tracking_number = validate_tracking_number(tracking_number)
    return find_claim(service, tracking_number, *load_claim_index(service, sources, tracking_number))


def lookup_claims(service, sources, tracking_numbers):
    try:
        index = load_claim_index(service, sources)
    except Exception:
        return [_error_result(number, LookupError('source_unavailable', 502, SOURCE_UNAVAILABLE_MESSAGE))
                for number in tracking_numbers]
    results = []
    for number in tracking_numbers:
        try:
            number = validate_tracking_number(number)
            claim = find_claim(service, number, *index)
            results.append({'tracking_number': number, 'state': 'found', 'http_status': 200, 'claim': claim})
        except LookupError as error:
            results.append(_error_result(number, error))
        except Exception:
            results.append(_error_result(number, LookupError('source_unavailable', 502, SOURCE_UNAVAILABLE_MESSAGE)))
    return results


def _error_result(number, error):
    return {'tracking_number': number, 'state': 'error', 'http_status': error.status,
            'error': {'code': error.code, 'message': str(error)}}

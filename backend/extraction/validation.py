import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .facts import date_value, money
from .models import Citation, DocumentFacts


def value(field):
    if field is None or field.state != 'present':
        return None
    values = {obs.value for obs in field.observations}
    return next(iter(values)) if len(values) == 1 and None not in values else None


def decimal_value(field):
    raw = value(field)
    try:
        return Decimal(raw) if raw is not None and re.fullmatch(r'-?\d+\.\d{2}', raw) else None
    except InvalidOperation:
        return None


def fact_items(result):
    yield from result.fields.items()
    for entry in result.entries:
        for name in ('amount', 'posting_date', 'service_period', 'running_balance'):
            yield f'{entry.entry_id}.{name}', getattr(entry, name)
    for index, term in enumerate(result.rent_terms):
        for name in ('start', 'end', 'monthly_rent'):
            yield f'rent_terms.{index}.{name}', getattr(term, name)


def all_citations(result):
    for _, field in fact_items(result):
        for obs in field.observations:
            yield from (cite for cite in obs.citations if isinstance(cite, Citation))
    for entry in result.entries:
        yield from entry.citations
    for clause in result.clauses:
        yield from (cite for cite in clause.citations if isinstance(cite, Citation))
    yield from result.unparsed


def intersects(first, second):
    return (max(first[0], second[0]) < min(first[2], second[2])
            and max(first[1], second[1]) < min(first[3], second[3]))


def center_inside(box, region, tolerance=0):
    x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
    return (region[0]-tolerance <= x <= region[2]+tolerance
            and region[1]-tolerance <= y <= region[3]+tolerance)


def field_citations(field):
    return [cite for obs in field.observations for cite in obs.citations if isinstance(cite, Citation)]


def check(name, status, **details):
    return {'check': name, 'status': status, **details}


def numeric_check(name, expected, observed, **details):
    if expected is None or observed is None:
        return check(name, 'skipped', reason='Missing, unreadable, conflicting, or ambiguous operand', **details)
    return check(name, 'passed' if expected == observed else 'failed',
                 expected=format(expected, '.2f'), observed=format(observed, '.2f'),
                 difference=format(observed-expected, '.2f'), **details)


def source_hash(document):
    try:
        return hashlib.sha256(Path(document.original_path).read_bytes()).hexdigest() if document.original_path else None
    except OSError:
        return None


def ledger_direction(entries):
    dates = [value(entry.posting_date) for entry in entries]
    if any(date is None for date in dates):
        return None
    differences = {(later > earlier)-(later < earlier) for earlier, later in zip(dates, dates[1:]) if later != earlier}
    return 'oldest_first' if differences == {1} else 'newest_first' if differences == {-1} else None


def delta(entry):
    if entry.kind == 'unclassified' or not entry.source_columns:
        return None
    columns = {name: money(raw) for name, raw in entry.source_columns.items() if name in ('Charges', 'Payments')}
    if not columns or None in columns.values() or len(columns) > 1:
        return None
    return Decimal(columns.get('Charges', '0.00')) - Decimal(columns.get('Payments', '0.00'))


def anchor(entry):
    citations = field_citations(entry.posting_date) or entry.citations[:1]
    cite = citations[0]
    return cite.page, (cite.bbox[1]+cite.bbox[3])/2


def blocked(field, regions):
    return any(region['page'] == cite.page and intersects(cite.bbox, region['bbox'])
               for cite in field_citations(field) for region in regions)


def ledger_arithmetic(result, *, order=None, regions=()):
    entries = result.entries
    order = order or ledger_direction(entries)
    if order not in ('newest_first', 'oldest_first'):
        return [check('running_balances', 'skipped', reason='Ledger direction not established from source dates')]
    checks = []
    for first, second in zip(entries, entries[1:]):
        current, previous = (first, second) if order == 'newest_first' else (second, first)
        references = {'entries': [current.entry_id, previous.entry_id], 'order': order,
                      'citations': [cite.model_dump(mode='json') for cite in current.citations[:1]+previous.citations[:1]]}
        start, end = sorted((anchor(first), anchor(second)))
        gaps = [cite for cite in result.unparsed if start < (cite.page, (cite.bbox[1]+cite.bbox[3])/2) < end]
        if gaps or abs(start[0]-end[0]) > 1:
            checks.append(check('running_balance', 'skipped', reason='Unparsed source region between rows', **references))
            continue
        if blocked(current.running_balance, regions) or blocked(previous.running_balance, regions):
            checks.append(check('running_balance', 'skipped', reason='Source balance cropped or unreadable', **references))
            continue
        change, before, after = delta(current), decimal_value(previous.running_balance), decimal_value(current.running_balance)
        expected = before+change if before is not None and change is not None else None
        checks.append(numeric_check('running_balance', expected, after, **references))
    if entries:
        latest = entries[0] if order == 'newest_first' else entries[-1]
        checks.append(numeric_check('summary_matches_latest_row', decimal_value(result.fields.get('balance')),
                                    None if blocked(latest.running_balance, regions) else decimal_value(latest.running_balance),
                                    entry=latest.entry_id))
        oldest = entries[-1] if order == 'newest_first' else entries[0]
        opening, change = decimal_value(result.fields.get('opening_balance')), delta(oldest)
        checks.append(numeric_check('opening_balance_to_first_row',
                                    opening+change if opening is not None and change is not None else None,
                                    None if blocked(oldest.running_balance, regions) else decimal_value(oldest.running_balance),
                                    entry=oldest.entry_id))
    return checks


def statement_arithmetic(result):
    checks = []
    for kind, total_name in [('charge', 'total_charges'), ('credit', 'total_credits'), ('deposit', 'total_deposits')]:
        entries = [entry for entry in result.entries if entry.kind == kind]
        amounts = [decimal_value(entry.amount) for entry in entries]
        total = sum(amounts, Decimal('0.00')) if amounts and None not in amounts else None
        checks.append(numeric_check(f'{kind}_lines_match_total', total, decimal_value(result.fields.get(total_name)),
                                    entries=[entry.entry_id for entry in entries]))
    charges = decimal_value(result.fields.get('total_charges'))
    credits = decimal_value(result.fields.get('total_credits'))
    deposits = decimal_value(result.fields.get('total_deposits'))
    # Only one explicit deduction basis -- held deposits are not another credit.
    deduction = credits if credits is not None and deposits is None else deposits if deposits is not None and credits is None else None
    net = charges-deduction if charges is not None and deduction is not None else None
    due, refund = decimal_value(result.fields.get('tenant_balance_due')), decimal_value(result.fields.get('resident_refund'))
    if due is not None and refund is not None:
        checks.append(check('net_due', 'skipped', reason='Both resident refund and tenant balance are populated'))
    else:
        checks.append(numeric_check('net_due', net, due if due is not None else -refund if refund is not None else None))
    return checks


def validate_document(result, *, regions=(), order=None):
    doc = result.document
    checks, issues = [], []
    actual_hash = source_hash(doc)
    checks.append(check('source_hash', 'passed' if actual_hash and actual_hash == doc.sha256 else 'failed',
                        expected=doc.sha256, observed=actual_hash))
    pages = {page.number: page for page in doc.pages}
    citations = {cite.model_dump_json(): cite for cite in all_citations(result)}
    bad_citations = []
    for cite in citations.values():
        page = pages.get(cite.page)
        x0, y0, x1, y1 = cite.bbox
        valid = (page and cite.sha256 == doc.sha256 and cite.document_id == doc.document_id
                 and cite.method == page.method and 0 <= x0 < x1 <= page.width+0.5 and 0 <= y0 < y1 <= page.height+0.5)
        if valid:
            region_words = Counter(token for word in page.words if center_inside(word.bbox, cite.bbox, tolerance=0.5)
                                   for token in word.text.split())
            valid = not (Counter(cite.quote.split())-region_words)
        if not valid:
            bad_citations.append(cite.model_dump(mode='json'))
    checks.append(check('citation_integrity', 'failed' if bad_citations else 'passed', checked=len(citations),
                        invalid=bad_citations, scope='Hash, page, bounds, method and retained word locations; not visual accuracy'))
    for name, field in fact_items(result):
        if result.document.kind == 'ledger' and field.state == 'missing' and name.endswith(('.amount', '.posting_date', '.running_balance')):
            issues.append({'code': 'missing_financial_operand', 'field': name, 'reason': field.reason})
        if field.state in ('conflicting', 'unreadable', 'unreviewed'):
            issues.append({'code': f'field_{field.state}', 'field': name, 'reason': field.reason})
        if name.endswith('service_period') and value(field) and '/' in value(field):
            start, end = value(field).split('/', 1)
            if start > end:
                issues.append({'code': 'reversed_service_period', 'field': name, 'raw_value': value(field)})
        for obs in field.observations:
            money_field = name.endswith(('.amount', '.running_balance', '.monthly_rent')) or name in {
                'requested_insurer_reimbursement', 'tenant_balance_due', 'resident_refund', 'total_charges',
                'total_credits', 'total_deposits', 'deposit_held', 'prepayments', 'opening_balance', 'closing_balance',
                'deposit_balance', 'deposit_paid', 'balance', 'total_unpaid', 'deposit_requirement',
            }
            date_field = name.endswith(('.posting_date', '.start', '.end')) or name in {
                'reported_move_out', 'key_return', 'confirmed_possession_end', 'reported_lease_expiration',
                'rent_paid_through', 'notice_date', 'balance_as_of',
            }
            normalized = money(obs.raw_value) if money_field else date_value(obs.raw_value) if date_field else obs.value
            if obs.value is not None and normalized != obs.value:
                issues.append({'code': 'invalid_normalized_value', 'field': name, 'raw': obs.raw_value, 'value': obs.value})
            if any('Low OCR' in limitation for limitation in obs.limitations):
                issues.append({'code': 'low_ocr_confidence', 'field': name})
    repeated = defaultdict(list)
    for entry in result.entries:
        if entry.citations:
            cite = entry.citations[0]
            repeated[(cite.page, tuple(round(v, 2) for v in cite.bbox))].append(entry.entry_id)
        date_cites, amount_cites = field_citations(entry.posting_date), field_citations(entry.amount)
        if date_cites and amount_cites:
            date_box, amount_box = date_cites[0].bbox, amount_cites[0].bbox
            if date_cites[0].page != amount_cites[0].page or max(date_box[1], amount_box[1]) > min(date_box[3], amount_box[3])+4:
                issues.append({'code': 'row_alignment_suspect', 'entry': entry.entry_id})
    issues.extend({'code': 'duplicate_source_region', 'entries': ids} for ids in repeated.values() if len(ids) > 1)
    for region in regions:
        issues.append({'code': 'source_'+region['state'], 'page': region['page'], 'bbox': region['bbox'],
                       'reason': region['reason'], 'value': None})
    if result.unparsed:
        issues.append({'code': 'unparsed_regions', 'count': len(result.unparsed)})
    for page in doc.pages:
        if page.method == 'ocr':
            issues.append({'code': 'ocr_table_unverified', 'page': page.number})
            if any('\n' in word.text or '\t' in word.text for word in page.words):
                issues.append({'code': 'malformed_ocr_token', 'page': page.number,
                               'reason': 'Word contains TSV separators or newlines; rerun OCR ingestion'})
        if page.state == 'unreadable' or page.errors:
            issues.append({'code': 'page_unreadable_or_failed', 'page': page.number, 'errors': page.errors})
    issues.extend({'code': 'document_error', 'reason': reason} for reason in doc.errors)
    if doc.kind == 'ledger':
        checks.extend(ledger_arithmetic(result, order=order, regions=regions))
    if doc.kind == 'move_out_statement':
        checks.extend(statement_arithmetic(result))
    return {'document_id': doc.document_id, 'sha256': doc.sha256, 'checks': checks, 'issues': issues,
            'status': 'review_required' if issues or any(c['status'] != 'passed' for c in checks) else 'checks_passed_within_scope',
            'source_visual_accuracy': 'not_established_by_automated_checks'}


def annotation_checks(results, annotations):
    checks = []
    for expected in annotations:
        if expected['type'] == 'unreadable_region':
            continue
        candidates = [result for result in results if result.document.sha256 == expected['sha256']]
        if not candidates:
            checks.append(check('source_annotation', 'skipped', annotation=expected['id'], reason='Source version not included'))
            continue
        result = candidates[0]
        if source_hash(result.document) != expected['sha256']:
            checks.append(check('source_annotation', 'failed', annotation=expected['id'], reason='Retained source hash does not match reviewed source'))
            continue
        compared, problems = {}, []
        if expected['type'] == 'entry':
            entries = [entry for entry in result.entries if any(
                cite.page == expected['page'] and center_inside(cite.bbox, expected['row_bbox'])
                for cite in field_citations(entry.posting_date)+field_citations(entry.amount))]
            if len(entries) != 1:
                checks.append(check('source_row', 'failed', annotation=expected['id'],
                                    reason='No unique extracted row', candidates=[entry.entry_id for entry in entries]))
                continue
            entry = entries[0]
            for name, target in expected['values'].items():
                actual = getattr(entry, name)
                actual = value(actual) if hasattr(actual, 'observations') else actual
                compared[name] = {'expected': target, 'observed': actual}
                if actual != target:
                    problems.append(name)
            for token in expected.get('description_contains', []):
                if token.casefold() not in entry.description.casefold():
                    problems.append('description:'+token)
            for cite in field_citations(entry.amount):
                if cite.page != expected['page'] or not center_inside(cite.bbox, expected['amount_bbox']):
                    problems.append('amount_column')
            row_cite = entry.citations[0]
            if row_cite.bbox[1] < expected['row_bbox'][1]-4 or row_cite.bbox[3] > expected['row_bbox'][3]+4:
                problems.append('row_region_spills')
            compared['entry_id'] = entry.entry_id
        else:
            parts = expected['field'].split('.')
            field = result.fields.get(parts[0]) if len(parts) == 1 else (
                getattr(result.rent_terms[int(parts[1])], parts[2]) if int(parts[1]) < len(result.rent_terms) else None)
            actual = value(field)
            compared = {'expected': expected['value'], 'observed': actual}
            if actual != expected['value']:
                problems.append('value')
            if field is None or not any(cite.page == expected['page'] and intersects(cite.bbox, expected['bbox'])
                                        for cite in field_citations(field)):
                problems.append('source_region')
        checks.append(check('source_annotation', 'failed' if problems else 'passed', annotation=expected['id'],
                            compared=compared, problems=problems, reviewer_kind=expected['reviewer_kind'],
                            sha256=expected['sha256'], page=expected['page']))
    return checks


def validate_report(results, annotations=()):
    documents = []
    for result in results:
        regions = [item for item in annotations if item['type'] == 'unreadable_region' and item['sha256'] == result.document.sha256]
        documents.append(validate_document(result, regions=regions))
    copies = defaultdict(list)
    for result, validation in zip(results, documents):
        if validation['checks'][0]['status'] == 'passed':
            copies[result.document.sha256].append(result.document.document_id)
    duplicates = [{'sha256': sha, 'documents': ids, 'action': 'Preserve source occurrences; do not add repeated assertions'}
                  for sha, ids in copies.items() if len(ids) > 1]
    return {'validation_version': 'checks-0.1', 'status': 'review_required',
            'documents': documents, 'duplicate_documents': duplicates,
            'source_page_checks': annotation_checks(results, annotations),
            'independent_human_validation': False, 'reliable_ocr_table_extraction': 'not_established',
            'limitations': ['Arithmetic consistency does not prove extraction accuracy or completeness.',
                            'Cropped values are not reconstructed from arithmetic.',
                            'Similar amounts or descriptions alone do not establish duplicate transactions.',
                            'Agent visual annotations are development checks, not an independent evaluation set.']}


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Validate saved extraction against retained sources and optional page annotations')
    parser.add_argument('report', type=Path)
    parser.add_argument('--annotations', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    results = [DocumentFacts.model_validate(item) for item in report['documents']]
    annotations = json.loads(args.annotations.read_text())['annotations'] if args.annotations else []
    validation = validate_report(results, annotations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(validation, indent=2)+'\n')
    print(args.output.resolve())


if __name__ == '__main__':
    main()

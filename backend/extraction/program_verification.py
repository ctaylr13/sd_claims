"""Opt-in native waiver readings and proposal checks; never claim decisions."""

import hashlib
import io
import re
from collections import Counter
from pathlib import Path

import pdfplumber

from .models import Citation, DocumentInput
from .facts import MONEY, money
from .pdf import fallback_reason, native_words
from .program_cells import canonical_quote, read_waiver_cells

PROGRAM_KEYS = ('program_election', 'program_monthly_fee', 'waived_deposit')


def read_program_document(source: DocumentInput, expected_sha256: str):
    if source.kind not in ('lease', 'renewal'):
        raise ValueError('Program cells require a lease or renewal')
    content = Path(source.path).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError('Program source changed')
    pages, candidates = {}, []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            if page.bbox[0] or page.bbox[1]:
                raise ValueError('Nonzero page origin')
            readable = not fallback_reason(page, native_words(page)) and number not in source.force_ocr_pages
            records, audits = read_waiver_cells(page) if readable else ([], [])
            for record in records:
                record['citation'] = Citation(document_id=source.document_id, sha256=expected_sha256,
                    page=number, quote=record['quote'], bbox=record['bbox'], method='native_text').model_dump(mode='json')
                candidates.append({'key':record['key'], 'value':record['value']})
            for match in re.finditer(rf'zero\s+deposit\s+program\s+(?:premium|fee)\s*:?\s*({MONEY})',
                                     page.extract_text() or '', re.I):
                candidates.append({'key':'program_monthly_fee', 'value':money(match.group(1))})
            pages[number] = {'size':[page.width,page.height], 'readable':bool(readable),
                             'records':records, 'audits':audits}
    incomplete = not pages or any(not p['readable'] for p in pages.values())
    holds = {}
    for key in PROGRAM_KEYS:
        if incomplete:
            holds[key] = 'incomplete_document_context'
        elif len({c['value'] for c in candidates if c['key']==key}) > 1:
            holds[key] = 'conflicting_document_values'
    return {'sha256':expected_sha256, 'pages':pages, 'holds':holds}


def quote_supported(quote, context):
    def tokens(value):
        return Counter(re.findall(r'[+-]?\d+(?:\.\d+)?|[a-z]+',canonical_quote(value)))
    proposed, recovered = tokens(quote), tokens(context)
    return bool(proposed and not proposed-recovered)


def verify_program_proposals(proposals, source, page_number):
    page = source['pages'][page_number]
    results, used = [], set()
    for proposal in proposals:
        key, value = proposal['key'], proposal['value']
        reason, record = None, None
        if key not in PROGRAM_KEYS:
            reason = 'no_independent_program_rule'
        elif not page['readable'] or key in source['holds']:
            reason = source['holds'].get(key,'source_not_readable')
        elif (not isinstance(value,str) or (key=='program_election' and value not in ('yes','no'))
              or (key!='program_election' and not re.fullmatch(r'-?\d+\.\d{2}',value))):
            reason = 'invalid_value_type'
        elif proposal.get('event_date') is not None or proposal.get('service_period') is not None:
            reason = 'program_terms_require_null_event_context'
        else:
            matches = [r for r in page['records'] if r['key']==key and r['value']==value
                       and quote_supported(proposal['quote'],r['context'])]
            if len(matches)!=1:
                reason = 'ambiguous_source_cells' if matches else 'value_or_quote_not_supported'
            elif (key,value) in used:
                reason = 'duplicate_proposal'
            else:
                record, = matches
                cite = record['citation']
                x0,y0,x1,y1 = cite['bbox']
                if (cite['sha256']!=source['sha256'] or cite['page']!=page_number
                        or cite['method']!='native_text'
                        or not 0<=x0<x1<=page['size'][0] or not 0<=y0<y1<=page['size'][1]):
                    reason = 'invalid_source_anchor'
                    record = None
                else:
                    used.add((key,value))
        results.append({'proposal':proposal, 'status':'needs_review' if reason else 'source_supported',
                        'reason':reason or 'native_form_cells', 'anchor':record['citation'] if record else None,
                        'prediction_eligible':False, 'training_eligible':False})
    return results

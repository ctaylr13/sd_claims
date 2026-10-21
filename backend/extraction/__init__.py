import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .facts import fact, money
from .financial import ledger, move_out, shared_fields
from .lease import effective_rent, lease
from .models import Document, DocumentFacts, Manifest, Observation
from .pdf import extract_pdf

PARSERS = {'ledger': ledger, 'move_out_statement': move_out, 'lease': lease, 'renewal': lease}


def extract_claim(manifest: Manifest, output_root: Path):
    from .validation import validate_report

    run_dir = output_root.resolve() / uuid.uuid4().hex
    run_dir.mkdir(parents=True, mode=0o700)
    documents = []
    for index, item in enumerate(manifest.documents):
        try:
            doc = extract_pdf(item, run_dir / f'document-{index+1}')
        except OSError as error:
            doc = Document(document_id=item.document_id, kind=item.kind, sha256=None, original_path=None,
                           source={**item.source, 'requested_local_path': item.path},
                           errors=[f'source_read_failed:{type(error).__name__}'])
        try:
            documents.append(PARSERS[item.kind](doc))
        except Exception as error:
            doc.errors.append(f'parser_failed:{type(error).__name__}')
            documents.append(DocumentFacts(document=doc, fields=shared_fields(doc), limitations=[
                'Parser failed; document requires review',
            ]))
    requests = [obs for result in documents
                for obs in result.fields.get('requested_insurer_reimbursement', fact([])).observations]
    requests.extend(Observation(raw_value=cell.quote, value=money(cell.quote), citations=[cell],
                                limitations=['Decision-time availability unverified'])
                    for cell in manifest.recorded_requests)
    request = fact(requests, unavailable=any(result.document.errors or
                   any(page.state == 'unreadable' for page in result.document.pages) for result in documents))
    report = {
        'schema_version': 'extraction-0.2', 'claim_id': manifest.claim_id,
        'created_at': datetime.now(timezone.utc).isoformat(), 'parser_version': 'rules-0.2',
        'documents': [result.model_dump(mode='json') for result in documents],
        'requested_insurer_reimbursement': request.model_dump(mode='json'),
        'effective_on': manifest.effective_on,
        'effective_monthly_rent': effective_rent(documents, manifest.effective_on).model_dump(mode='json'),
        'validation': validate_report(documents),
        'training_eligible': False, 'review_required': True,
        'limitations': ['Development extraction; no adjudication or independently validated accuracy.',
                        'Missing states describe supported-pattern search scope, not absence of evidence everywhere.',
                        'Confirmation labels are source assertions, not independent verification.',
                        'Receipt before decision is unverified. No model provider or durable job store selected.'],
    }
    report_path = run_dir / 'report.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    return report_path

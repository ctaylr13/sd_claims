import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.diagnostics import failure_context, log_exception
from backend.storage import LocalStore

from .capture import MAX_DOCUMENTS, MAX_TOTAL_BYTES, ExtractionLimitExceeded, capture_pdf
from .facts import fact, money
from .financial import ledger, move_out, shared_fields
from .lease import effective_rent, lease
from .models import Document, DocumentFacts, Manifest, Observation
from .pdf import extract_pdf, extraction_tools

PARSER_VERSION = 'rules-0.3'
SCHEMA_VERSION = 'extraction-0.3'
CONFIG_VERSION = 'pdf-0.3'

PARSERS = {'ledger': ledger, 'move_out_statement': move_out, 'lease': lease, 'renewal': lease}


def extract_claim(manifest: Manifest, output_root: Path, *, retry=False):
    if len(manifest.documents) > MAX_DOCUMENTS:
        raise ExtractionLimitExceeded('Each claim must contain at most 20 documents')
    with TemporaryDirectory(prefix='claim-pdfs-') as temporary:
        sources = []
        total_bytes = 0
        request = manifest.model_dump(mode='json')
        for index, (item, reference) in enumerate(zip(manifest.documents, request['documents'])):
            captured = Path(temporary) / f'{index}.pdf'
            try:
                sha256, size = capture_pdf(Path(item.path), captured, remaining_bytes=MAX_TOTAL_BYTES-total_bytes)
                total_bytes += size
            except OSError:
                log_exception('capture_document', claim_id=manifest.claim_id, document_id=item.document_id)
                captured = None
                sha256 = None
            sources.append(captured)
            reference['sha256'] = sha256
        tools = extraction_tools()
        request.update(parser_version=PARSER_VERSION, schema_version=SCHEMA_VERSION,
                       config_version=CONFIG_VERSION, tools=tools)
        if any(source is None for source in sources):
            request['unavailable_source_attempt'] = uuid.uuid4().hex
        store = LocalStore(output_root)
        return store.run(request, lambda directory: _extract_claim(manifest, directory, sources, tools), retry=retry)


def _extract_claim(manifest: Manifest, run_dir: Path, sources, tools):
    from .validation import validate_report

    documents = []
    for index, item in enumerate(manifest.documents):
        try:
            if sources[index] is None:
                raise OSError('Source unavailable when request was captured')
            with failure_context(claim_id=manifest.claim_id):
                doc = extract_pdf(item, run_dir / f'document-{index+1}', captured_path=sources[index], tools=tools)
        except OSError as error:
            log_exception('extract_document', claim_id=manifest.claim_id, document_id=item.document_id)
            doc = Document(document_id=item.document_id, kind=item.kind, sha256=None, original_path=None,
                           source={**item.source, 'requested_local_path': item.path},
                           errors=[f'source_read_failed:{type(error).__name__}'])
        try:
            documents.append(PARSERS[item.kind](doc))
        except Exception as error:
            log_exception('parse_document', claim_id=manifest.claim_id, document_id=item.document_id)
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
        'schema_version': SCHEMA_VERSION, 'config_version': CONFIG_VERSION, 'claim_id': manifest.claim_id,
        'created_at': datetime.now(timezone.utc).isoformat(), 'parser_version': PARSER_VERSION,
        'documents': [result.model_dump(mode='json') for result in documents],
        'requested_insurer_reimbursement': request.model_dump(mode='json'),
        'effective_on': manifest.effective_on,
        'review_regions': [region.model_dump(mode='json') for region in manifest.review_regions],
        'effective_monthly_rent': effective_rent(documents, manifest.effective_on).model_dump(mode='json'),
        'validation': validate_report(documents, review_regions=[region.model_dump(mode='json')
                                                                for region in manifest.review_regions]),
        'training_eligible': False, 'review_required': True,
        'limitations': ['Development extraction; no adjudication or independently validated accuracy.',
                        'Missing states describe supported-pattern search scope, not absence of evidence everywhere.',
                        'Confirmation labels are source assertions, not independent verification.',
                        'Receipt before decision is unverified. No model provider selected.'],
    }
    report_path = run_dir / 'report.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    return report_path

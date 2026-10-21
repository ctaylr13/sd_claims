"""Local OCR evidence for visual review; never accepted waiver facts."""

import hashlib
import json
from pathlib import Path

from .facts import citation
from .models import DocumentInput
from .pdf import extract_pdf
from .program_verification import read_program_document


def review_program_images(source: DocumentInput, expected_sha256: str, output: Path):
    readings = read_program_document(source, expected_sha256)
    pages = [number for number, page in readings['pages'].items() if page['review_reason']]
    content = Path(source.path).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError('Program source changed')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    evidence = []
    if pages:
        request = source.model_copy(update={'force_ocr_pages': sorted(set(source.force_ocr_pages) | set(pages))})
        document = extract_pdf(request, output / 'source', content=content)
        for page in document.pages:
            if page.number not in pages:
                continue
            evidence.append({'page': page.number, 'status': 'needs_review', 'image_path': page.image_path,
                'reason': readings['pages'][page.number]['review_reason'],
                'citation': citation(document, page, page.words).model_dump(mode='json') if page.words else None,
                'errors': page.errors, 'ocr_word_count': len(page.words),
                'limitations': ['OCR may omit or misalign values; visual review required',
                                'No election, field assignment, enrollment or coverage inferred']})
    result = {'document_id': source.document_id, 'sha256': expected_sha256, 'review_pages': evidence,
              'source_readings': readings, 'accepted_facts': [],
              'prediction_eligible': False, 'training_eligible': False}
    (output / 'review.json').write_text(json.dumps(result, indent=2) + '\n')
    for path in output.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    return result

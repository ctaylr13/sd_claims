import argparse
from pathlib import Path

from backend.storage import JobInProgress, RetryRequired

from . import extract_claim
from .models import Manifest


def main():
    parser = argparse.ArgumentParser(
        description='Extract cited claim facts from a local PDF manifest',
        epilog='Manifest: claim_id, documents [{path, document_id, kind}], optional effective_on (YYYY-MM-DD). '
               'Kinds: ledger, move_out_statement, lease, renewal. Paths are relative to the manifest. '
               'Optional recorded_requests: [{source_id, source_version, row, column: "Amount of Claim", quote}]. '
               'Install requirements-extraction.txt; OCR also needs Tesseract with English language data.',
    )
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, default=Path('cache/extraction'))
    parser.add_argument('--retry', action='store_true', help='Retry failed or interrupted local work')
    args = parser.parse_args()
    manifest = Manifest.model_validate_json(args.manifest.read_text())
    for item in manifest.documents:
        item.path = str((args.manifest.resolve().parent / item.path).resolve())
    try:
        print(extract_claim(manifest, args.output, retry=args.retry))
    except (JobInProgress, RetryRequired) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()

import hashlib
from pathlib import Path

MAX_DOCUMENTS = 20
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class ExtractionLimitExceeded(ValueError):
    pass


def capture_pdf(source: Path, destination: Path, *, remaining_bytes=MAX_TOTAL_BYTES):
    size = 0
    digest = hashlib.sha256()
    with source.open('rb') as incoming, destination.open('xb') as outgoing:
        while chunk := incoming.read(CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ExtractionLimitExceeded('Each PDF must be at most 50 MiB')
            if size > remaining_bytes:
                raise ExtractionLimitExceeded('Combined PDFs must be at most 200 MiB per claim')
            outgoing.write(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size

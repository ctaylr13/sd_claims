import csv
import hashlib
import io
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

import pdfplumber

from backend.diagnostics import log_exception

from .capture import MAX_FILE_BYTES, ExtractionLimitExceeded, capture_pdf
from .models import Document, DocumentInput, Page, Word


def text_usable(words):
    text = ' '.join(word.text for word in words)
    return (sum(char.isalnum() for char in text) >= 20
            and text.count('\ufffd') / max(len(text), 1) < 0.05
            and '(cid:' not in text)


def native_words(page):
    x0, y0 = page.bbox[:2]
    return [Word(text=word['text'], bbox=(word['x0']-x0, word['top']-y0, word['x1']-x0, word['bottom']-y0))
            for word in page.dedupe_chars().extract_words(x_tolerance=2, y_tolerance=3)]


def fallback_reason(page, words):
    if not text_usable(words):
        return 'image_only_or_unusable_native_text'
    image_area = sum(image['width']*image['height'] for image in page.images)
    character_count = sum(char.isalnum() for word in words for char in word.text)
    if image_area > page.width*page.height*0.65 and character_count < 200:
        return 'image_dominant_page_with_sparse_native_text'
    return None


def ocr_words(image_path, width, height, image_size, *, executable='tesseract'):
    result = subprocess.run([executable, str(image_path), 'stdout', '-l', 'eng', '--psm', '3', 'tsv'],
                            capture_output=True, text=True, check=True, timeout=120)
    words = []
    sx, sy = width / image_size[0], height / image_size[1]
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter='\t', quoting=csv.QUOTE_NONE):
        if row['level'] != '5' or not row['text'].strip():
            continue
        x, y, w, h = (int(row[key]) for key in ('left', 'top', 'width', 'height'))
        words.append(Word(text=row['text'], bbox=(x*sx, y*sy, (x+w)*sx, (y+h)*sy),
                          confidence=float(row['conf'])))
    return words


def extraction_tools():
    tools = {name: version(name) for name in ('pdfplumber', 'pypdfium2')}
    if shutil.which('tesseract'):
        try:
            tools['tesseract'] = subprocess.run(['tesseract', '--version'], capture_output=True,
                                                 text=True, check=True, timeout=10).stdout.splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            tools['tesseract'] = 'version_unavailable'
    return tools


def extract_pdf(item: DocumentInput, artifact_dir: Path, *, ocr=ocr_words, content=None,
                captured_path=None, tools=None):
    if content is not None and len(content) > MAX_FILE_BYTES:
        raise ExtractionLimitExceeded('Each PDF must be at most 50 MiB')
    artifact_dir.mkdir(parents=True, exist_ok=False)
    original = artifact_dir / 'original.pdf'
    if content is None:
        digest, _ = capture_pdf(Path(captured_path) if captured_path is not None else Path(item.path), original)
    else:
        original.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
    doc = Document(document_id=item.document_id, kind=item.kind, sha256=digest,
                   original_path=str(original.resolve()), source=item.source,
                   tools=extraction_tools() if tools is None else tools)
    try:
        with pdfplumber.open(original) as pdf:
            if any(number > len(pdf.pages) for number in item.force_ocr_pages):
                doc.errors.append('force_ocr_page_out_of_range')
            for index, source_page in enumerate(pdf.pages, 1):
                page = Page(number=index, width=source_page.width, height=source_page.height,
                            method='native_text', state='readable')
                doc.pages.append(page)
                try:
                    page.words = native_words(source_page)
                    page.native_text = ' '.join(word.text for word in page.words)
                except Exception as error:
                    log_exception('native_text_failed', document_id=item.document_id, page_number=index)
                    page.errors.append(f'native_text_failed:{type(error).__name__}')
                try:
                    image = source_page.to_image(resolution=200, force_mediabox=True).original
                    image_path = artifact_dir / f'page-{index}.png'
                    image.save(image_path)
                    page.image_path = str(image_path.resolve())
                    page.image_size = image.size
                    page.image_to_page_scale = (page.width/image.width, page.height/image.height)
                except Exception as error:
                    log_exception('render_failed', document_id=item.document_id, page_number=index)
                    page.errors.append(f'render_failed:{type(error).__name__}')
                reason = fallback_reason(source_page, page.words)
                if index in item.force_ocr_pages or reason:
                    page.fallback_reason = ('explicit_unusable_text' if index in item.force_ocr_pages
                                            else reason)
                    page.method = 'ocr'
                    page.native_words = page.words
                    page.words = []
                    if page.image_path:
                        try:
                            page.words = ocr(Path(page.image_path), page.width, page.height, page.image_size)
                        except Exception as error:
                            log_exception('ocr_failed', document_id=item.document_id, page_number=index)
                            page.errors.append(f'ocr_failed:{type(error).__name__}')
                    if not text_usable(page.words):
                        page.state = 'unreadable'
                    else:
                        page.state = 'partial'
                elif page.errors:
                    page.state = 'partial'
                if page.method == 'native_text':
                    try:
                        x0, y0 = source_page.bbox[:2]
                        page.table_cells = [(cell[0]-x0, cell[1]-y0, cell[2]-x0, cell[3]-y0)
                                            for table in source_page.find_tables() for cell in table.cells]
                    except Exception as error:
                        log_exception('table_geometry_failed', document_id=item.document_id, page_number=index)
                        page.errors.append(f'table_geometry_failed:{type(error).__name__}')
                        page.state = 'partial'
    except Exception as error:
        log_exception('pdf_read_failed', document_id=item.document_id)
        doc.errors.append(f'pdf_read_failed:{type(error).__name__}')
    return doc

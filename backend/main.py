import json
from pathlib import Path
from threading import Lock
from typing import Annotated

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from .batches import BatchProgress, BatchRequest, create_batch, run_batch
from .claims import LookupError, SOURCE_UNAVAILABLE_MESSAGE, lookup_claim, lookup_claims
from .drive import REPO_ROOT, connect_drive, load_sources
from .schemas import BatchLookupRequest, BatchLookupResponse, ClaimResponse, ErrorResponse
from .storage import LocalStore
from .review import batch_recommendations, claim_recommendation, evaluation_metrics

load_dotenv(REPO_ROOT / '.env')

app = FastAPI(title='Claims lookup API', version='0.1.0')
authorization_lock = Lock()


def get_store():
    return LocalStore(REPO_ROOT / 'cache/extraction')


def get_drive():
    try:
        sources = load_sources()
        with authorization_lock:
            service = connect_drive(REPO_ROOT / 'credentials/drive-oauth-client.json',
                                    REPO_ROOT / 'secrets/drive-token.json', allow_interactive=False)
    except Exception:
        raise LookupError('source_unavailable', 502, SOURCE_UNAVAILABLE_MESSAGE) from None
    try:
        yield service, sources
    finally:
        service.close()


@app.middleware('http')
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def error_response(error, headers=None):
    return JSONResponse(status_code=error.status,
                        content={'error': {'code': error.code, 'message': str(error)}},
                        headers=headers)


@app.exception_handler(LookupError)
async def lookup_error(request: Request, error: LookupError):
    return error_response(error)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return error_response(LookupError('invalid_request', 422,
                                     'Request does not match the endpoint schema; see /docs'))


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    code = 'method_not_allowed' if error.status_code == 405 else 'route_not_found'
    return error_response(LookupError(code, error.status_code, str(error.detail)), error.headers)


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.post('/api/batches', response_model=BatchProgress, status_code=202)
def submit_batch(body: BatchRequest, background: BackgroundTasks,
                 store: Annotated[LocalStore, Depends(get_store)]):
    progress = create_batch(store, body)
    background.add_task(run_batch, progress['batch_id'], body, store.root)
    return progress


def require_batch(store, batch_id):
    progress = store.batch(batch_id)
    if progress is None:
        raise LookupError('batch_not_found', 404, 'Batch not found')
    return progress


@app.get('/api/batches/{batch_id}', response_model=BatchProgress)
def batch_progress(batch_id: str, store: Annotated[LocalStore, Depends(get_store)]):
    return require_batch(store, batch_id)


def require_result(batch_id, index, store):
    progress = require_batch(store, batch_id)
    if not 0 <= index < len(progress['results']):
        raise LookupError('claim_not_found', 404, 'Batch claim index not found')
    result = progress['results'][index]
    if result['state'] != 'succeeded':
        raise LookupError('result_unavailable', 409, 'Claim has no completed result')
    path = store.result_for_attempt(result['attempt'])
    if path is None:
        raise LookupError('result_unavailable', 409, 'Result artifacts require an explicit retry')
    return path


@app.get('/api/batches/{batch_id}/claims/{index}/result')
def batch_result(batch_id: str, index: int, store: Annotated[LocalStore, Depends(get_store)]):
    return json.loads(require_result(batch_id, index, store).read_text())


@app.post('/api/claims/lookup', response_model=BatchLookupResponse,
          responses={422: {'model': ErrorResponse}, 502: {'model': ErrorResponse}})
def batch_lookup(body: BatchLookupRequest, drive: Annotated[tuple, Depends(get_drive)]):
    results = lookup_claims(*drive, body.tracking_numbers)
    found = sum(result['state'] == 'found' for result in results)
    return {'results': results, 'total': len(results), 'found': found, 'failed': len(results) - found}


@app.get('/api/claims/{tracking_number}', response_model=ClaimResponse,
         responses={status: {'model': ErrorResponse} for status in (400, 404, 409, 502)})
def claim_lookup(tracking_number: str, drive: Annotated[tuple, Depends(get_drive)]):
    try:
        return lookup_claim(*drive, tracking_number)
    except LookupError:
        raise
    except Exception:
        raise LookupError('source_unavailable', 502, SOURCE_UNAVAILABLE_MESSAGE) from None


@app.post('/api/claims/{tracking_number}/recommendation')
def recommendation(tracking_number: str, drive: Annotated[tuple, Depends(get_drive)]):
    return claim_recommendation(lookup_claim(*drive, tracking_number))


@app.get('/api/review/metrics')
def metrics():
    return evaluation_metrics()


def retained_document(batch_id, index, document_index, store, page_number=None):
    result = require_result(batch_id, index, store)
    report = json.loads(result.read_text())
    documents = report.get('documents', [])
    if not 0 <= document_index < len(documents):
        raise LookupError('document_not_found', 404, 'Document not found')
    document = documents[document_index]['document']
    original = document.get('original_path')
    if page_number is not None:
        page = next((page for page in document.get('pages', []) if page['number'] == page_number), {})
        original = page.get('image_path')
    path = Path(original).resolve() if original else None
    if path is None or not path.is_relative_to(result.parent.resolve()) or not path.is_file():
        raise LookupError('document_not_found', 404, 'Retained document or page unavailable')
    return path


@app.get('/api/batches/{batch_id}/claims/{index}/documents/{document_index}')
def batch_document(batch_id: str, index: int, document_index: int,
                   store: Annotated[LocalStore, Depends(get_store)]):
    return FileResponse(retained_document(batch_id, index, document_index, store),
                        media_type='application/pdf', headers={'Content-Disposition': 'inline'})


@app.get('/api/batches/{batch_id}/claims/{index}/documents/{document_index}/pages/{page_number}')
def batch_page(batch_id: str, index: int, document_index: int, page_number: int,
               store: Annotated[LocalStore, Depends(get_store)]):
    return FileResponse(retained_document(batch_id, index, document_index, store, page_number),
                        media_type='image/png')


@app.post('/api/claims/recommendations')
def recommend_batch(body: BatchLookupRequest, drive: Annotated[tuple, Depends(get_drive)]):
    return batch_recommendations(lookup_claims(*drive, body.tracking_numbers))

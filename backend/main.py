from threading import Lock
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .claims import LookupError, SOURCE_UNAVAILABLE_MESSAGE, lookup_claim, lookup_claims
from .drive import REPO_ROOT, connect_drive, load_sources
from .schemas import BatchLookupRequest, BatchLookupResponse, ClaimResponse, ErrorResponse

app = FastAPI(title='Claims lookup API', version='0.1.0')
authorization_lock = Lock()


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
                                     'Supply tracking_numbers as 1–100 strings or a comma-separated list'))


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    code = 'method_not_allowed' if error.status_code == 405 else 'route_not_found'
    return error_response(LookupError(code, error.status_code, str(error.detail)), error.headers)


@app.get('/api/health')
def health():
    return {'status': 'ok'}


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

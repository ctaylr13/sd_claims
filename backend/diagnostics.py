import logging
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)
_context = ContextVar('failure_context', default={})


@contextmanager
def failure_context(**identifiers):
    token = _context.set({**_context.get(), **identifiers})
    try:
        yield
    finally:
        _context.reset(token)


def log_exception(operation, **identifiers):
    context = {'claim_id': None, 'batch_id': None, **_context.get(), **identifiers}
    logger.exception('operation=%s context=%s', operation, context,
                     extra={'operation': operation, **context})

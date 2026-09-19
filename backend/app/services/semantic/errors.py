"""
Error taxonomy for the shared semantic-judge chain.

TRANSIENT   -- worth retrying the SAME provider (timeout, 429, 5xx, network).
PERMANENT   -- retrying the same provider cannot help (bad key, bad request,
               unknown/missing model); move to the next provider immediately.
UNSUPPORTED -- this provider does not expose the requested operation (e.g.
               JEV has no summary generation); skipped, not a failure.
"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional


class ErrorKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNSUPPORTED = "unsupported"


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, detail: str, *, provider: Optional[str] = None):
        super().__init__(f"{provider or '?'}: {kind.value}: {detail}")
        self.kind = kind
        self.detail = detail
        self.provider = provider


class SemanticJudgmentUnavailable(Exception):
    """Every provider in the chain failed. Callers MUST treat this as "no
    semantic judgment exists" -- never substitute a heuristic verdict."""

    def __init__(self, message: str = "all semantic providers failed", *, attempts: Optional[list] = None):
        super().__init__(message)
        self.attempts = attempts or []


_TRANSIENT_STATUS = {408, 409, 425, 429}
_TRANSIENT_NAMES = {
    "RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError",
    "ReadTimeout", "ConnectTimeout", "ConnectError", "ReadError", "RemoteProtocolError",
    "TimeoutException", "TransportError", "ServiceUnavailable",
}
_PERMANENT_NAMES = {
    "AuthenticationError", "PermissionDeniedError", "BadRequestError", "NotFoundError",
    "UnprocessableEntityError", "ModelNotFound",
}


def classify_exception(exc: BaseException) -> ErrorKind:
    """Map any provider/library exception to an ErrorKind. Unknown errors are
    TRANSIENT (one bounded retry, then the next provider) -- never silently
    PERMANENT, which would hide a recoverable outage."""
    if isinstance(exc, ProviderError):
        return exc.kind
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return ErrorKind.TRANSIENT
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        if status in _TRANSIENT_STATUS or status >= 500:
            return ErrorKind.TRANSIENT
        if 400 <= status < 500:
            return ErrorKind.PERMANENT
    for cls in type(exc).__mro__:
        if cls.__name__ in _PERMANENT_NAMES:
            return ErrorKind.PERMANENT
        if cls.__name__ in _TRANSIENT_NAMES:
            return ErrorKind.TRANSIENT
    return ErrorKind.TRANSIENT

"""Offline unit tests for the durable-run retry classification (§33)."""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.execution.durable_run import (
    NON_RETRYABLE_ERROR_CLASSES,
    RETRYABLE_ERROR_CLASSES,
    _is_retryable,
    classify_error,
)


class _Timeout(Exception):
    pass


class TransientBackendError(Exception):
    pass


@pytest.mark.parametrize("exc, expected", [
    (asyncio.TimeoutError(), "timeout"),
    (_Timeout("read timed out"), "timeout"),
    (ConnectionResetError("connection reset by peer"), "network"),
    (OSError("Name or service not known (dns)"), "network"),
    (RuntimeError("429 rate limit exceeded"), "rate_limit"),
    (TransientBackendError("temporary blip"), "transient"),
    (PermissionError("forbidden"), "auth"),
    (KeyError("row not found / missing"), "not_found"),
    (ValueError("schema validation failed"), "validation"),
    (RuntimeError("something structurally wrong"), "logic"),
])
def test_classify_error(exc, expected):
    assert classify_error(exc) == expected


def test_retryable_partition_is_disjoint_and_covers_the_classifier():
    assert RETRYABLE_ERROR_CLASSES.isdisjoint(NON_RETRYABLE_ERROR_CLASSES)
    assert _is_retryable("timeout") and _is_retryable("network") and _is_retryable("rate_limit")
    assert not _is_retryable("validation")
    assert not _is_retryable("logic")   # unknown -> conservative, not retried
    assert not _is_retryable(None)


def test_serialization_error_is_conflict_and_retryable():
    e = asyncpg.exceptions.SerializationError("could not serialize access")
    assert classify_error(e) == "conflict"
    assert _is_retryable("conflict")

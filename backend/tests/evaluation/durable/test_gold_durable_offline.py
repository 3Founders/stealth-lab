"""
Offline gold-labeled coverage for durable retry/resume policy (task spec
§6-§7), extending -- not duplicating -- the product's own
tests/test_durable_run_offline.py / test_durable_resume_offline.py. No DB,
no network.

This suite's own angle: a labeled classification matrix over
classify_error() (this suite's convention for a policy function with a
known-correct answer per input, matching e.g.
applicability/test_gold_applicability_offline.py's shape), plus structural
invariants over the RETRYABLE/NON_RETRYABLE partition that the E2E layer
depends on holding.
"""
from __future__ import annotations

import pytest

from app.execution.durable_run import (
    NON_RETRYABLE_ERROR_CLASSES,
    RETRYABLE_ERROR_CLASSES,
    _is_retryable,
    classify_error,
)


class TransientError(Exception):
    """classify_error's 'transient'/'temporar' branch matches the
    exception's TYPE NAME only, unlike timeout/network which also match
    the message -- a real asymmetry in the implementation, not a typo in
    this gold case. A message-only 'transient failure, try again' on a
    plain RuntimeError does NOT classify as transient; only the type name
    does."""


# (exception, expected error_class) -- a real gold-labeled matrix over the
# actual classify_error() implementation, not a re-statement of its source.
_CASES: list[tuple[BaseException, str]] = [
    (TimeoutError("request timed out"), "timeout"),
    (ConnectionError("connection reset by peer"), "network"),
    (RuntimeError("rate limit exceeded, back off"), "rate_limit"),
    (TransientError("try again shortly"), "transient"),
    (PermissionError("unauthorized: forbidden"), "auth"),
    (LookupError("resource does not exist"), "not_found"),
    (ValueError("schema validation failed"), "validation"),
    (TypeError("wrong argument type"), "validation"),
    (AssertionError("invariant violated"), "validation"),
    (RuntimeError("the disk caught fire"), "logic"),  # genuinely unrecognized -> safe default
]


@pytest.mark.parametrize("exc,expected", _CASES, ids=[c[1] for c in _CASES])
def test_gold_classify_error_matrix(exc, expected):
    assert classify_error(exc) == expected


def test_retryable_and_non_retryable_partitions_are_disjoint():
    assert RETRYABLE_ERROR_CLASSES.isdisjoint(NON_RETRYABLE_ERROR_CLASSES)


def test_unknown_failure_class_logic_is_non_retryable_by_default():
    # "logic" is classify_error's catch-all for anything it doesn't
    # recognize (test_gold_classify_error_matrix's last case) -- proving it
    # is NOT retryable is the policy-level guarantee that an unrecognized
    # failure mode never gets auto-retried into a false sense of resilience.
    assert "logic" in NON_RETRYABLE_ERROR_CLASSES
    assert _is_retryable("logic") is False


@pytest.mark.parametrize("ec", sorted(RETRYABLE_ERROR_CLASSES))
def test_every_retryable_class_is_retryable(ec):
    assert _is_retryable(ec) is True


@pytest.mark.parametrize("ec", sorted(NON_RETRYABLE_ERROR_CLASSES) + [None, "totally-unknown-class"])
def test_every_non_retryable_or_unmodeled_class_is_not_retryable(ec):
    assert _is_retryable(ec) is False

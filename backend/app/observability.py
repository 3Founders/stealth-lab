"""Error and performance reporting, off by default, redacted on the way out.

WHY THIS EXISTS: PRODUCTION_READINESS.md's "What does not exist at all"
names observability first -- "If the hosted instance errors or goes down,
nothing tells anyone." That was not hypothetical: 32 ingestion jobs failed
silently on 2026-08-28 and were only found by reading the job table by
hand, and 28 more failed the same way an hour later.

THREE ENTRY POINTS, not one. This project serves two separate ASGI apps
plus a headless worker, and instrumenting only the obvious one would leave
the other two dark:
    app/main.py                -> FastAPI, the /v1 routers      ("api")
    app/mcp_server/server.py   -> streamable_http_app, 9 tools  ("mcp")
    scripts/run_ingestion.py   -> no HTTP at all                ("worker")
`component` tags every event so the three are separable in one project.

REDACTION IS THE LOAD-BEARING PART. trace_redaction.py declares
NEVER_SEND_EXTERNALLY_BY_DEFAULT = True, whose own comment says "callers
integrating an external LLM path must check this explicitly; this module
does not and cannot enforce it by itself". Sentry IS an external path --
arguably the most dangerous one in the repo, because an exception's local
variables and request body routinely contain the exact tool_input /
tool_output payloads the collector spent effort redacting. So:

  - send_default_pii is False, but that alone is NOT sufficient: it governs
    headers/cookies/user identity, not an application dict that happens to
    hold a transcript.
  - every outbound event is walked through trace_redaction.redact_value(),
    the SAME recursive scrubber the ingest chokepoint uses. One redaction
    implementation, one place to fix, same discipline as everywhere else
    in this repo.
  - a handful of keys are dropped wholesale rather than scrubbed, because
    a partial match on a 32KB tool payload is not worth the residual risk.

OFF BY DEFAULT: no SENTRY_DSN means init() returns immediately and nothing
is imported, sent, or patched. Local work, offline tests, and a clean
clone are unaffected -- same Optional-secret convention as config.py.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import get_settings
from app.services.trace_redaction import (
    NEVER_SEND_EXTERNALLY_BY_DEFAULT,
    redact_value,
)

log = logging.getLogger(__name__)

#: Keys whose values are dropped entirely rather than pattern-scrubbed.
#: These routinely carry whole tool payloads or raw transcript text, where
#: a missed pattern is a real leak and the diagnostic value of the content
#: is low (the exception type and stack are what actually help).
DROP_KEYS_ENTIRELY = frozenset({
    "tool_input", "tool_output", "tool_response", "raw_payload",
    "content", "message_content", "transcript", "statement",
    "capability_statement", "goal_text", "label",
})

_REDACTED = "[dropped before send: may contain trace content]"

#: Set once by init(); read by tests and by callers that want to know
#: whether reporting is actually live rather than assuming it.
_enabled = False


def _scrub(event: dict, hint: Optional[dict] = None) -> Optional[dict]:
    """Sentry before_send hook: redact, then drop the highest-risk keys.

    Returns the event (never None) -- dropping the event entirely would
    trade a leak risk for total blindness, which is the failure mode this
    module exists to remove. If scrubbing itself raises, the event is
    dropped rather than sent unscrubbed: failing closed is the only safe
    direction here.
    """
    try:
        scrubbed = redact_value(event, [])
        return _drop_risky_keys(scrubbed)
    except Exception:  # noqa: BLE001 -- deliberately broad, see docstring
        log.exception("observability: scrubbing failed; dropping event unsent")
        return None


def _drop_risky_keys(value: Any) -> Any:
    """Recursively replace DROP_KEYS_ENTIRELY values, at any depth."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k in DROP_KEYS_ENTIRELY else _drop_risky_keys(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_drop_risky_keys(v) for v in value]
    return value


def init(component: str) -> bool:
    """Initialise reporting for one component. Returns whether it is live.

    Idempotent and safe to call from every entry point; a missing DSN is a
    normal state, not an error.
    """
    global _enabled

    settings = get_settings()
    dsn = settings.sentry_dsn
    if not dsn:
        log.debug("observability: no SENTRY_DSN, reporting disabled (%s)", component)
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncpg import AsyncPGIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        # requirements.txt declares it, but a partial install must not take
        # the server down -- reporting is a diagnostic, never a dependency
        # of serving traffic.
        log.warning("observability: SENTRY_DSN set but sentry_sdk not installed")
        return False

    # Explicitly acknowledged rather than assumed: this module is an
    # external send path, which is exactly what that constant is about.
    assert NEVER_SEND_EXTERNALLY_BY_DEFAULT, (
        "trace_redaction lowered its never-send-externally posture; "
        "re-review this module before shipping events off-box"
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment,
        release=settings.release,
        server_name=component,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        before_send=_scrub,
        before_send_transaction=_scrub,
        integrations=[StarletteIntegration(), AsyncPGIntegration()],
    )
    sentry_sdk.set_tag("component", component)
    _enabled = True
    log.info("observability: reporting enabled (component=%s)", component)
    return True


def is_enabled() -> bool:
    return _enabled

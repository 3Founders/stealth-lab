"""
Client-side redaction for trace ingestion (ticket 18, memory-substrate
map). Runs at the collector, before any network transmission -- per
ticket 18's own answer, this is a best-effort floor for *detected*
patterns, not a guarantee. It never operates on raw serialized text:
walks the already-parsed JSON structure and substitutes only within
string leaf values, so a match can never produce invalid JSON (a raw-text
regex substitution risks exactly that, per ticket 18's own correction).

HONEST LIMIT, stated in-code, not just in the ticket: no pattern set here
catches every real secret shape. Ticket 18 cites Gitleaks' own
documentation making the same admission. This is why local-only stays
the hard default regardless of what this module catches -- see
NEVER_SEND_EXTERNALLY_BY_DEFAULT below, not a comment, a real constant
callers must not silently work around.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

# Real, if not exhaustive, known-token patterns -- layer 1 of ticket 18's
# three-layer design. Each tuple is (name, compiled pattern). Ordered
# roughly by specificity so a more specific match wins when spans overlap.
KNOWN_TOKEN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("stripe_key", re.compile(r"sk_(live|test)_[A-Za-z0-9]{16,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9-]{20,}")),
    ("generic_bearer", re.compile(r"[Bb]earer\s+[A-Za-z0-9._-]{20,}")),
    ("private_key_block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    )),
]

# Layer 2: path-based rules. A tool call reading any of these is treated
# as sensitive by content regardless of what pattern-matching finds --
# ticket 18's own example (a private key's raw bytes don't match any
# known-prefix pattern at all, but its filename is a reliable signal).
SENSITIVE_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"(^|/)\.env($|\.[\w.]+$)"),
    re.compile(r"\.pem$"),
    re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$"),
    re.compile(r"\.key$"),
    re.compile(r"\.pfx$"),
    re.compile(r"(^|/)\.ssh/"),
    re.compile(r"(^|/)\.aws/credentials$"),
]

PATH_REDACTION_PLACEHOLDER = "[EXCLUDED: sensitive path]"

# Real, load-bearing constant, not a comment (ticket 18, Grill 2): nothing
# in this module's own successful redaction should be read by a caller as
# license to send raw content externally. Callers integrating an external
# LLM path must check this explicitly; this module does not and cannot
# enforce it by itself, and does not claim to.
NEVER_SEND_EXTERNALLY_BY_DEFAULT = True


def _redact_string(value: str) -> tuple[str, list[str]]:
    """Substitutes every known-token match within one string value.
    Returns (possibly-modified string, list of pattern names matched)."""
    matched: list[str] = []
    for name, pattern in KNOWN_TOKEN_PATTERNS:
        def _sub(m: re.Match, _name=name) -> str:
            matched.append(_name)
            return f"[REDACTED:{_name}]"
        value = pattern.sub(_sub, value)
    return value, matched


def _is_sensitive_path(value: str) -> bool:
    # PurePosixPath normalizes separators without touching the filesystem
    # -- this never opens or stats the path, just inspects the string.
    candidate = str(PurePosixPath(value))
    return any(p.search(candidate) for p in SENSITIVE_PATH_PATTERNS)


def redact_value(value: Any, matched_patterns: list[str]) -> Any:
    """
    Recursively walks a parsed JSON value (dict / list / str / other),
    substituting matched known-token spans within string leaves only.
    Does NOT apply the path-exclusion rule -- that is cross-field (see
    redact_event's real bug fix below) and cannot be decided correctly
    by looking at one leaf in isolation.
    """
    if isinstance(value, str):
        redacted, names = _redact_string(value)
        matched_patterns.extend(names)
        return redacted
    if isinstance(value, dict):
        return {k: redact_value(v, matched_patterns) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, matched_patterns) for v in value]
    return value


def _find_sensitive_path(value: Any) -> bool:
    """True if any string leaf anywhere in `value` looks like a
    sensitive path. Used only against tool_input, to decide whether
    tool_output must be excluded wholesale."""
    if isinstance(value, str):
        return _is_sensitive_path(value)
    if isinstance(value, dict):
        return any(_find_sensitive_path(v) for v in value.values())
    if isinstance(value, list):
        return any(_find_sensitive_path(v) for v in value)
    return False


def redact_event(event: dict) -> dict:
    """
    Redacts one already-parsed trace event dict in place (returns a new
    dict; does not mutate the input). Only tool_input/tool_output are
    redacted -- per ticket 18, redaction targets content, not the event's
    own structural/identity fields (event_type, timestamp, actor_id, etc),
    which are never secret-shaped and redacting them would corrupt
    episode assembly for no safety benefit.

    REAL BUG FOUND AND FIXED while testing this (not hypothetical): the
    first version checked each string leaf for "is this string itself a
    sensitive path" independently, which correctly redacted
    tool_input.file_path (a path string) but left tool_output.content (the
    actual file contents, an unrelated string that doesn't look like a
    path) completely untouched -- exactly the case ticket 18's own answer
    names explicitly ("a Read of .env... is sensitive by content
    regardless of what pattern-matching finds"). Fixed by checking
    tool_input for a sensitive path FIRST, and if found, excluding
    tool_output wholesale -- a cross-field decision a per-leaf check
    cannot make correctly on its own.

    Returns the redacted event plus a `_redaction` metadata key recording
    which pattern names fired -- real, auditable signal for whether
    redaction did anything, not a silent transform.
    """
    matched: list[str] = []
    result = dict(event)

    path_excluded = "tool_input" in result and _find_sensitive_path(result["tool_input"])

    if path_excluded:
        matched.append("sensitive_path")
        # Wholesale, not just the path field itself: other tool_input
        # params (e.g. an Edit call's old_string/new_string) are content
        # FROM the same sensitive file, not independent of it.
        if "tool_input" in result:
            result["tool_input"] = PATH_REDACTION_PLACEHOLDER
        if "tool_output" in result:
            result["tool_output"] = PATH_REDACTION_PLACEHOLDER
    else:
        if "tool_input" in result and result["tool_input"] is not None:
            result["tool_input"] = redact_value(result["tool_input"], matched)
        if "tool_output" in result and result["tool_output"] is not None:
            result["tool_output"] = redact_value(result["tool_output"], matched)

    if matched:
        result["_redaction"] = {"patterns_matched": sorted(set(matched))}
    return result

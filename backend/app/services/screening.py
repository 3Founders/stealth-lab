"""
Ingestion-time security / policy screening -- the persisted, auditable
ALLOW / QUARANTINE / REJECT record (V4-hardening Part II-A §5 / Gate G3 /
audit B14).

WHY THIS EXISTS
    Injection screening already runs at ingestion time
    (`skill_ingestion.py::_screen_untrusted_document`) but its ONLY effect
    is a provenance downgrade to 'system_pending_review'. There is NO row
    anywhere recording that a screen ran, what it decided, which detector
    decided it, at what version, over what content, and why. §5 requires
    exactly that: "Persist ALLOW / QUARANTINE / REJECT with detector/
    version/reason. Rejected material is auditable. Do not silently delete
    rejected material."

WHAT THIS IS -- AND IS NOT
    This is a DETECTION + RECORD layer, not a full DLP framework. It does
    not reinvent injection or secret detection: it COMPOSES the regex
    detectors that already exist and are already tested elsewhere --

      - `_META_DIRECTIVE_RE` / `_TRUST_ASSERTION_RE`
        (app.services.skill_ingestion) -- prompt-injection and
        trust-escalation, imported verbatim.
      - `KNOWN_TOKEN_PATTERNS` (app.services.trace_redaction) -- the
        known-secret-token catalogue, imported verbatim.
      - a credential key/value regex copied from
        `publication.py::_residual_secret_signals` (that function is
        module-private and takes a dict of already-scrubbed fields, so it
        is not directly reusable here; its regex literal is reused with
        attribution).

    A REJECT decision does NOT delete anything. `screen_document_text` and
    `decide` are pure; `record_screening_run` writes the audit rows. What
    to actually do with a QUARANTINE / REJECT (drop the artifact, route it
    to human review, downgrade provenance) is the caller's decision -- the
    material stays auditable either way.

SCOPE LIMIT (stated in-code, not just here)
    The pattern sets are best-effort floors for DETECTED shapes, the same
    honest limit `trace_redaction`'s own module docstring states. `pii`,
    `license`, `malicious_executable` and `source_trust` are declared
    check types (the DB CHECK lists them, callers may record them) but
    `screen_document_text` does not yet implement detectors for them.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import asyncpg

from app.services.access import TenantScope, tenant_transaction
from app.services.skill_ingestion import _META_DIRECTIVE_RE, _TRUST_ASSERTION_RE
from app.services.trace_redaction import KNOWN_TOKEN_PATTERNS
from app.utils.ids import uuid7

# ---------------------------------------------------------------------------
# Exported vocabulary
# ---------------------------------------------------------------------------
DECISIONS: tuple[str, ...] = ("ALLOW", "QUARANTINE", "REJECT")

# TEXT, not an enum -- this list will grow (see migration 54's header).
# Kept 1:1 with `check_type_chk_screening_decisions` in
# db/53_screening_decisions.sql.
CHECK_TYPES: tuple[str, ...] = (
    "prompt_injection",
    "trust_escalation",
    "secret_exposure",
    "pii",
    "license",
    "malicious_executable",
    "source_trust",
    "unsafe_locator",
)

SCREENING_DETECTOR_VERSION = "screening@v1"

# The detector id every `screen_document_text` finding carries -- it reuses
# skill_ingestion's own injection detectors, so it names that origin.
SCREENING_DETECTOR = "skill_md._screen_untrusted_document"

# (secret) A credential key/value shape. Copied verbatim from
# `app.services.publication._residual_secret_signals` (module-private,
# not importable without reaching past an underscore into a function that
# takes a scrubbed dict) -- reused here, not reinvented.
_CREDENTIAL_KV_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|token|passwd|pwd|bearer)\b\s*[:=]\s*\S+"
)

# (secret) A PRIVATE KEY *header* on its own. `trace_redaction`'s
# `private_key_block` pattern requires a matching -----END----- line
# (re.DOTALL span); an ingested document often pastes only the header, so
# this catches that case. Header-only match, from V4-hardening §5's own
# minimal set.
_PRIVATE_KEY_HEADER_RE = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----")

# (unsafe locator) file:// URLs, link-local / loopback / RFC-1918 hosts,
# and bare-IP URLs -- an ingested document should not be steering a reader
# at localhost, the cloud metadata endpoint, or an internal host.
_UNSAFE_LOCATOR_RE = re.compile(
    r"(?i)(?:"
    r"file://\S+"
    r"|https?://(?:"
    r"localhost"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r")(?:[:/]\S*)?"
    r")"
)


# ---------------------------------------------------------------------------
# Pure detection
# ---------------------------------------------------------------------------
def _redacted_marker(check_type: str, offset: int) -> str:
    """The ONLY thing a matched secret is ever allowed to become in a
    persisted `signals` array -- never the raw value."""
    return f"<redacted:{check_type}>@{offset}"


def screen_document_text(
    text: str,
    *,
    name: str = "",
    steps: Optional[list[str]] = None,
) -> list[dict]:
    """Pure (no DB). Run every implemented ingestion-time screen over the
    document's own text (its body, plus optional `name` and `steps`) and
    return one entry per TRIPPED check:

        {"check_type": str, "signals": [str, ...], "severity": "block" | "flag"}

    `[]` means clean. `signals` NEVER contains a raw secret -- a matched
    secret is replaced with `_redacted_marker(...)`. Severity is encoded
    here so `decide` stays a trivial fold:

      block  -> prompt_injection, trust_escalation, secret_exposure
                (incl. a private-key header)
      flag   -> unsafe_locator

    (`decide` maps any block -> REJECT, any flag -> QUARANTINE.)
    """
    haystack = " ".join(
        part for part in [text or "", name or "", *(steps or [])] if part
    )
    findings: list[dict] = []

    # 1a. prompt injection -- reused detector.
    inj = [m for m in _META_DIRECTIVE_RE.finditer(haystack)]
    if inj:
        findings.append({
            "check_type": "prompt_injection",
            "signals": [f"meta_directive@{m.start()}" for m in inj],
            "severity": "block",
        })

    # 1b. trust escalation -- reused detector.
    trust = [m for m in _TRUST_ASSERTION_RE.finditer(haystack)]
    if trust:
        findings.append({
            "check_type": "trust_escalation",
            "signals": [f"trust_assertion@{m.start()}" for m in trust],
            "severity": "block",
        })

    # 2. secret / credential exposure -- reused KNOWN_TOKEN_PATTERNS +
    #    publication.py's credential-kv regex + a private-key header rule.
    secret_signals: list[str] = []
    for label, pattern in KNOWN_TOKEN_PATTERNS:
        for m in pattern.finditer(haystack):
            secret_signals.append(f"{label} {_redacted_marker('secret_exposure', m.start())}")
    for m in _PRIVATE_KEY_HEADER_RE.finditer(haystack):
        secret_signals.append(
            f"private_key_header {_redacted_marker('secret_exposure', m.start())}"
        )
    for m in _CREDENTIAL_KV_RE.finditer(haystack):
        secret_signals.append(
            f"credential_kv {_redacted_marker('secret_exposure', m.start())}"
        )
    if secret_signals:
        findings.append({
            "check_type": "secret_exposure",
            "signals": secret_signals,
            "severity": "block",
        })

    # 3. unsafe locator.
    locs = [m for m in _UNSAFE_LOCATOR_RE.finditer(haystack)]
    if locs:
        findings.append({
            "check_type": "unsafe_locator",
            # the locator is not a secret -- keep it, truncated, for audit.
            "signals": [f"{m.group(0)[:120]}@{m.start()}" for m in locs],
            "severity": "flag",
        })

    return findings


def decide(findings: list[dict]) -> str:
    """Pure fold. Any `severity == "block"` -> REJECT; else any
    `severity == "flag"` -> QUARANTINE; `[]` (or all-clear) -> ALLOW."""
    severities = {f.get("severity") for f in findings}
    if "block" in severities:
        return "REJECT"
    if "flag" in severities:
        return "QUARANTINE"
    return "ALLOW"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
_INSERT_SQL = """
    INSERT INTO screening_decisions (
        id, ingestion_context_id, source_ref, artifact_uri, content_hash,
        decision, check_type, detector, detector_version,
        signals, reason,
        created_by, visibility, owner_id, scope_type, scope_entity_id
    ) VALUES (
        $1::uuid, $2::uuid, $3::uuid, $4, $5,
        $6, $7, $8, $9,
        $10::jsonb, $11,
        $12, $13::visibility_level, $14, $15, $16
    )
    RETURNING id
"""


async def record_screening_decision(
    pool: asyncpg.Pool,
    *,
    decision: str,
    check_type: str,
    detector: str,
    detector_version: str = SCREENING_DETECTOR_VERSION,
    signals: Optional[list] = None,
    reason: Optional[str] = None,
    ingestion_context_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    artifact_uri: Optional[str] = None,
    content_hash: Optional[str] = None,
    created_by: str,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
) -> str:
    """Persist ONE screening decision row. Validates `decision` and
    `check_type` against the exported vocabularies BEFORE any SQL runs
    (ValueError otherwise -- a bad verdict never reaches the table). Write
    goes through `tenant_transaction(pool, TenantScope.commons())` per the
    house write-path rule; id is a `uuid7()`. Returns the new row id."""
    if decision not in DECISIONS:
        raise ValueError(
            f"decision must be one of {DECISIONS!r}, got {decision!r}"
        )
    if check_type not in CHECK_TYPES:
        raise ValueError(
            f"check_type must be one of {CHECK_TYPES!r}, got {check_type!r}"
        )
    if not detector:
        raise ValueError("record_screening_decision requires a non-empty detector")
    if not detector_version:
        raise ValueError("record_screening_decision requires a non-empty detector_version")
    if not created_by:
        raise ValueError("record_screening_decision requires an explicit created_by")

    new_id = uuid7()
    payload = json.dumps(list(signals) if signals is not None else [])
    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        row = await conn.fetchrow(
            _INSERT_SQL,
            new_id, ingestion_context_id, source_ref, artifact_uri, content_hash,
            decision, check_type, detector, detector_version,
            payload, reason,
            created_by, visibility, owner_id, scope_type, scope_entity_id,
        )
    return str(row["id"])


async def record_screening_run(
    pool: asyncpg.Pool,
    *,
    findings: list[dict],
    detector: str = SCREENING_DETECTOR,
    detector_version: str = SCREENING_DETECTOR_VERSION,
    ingestion_context_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    artifact_uri: Optional[str] = None,
    content_hash: Optional[str] = None,
    created_by: str = "screening.record_screening_run",
    visibility: str = "public",
    owner_id: Optional[str] = None,
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
) -> dict:
    """Convenience: compute `decide(findings)` and write ONE
    `record_screening_decision` row per finding -- or a single ALLOW row
    when `findings` is empty (a screen that ran and found nothing is still
    an auditable fact). Returns
    `{"decision": <aggregate>, "decision_ids": [...]}`."""
    aggregate = decide(findings)
    common = dict(
        detector=detector,
        detector_version=detector_version,
        ingestion_context_id=ingestion_context_id,
        source_ref=source_ref,
        artifact_uri=artifact_uri,
        content_hash=content_hash,
        created_by=created_by,
        visibility=visibility,
        owner_id=owner_id,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
    )
    decision_ids: list[str] = []

    if not findings:
        decision_ids.append(await record_screening_decision(
            pool,
            decision="ALLOW",
            check_type="source_trust",
            reason="screen ran; no signals tripped",
            signals=[],
            **common,
        ))
        return {"decision": aggregate, "decision_ids": decision_ids}

    for finding in findings:
        per_finding = "REJECT" if finding.get("severity") == "block" else "QUARANTINE"
        decision_ids.append(await record_screening_decision(
            pool,
            decision=per_finding,
            check_type=finding["check_type"],
            signals=list(finding.get("signals") or []),
            reason=finding.get("reason") or f"{finding['check_type']} screen tripped",
            **common,
        ))
    return {"decision": aggregate, "decision_ids": decision_ids}


async def get_screening_decisions(
    pool: asyncpg.Pool,
    *,
    content_hash: Optional[str] = None,
    ingestion_context_id: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """Live screening decision rows (`t_invalid IS NULL`), oldest first,
    bounded. Filter by `content_hash` and/or `ingestion_context_id`."""
    clauses = ["t_invalid IS NULL"]
    params: list[Any] = []
    if content_hash is not None:
        params.append(content_hash)
        clauses.append(f"content_hash = ${len(params)}")
    if ingestion_context_id is not None:
        params.append(ingestion_context_id)
        clauses.append(f"ingestion_context_id = ${len(params)}::uuid")
    params.append(max(1, min(int(limit), 1000)))
    sql = (
        "SELECT * FROM screening_decisions "
        f"WHERE {' AND '.join(clauses)} "
        f"ORDER BY t_valid ASC LIMIT ${len(params)}"
    )
    rows = await pool.fetch(sql, *params)
    return [dict(row) for row in rows]

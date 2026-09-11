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
    honest limit `trace_redaction`'s own module docstring states.
    `source_trust` is a declared check type (the DB CHECK lists it,
    callers may record it) but `screen_document_text` does not implement
    a detector for it -- that is a source-registry-level judgment, not a
    per-document text pattern. `pii` / `license` / `malicious_executable`
    ARE implemented below (curated, narrow patterns -- not full PII/DLP or
    SPDX classification; see each detector's own comment for exactly what
    it does and does not catch).
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

# TEXT, not an enum -- this list will grow (see migration 68's header).
# Kept 1:1 with `check_type_chk_screening_decisions` in
# db/68_screening_decisions.sql.
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

# (pii) US Social Security Number shape. A shape check, not a validity
# check (there is no public SSN validity algorithm) -- deliberately narrow
# (the exact dashed grouping) to avoid flagging arbitrary NNN-NN-NNNN-
# shaped IDs from unrelated domains as a false negative risk we accept in
# exchange for not flagging every dashed numeric code in a document.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# (pii) A credit-card-shaped run of 13-19 digits, optionally grouped by
# spaces or dashes. Luhn-validated below (`_luhn_valid`) so a random
# same-length number (e.g. an invoice id) is not flagged -- only a
# number that actually passes the card checksum is.
_CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# (pii) standard email address shape. Flagged only when >= 3 DISTINCT
# addresses appear in one document (see screen_document_text) -- a single
# email is normal attribution (an author byline), a list of addresses is
# the actual leak shape.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# (pii) a conservative NANP-shaped phone number. Same ">= 3 distinct"
# threshold as email, same reasoning.
_PHONE_RE = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")

# (license) a short, CURATED list of restrictive/copyleft phrases. This is
# NOT SPDX classification -- it is a narrow phrase-match on signals that a
# document asserts terms incompatible with a permissively-licensed global
# commons. Permissive phrasing (MIT, Apache-2.0, BSD, "public domain") is
# deliberately NOT in this list -- it is the unflagged, expected case.
_LICENSE_PHRASES: tuple[str, ...] = (
    "all rights reserved",
    "proprietary and confidential",
    "do not distribute",
    "no license granted",
    "gnu general public license",
    "gpl-3.0",
    "gpl-2.0",
    "agpl",
)
_LICENSE_RE = re.compile(
    "(?i)(" + "|".join(re.escape(p) for p in _LICENSE_PHRASES) + ")"
)

# (malicious_executable) classic dropper shapes: decode-then-execute or
# pipe-to-shell, across the shells/languages an ingested doc's embedded
# snippet might target. Narrow on purpose -- a bare base64 blob or a bare
# `curl` is NOT enough (both are everywhere legitimately); the pattern
# requires the decode/fetch AND the execution verb together.
_DROPPER_RE = re.compile(
    r"(?i)(?:"
    r"base64\s+(?:-d|--decode)\s*\|\s*(?:sh|bash|zsh)\b"
    r"|curl\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b"
    r"|wget\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b"
    r"|atob\([^)]*\)[^\n]{0,80}\beval\("
    r"|eval\(\s*atob\("
    r"|exec\(\s*base64\.b64decode\("
    r"|\[Convert\]::FromBase64String\([^\n]{0,120}Invoke-Expression"
    r"|Invoke-Expression[^\n]{0,120}\[Convert\]::FromBase64String"
    r")"
)
# A long base64-shaped blob is only suspicious in PROXIMITY to an
# execution keyword (see screen_document_text) -- alone it is routine
# (embedded images, keys, fixtures).
_LONG_B64_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_EXEC_KEYWORD_RE = re.compile(
    r"(?i)\b(eval|exec|invoke-expression|iex|system\(|os\.system|subprocess|"
    r"processstartinfo)\b"
)
_EXEC_PROXIMITY_WINDOW = 200  # chars either side of a long base64 blob


def _luhn_valid(digits: str) -> bool:
    """Standard Luhn checksum over a digit string (no separators)."""
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ---------------------------------------------------------------------------
# Pure detection
# ---------------------------------------------------------------------------
def _redacted_marker(check_type: str, offset: int) -> str:
    """The ONLY thing a matched secret is ever allowed to become in a
    persisted `signals` array -- never the raw value."""
    return f"<redacted:{check_type}>@{offset}"


def redact_document_text(text: str) -> tuple[str, list[str]]:
    """Return a safe derived-text representation for storage/search.

    The raw Artifact remains immutable and addressable by content hash; this
    function is for projections such as ``artifact_blocks.text`` that would
    otherwise replicate detected credential material.  Every replacement is
    length-independent on purpose: offsets continue to address the raw
    Artifact, never this redacted rendering.
    """
    from app.services.trace_redaction import redact_value

    matched: list[str] = []
    redacted = redact_value(text or "", matched)
    for label, pattern in (
        ("private_key", _PRIVATE_KEY_HEADER_RE),
        ("credential", _CREDENTIAL_KV_RE),
    ):
        def replace(_: re.Match[str], *, _label: str = label) -> str:
            matched.append(_label)
            return f"[REDACTED:{_label}]"
        redacted = pattern.sub(replace, redacted)
    return redacted, sorted(set(matched))


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

    `[]` means clean. `signals` NEVER contains a raw secret/PII value -- a
    match is replaced with `_redacted_marker(...)`. Severity is encoded
    here so `decide` stays a trivial fold:

      block  -> prompt_injection, trust_escalation, secret_exposure
                (incl. a private-key header), malicious_executable
      flag   -> unsafe_locator, pii, license

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

    # 2b. malicious executable -- decode-then-execute / pipe-to-shell
    # dropper shapes (block: this is the most dangerous class), plus a
    # long base64 blob ONLY when an execution keyword sits within
    # _EXEC_PROXIMITY_WINDOW chars of it (a bare blob alone is routine).
    exec_signals: list[str] = []
    for m in _DROPPER_RE.finditer(haystack):
        exec_signals.append(f"dropper_pattern@{m.start()}")
    for m in _LONG_B64_RE.finditer(haystack):
        window_start = max(0, m.start() - _EXEC_PROXIMITY_WINDOW)
        window_end = min(len(haystack), m.end() + _EXEC_PROXIMITY_WINDOW)
        if _EXEC_KEYWORD_RE.search(haystack[window_start:window_end]):
            exec_signals.append(f"base64_near_exec_keyword@{m.start()}")
    if exec_signals:
        findings.append({
            "check_type": "malicious_executable",
            "signals": exec_signals,
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

    # 4. PII -- SSN shape, Luhn-valid card numbers, and email/phone LISTS
    # (>= 3 distinct occurrences; a single one is normal attribution).
    # Never the raw value in a signal, always `_redacted_marker`.
    pii_signals: list[str] = []
    for m in _SSN_RE.finditer(haystack):
        pii_signals.append(f"ssn {_redacted_marker('pii', m.start())}")
    for m in _CREDIT_CARD_RE.finditer(haystack):
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            pii_signals.append(f"credit_card {_redacted_marker('pii', m.start())}")
    email_matches = list(_EMAIL_RE.finditer(haystack))
    if len({m.group(0) for m in email_matches}) >= 3:
        pii_signals.append(
            f"email_list(n={len({m.group(0) for m in email_matches})}) "
            f"{_redacted_marker('pii', email_matches[0].start())}"
        )
    phone_matches = list(_PHONE_RE.finditer(haystack))
    if len({m.group(0) for m in phone_matches}) >= 3:
        pii_signals.append(
            f"phone_list(n={len({m.group(0) for m in phone_matches})}) "
            f"{_redacted_marker('pii', phone_matches[0].start())}"
        )
    if pii_signals:
        findings.append({
            "check_type": "pii",
            "signals": pii_signals,
            "severity": "flag",
        })

    # 5. license -- a narrow curated restrictive/copyleft phrase match
    # (NOT SPDX classification; see _LICENSE_PHRASES' own comment).
    lic = [m for m in _LICENSE_RE.finditer(haystack)]
    if lic:
        findings.append({
            "check_type": "license",
            "signals": [f"license:{m.group(0).lower()}@{m.start()}" for m in lic],
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
# SSRF / fetch-locator guard (G3 tail / T13)
# ---------------------------------------------------------------------------
class UnsafeLocatorError(Exception):
    """Raised when the server is about to fetch a locator that points at a
    non-http(s) scheme or a private / loopback / link-local / reserved
    address (directly or via DNS). The fetch is aborted, not downgraded --
    an SSRF attempt is an active security event, not questionable content.
    """

    def __init__(self, locator: str, reason: str) -> None:
        self.locator = locator
        self.reason = reason
        super().__init__(f"unsafe fetch locator {locator!r}: {reason}")


# Hosts an ingestion fetch is expected to hit. A locator whose host is not
# on this list still passes if it resolves only to public IPs -- the list
# is a fast-path, not the whole policy.
_LOCATOR_HOST_ALLOWLIST: frozenset[str] = frozenset({
    "github.com", "api.github.com", "raw.githubusercontent.com",
    "codeload.github.com", "objects.githubusercontent.com",
})

_METADATA_HOSTS: frozenset[str] = frozenset({
    "metadata.google.internal", "metadata", "instance-data",
})


def _ip_is_public(ip_text: str) -> bool:
    import ipaddress

    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def screen_locator(locator: str, *, resolve: bool = True) -> dict[str, Any]:
    """
    Validate a URL the server itself is about to fetch. Pure except for an
    optional DNS lookup (`resolve=True`).

    Returns {"allowed": bool, "classification": "ALLOW"|"REJECT",
             "reason": str, "scheme": str, "host": str,
             "resolved_ips": [str, ...]}.

    Blocks: non-http(s) schemes; a host that is / resolves to a loopback,
    private, link-local, reserved, multicast or unspecified address; the
    cloud-metadata hostnames; a bare-IP host that is not public.
    """
    from urllib.parse import urlsplit

    parts = urlsplit((locator or "").strip())
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()

    def _deny(reason: str) -> dict[str, Any]:
        return {"allowed": False, "classification": "REJECT", "reason": reason,
                "scheme": scheme, "host": host, "resolved_ips": []}

    if scheme not in ("http", "https"):
        return _deny(f"scheme {scheme or '(none)'} is not http(s)")
    if not host:
        return _deny("no host in locator")
    if host in _METADATA_HOSTS:
        return _deny("cloud-metadata hostname")

    import ipaddress

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not _ip_is_public(host):
        return _deny(f"bare-IP host {host} is not a public address")

    resolved: list[str] = []
    if resolve and literal_ip is None:
        import socket

        try:
            infos = socket.getaddrinfo(host, parts.port or (443 if scheme == "https" else 80),
                                       proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            return _deny(f"DNS resolution failed: {exc}")
        resolved = sorted({info[4][0] for info in infos})
        if not resolved:
            return _deny("host resolved to no addresses")
        bad = [ip for ip in resolved if not _ip_is_public(ip)]
        if bad:
            return _deny(f"host resolves to non-public address(es): {', '.join(bad)}")

    return {"allowed": True, "classification": "ALLOW",
            "reason": "host on allowlist" if host in _LOCATOR_HOST_ALLOWLIST
            else "scheme + host/IP are public",
            "scheme": scheme, "host": host, "resolved_ips": resolved}


def assert_safe_locator(locator: str, *, resolve: bool = True) -> dict[str, Any]:
    """`screen_locator` but raises `UnsafeLocatorError` on a deny. Returns
    the ALLOW result dict. Call this immediately before any server-side
    fetch (`httpx.get`, `git clone`) of an externally-influenced URL."""
    result = screen_locator(locator, resolve=resolve)
    if not result["allowed"]:
        raise UnsafeLocatorError(locator, result["reason"])
    return result


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

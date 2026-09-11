"""
Explicit private -> global publish gate for Claims (G12 / G24 sibling).

`publication.py::publish_procedure` is the canonical Procedure publish
gate: private -> global never happens automatically, always through one
explicit, audited, scrubbed, dependency-checked call. Claims had NO
analogous gate -- `capture_claim` can write `visibility='private'`, but
nothing ever promotes that row to `'public'` except a raw, ungated
`UPDATE knowledge_nodes SET visibility = ...`, which would skip every
check `publish_procedure` enforces (ownership, content screening, secret
scrubbing, source-lineage privacy). This module is that gate, for Claims.

WHY THIS MATTERS NOW: G12's exploration write-back
(`app.stealth.exploration.close_exploration`) captures a resolved
exploration as a real, durable, PRIVATE Claim. Without this module that
Claim had no path to ever become global -- the "private candidate" half
of the private/global simulation existed, the "explicit publish" half
did not. This closes that loop, mirroring `publish_procedure`'s posture
exactly: never automatic, always audited, fails closed.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.access import TenantScope, tenant_transaction
from app.services.classification import PRIVATE_CLASSES, classify
from app.services.publish import _scrub_value  # the same real secret + path scrub publish_procedure reuses
from app.services.screening import screen_document_text


class ClaimNotFound(LookupError):
    pass


class ClaimPublicationDenied(PermissionError):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


class ClaimAlreadyPublic(ValueError):
    pass


_CLAIM_SELECT = """
    SELECT id, name AS statement, visibility, owner_id, scope_type, scope_entity_id,
           properties
    FROM knowledge_nodes
    WHERE id = $1::uuid AND node_type = 'claim' AND t_invalid IS NULL
"""


def _props(row: dict) -> dict:
    import json
    p = row.get("properties")
    return json.loads(p) if isinstance(p, str) else (p or {})


async def _source_lineage_reasons(pool: Any, props: dict) -> list[str]:
    """Check the claim's own known Source for privacy. `source_ref` is the
    only lineage this module resolves today (it has no dedicated column --
    `capture_claim` stashes it in `properties['source_ref']`, per that
    module's own docstring); a trace-derived claim's `claim_sources` ->
    `observations` -> Source chain is a real extension point, not wired
    here yet -- see the module docstring."""
    source_ref = props.get("source_ref")
    if not source_ref:
        return []
    row = await pool.fetchrow(
        "SELECT id, visibility, scope_type FROM sources WHERE id = $1::uuid AND t_invalid IS NULL",
        source_ref,
    )
    if row is None:
        return [f"claim cites source {source_ref!r} which no longer resolves -- unverifiable origin"]
    cls = classify(visibility=row["visibility"], scope_type=row["scope_type"])
    if (row["visibility"] or "").lower() in ("private", "org", "organization") or cls in PRIVATE_CLASSES:
        return [f"source {source_ref!r} is {row['visibility']!r} -- cannot generalize its claim to global"]
    return []


async def publish_claim(
    pool: asyncpg.Pool, *, claim_id: str, actor_subject: str,
) -> dict:
    """The canonical Claim publish. Returns
    {"claim_id", "previous_visibility", "scrubbed_statement", "reasons": []}.
    Raises `ClaimNotFound` / `ClaimAlreadyPublic` / `ClaimPublicationDenied`.
    Idempotent by refusal, not by silently no-op-ing: publishing an
    already-public claim is a caller error, surfaced as such.
    """
    row = await pool.fetchrow(_CLAIM_SELECT, claim_id)
    if row is None:
        raise ClaimNotFound(claim_id)
    row = dict(row)
    props = _props(row)

    if (row["visibility"] or "").lower() == "public":
        raise ClaimAlreadyPublic(claim_id)

    reasons: list[str] = []

    # 1. authority -- only the owner may publish their own private claim.
    if (row.get("owner_id") or "") != actor_subject:
        reasons.append("actor does not own the claim")

    # 2. scope -- only private/org rows are publishable at all.
    if (row["visibility"] or "").lower() not in ("private", "org", "organization"):
        reasons.append(f"claim visibility {row['visibility']!r} is not publishable")

    # 3. content screening -- reuse the real ingestion-time detectors
    #    (secret/PII/malicious-executable/injection). A block-severity
    #    finding refuses publication outright; the claim keeps its
    #    unscrubbed statement (nothing here mutates until every check
    #    passes).
    findings = screen_document_text(row["statement"] or "")
    blocking = [f for f in findings if f.get("severity") == "block"]
    if blocking:
        reasons.append(
            "content screen blocked publication: "
            + ", ".join(sorted({f["check_type"] for f in blocking}))
        )

    # 4. source lineage -- a claim citing a private Source cannot be
    #    generalized to a public one without leaking that source's scope.
    reasons.extend(await _source_lineage_reasons(pool, props))

    if reasons:
        raise ClaimPublicationDenied(reasons)

    # 5. scrub (same primitive publish_procedure uses) -- belt-and-braces
    #    alongside the screen above; screen_document_text flags secret
    #    *patterns*, _scrub_value additionally strips known path/PII shapes.
    scrubbed_statement = _scrub_value(row["statement"])

    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        result = await conn.execute(
            "UPDATE knowledge_nodes SET visibility = 'public', name = $2 "
            "WHERE id = $1::uuid AND node_type = 'claim' AND t_invalid IS NULL "
            "AND visibility <> 'public'",
            claim_id, scrubbed_statement,
        )
    if result == "UPDATE 0":
        # Raced with a concurrent publish/invalidate between the read
        # above and this write -- fail closed, don't claim success.
        raise ClaimAlreadyPublic(claim_id)

    from app.services.changeset_record import ChangeOperation, record_change_set

    await record_change_set(
        pool,
        author=actor_subject,
        reason=f"claim {claim_id} published private -> global",
        operations=[
            ChangeOperation(
                operation="status_change",  # OPERATIONS whitelist has no dedicated visibility_change;
                target_table="knowledge_nodes",  # same generic bucket claim_belief.py uses for a
                                                  # knowledge_nodes field mutation -- detail says what changed.
                target_id=str(claim_id),
                detail={
                    "previous_visibility": row["visibility"],
                    "new_visibility": "public",
                    "scrubbed": scrubbed_statement != row["statement"],
                },
            )
        ],
    )

    return {
        "claim_id": str(claim_id),
        "previous_visibility": row["visibility"],
        "scrubbed_statement": scrubbed_statement,
        "reasons": [],
    }

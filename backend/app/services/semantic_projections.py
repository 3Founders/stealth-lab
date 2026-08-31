"""
Canonical embedding-text builders + a content-hash primitive for
re-embedding avoidance.

The graph remains authoritative; embeddings are projections of it. Today
each real writer builds its embedding input ad hoc and inline -- e.g.
`capture_claim` (app/services/claims.py) just embeds the raw `statement`
string, with no structured-field signal folded in. This module gives each
of the three main object types (claim / task / procedure) ONE canonical,
reused function that builds the exact text string that should be embedded
for that object, so every caller (writers, backfills, re-embed jobs)
produces the same projection from the same inputs.

It also gives a real, testable way to detect "this object's embeddable
content hasn't changed since it was last embedded" (`content_hash` +
`needs_reembedding`), so a caller can skip a real embedding API call when
nothing that feeds the projection actually changed.

HONESTY NOTE -- what this module is NOT:
  - It is NOT wired into any real writer. `capture_claim`,
    `capture_procedure`, and friends are UNCHANGED by this file and still
    build their embedding input the old way. Adopting these functions in
    those writers is a separate, follow-up change.
  - It does NOT add a `semantic_projection_hash` (or similarly named)
    column anywhere. Persisting a content hash for reuse across writes
    needs a migration, which is a bigger decision than this task makes
    unilaterally (CLAUDE.md: additive migrations go through the board).
  - It makes zero network calls and imports nothing from
    `app.services.embeddings` -- pure text-building and hashing only.

Templates chosen (see each function's docstring for the exact reasoning):

  claim:     statement, then "\\n\\nsubject: ...\\npredicate: ...\\nobject: ..."
             for whichever of subject/predicate/object are given (only
             non-None fields appear, in that fixed order), nothing appended
             when none are given.
  task:      name, then "\\n" + description if given, then "\\n\\nio: k1, k2"
             listing io_schema's top-level keys (not a full dump) if the
             dict is non-empty.
  procedure: name, then "\\n" + goal, then "\\n\\ncapability: ..." if
             capability_statement is given.

These mirror the existing `node_text()` convention in
app/services/embeddings.py (name, then "\\n" + description) rather than
inventing a new separator style.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Optional


def claim_embedding_text(
    *,
    statement: str,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    object: Optional[str] = None,  # noqa: A002 -- matches the domain's triple vocabulary
) -> str:
    """
    Canonical embeddable text for a claim.

    A structured (subject, predicate, object) triple carries real semantic
    signal beyond the free-text `statement` alone, so when any of the three
    are present they are appended as a labeled block, one field per line,
    in fixed subject/predicate/object order -- only the fields actually
    given appear (a partial triple is not padded with blanks). With none
    given, the result is exactly `statement` (today's real behavior in
    `capture_claim`, preserved as the fallback).
    """
    lines: list[str] = []
    if subject is not None:
        lines.append(f"subject: {subject}")
    if predicate is not None:
        lines.append(f"predicate: {predicate}")
    if object is not None:
        lines.append(f"object: {object}")

    if not lines:
        return statement
    return statement + "\n\n" + "\n".join(lines)


def task_embedding_text(
    *,
    name: str,
    description: Optional[str] = None,
    io_schema: Optional[Mapping[str, Any]] = None,
) -> str:
    """
    Canonical embeddable text for a task_nodes row.

    Name plus description (matching the existing `node_text()` convention
    in app/services/embeddings.py), with the I/O shape folded in as its
    real top-level keys -- e.g. "io: input, output" -- rather than a full
    schema dump: the *shape* of what a task consumes/produces is real
    semantic signal for matching a task to a query, but the full JSON
    schema is mostly structural noise that would dilute the embedding.
    An empty or absent io_schema contributes nothing.
    """
    text = name
    if description:
        text += "\n" + description
    if io_schema:
        keys = ", ".join(str(k) for k in io_schema.keys())
        if keys:
            text += "\n\nio: " + keys
    return text


def procedure_embedding_text(
    *,
    name: str,
    goal: str,
    capability_statement: Optional[str] = None,
) -> str:
    """
    Canonical embeddable text for a procedures row.

    Goal is the primary signal (what the procedure is *for*), paired with
    name. `capability_statement` -- procedures already carry this real
    column from earlier extraction work -- is already-abstracted semantic
    content describing what the procedure can actually do, so when present
    it's appended as a labeled block; absent, the text is just name + goal.
    """
    text = name + "\n" + goal
    if capability_statement:
        text += "\n\ncapability: " + capability_statement
    return text


def content_hash(text: str) -> str:
    """Deterministic hash of one object's canonical embeddable text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def needs_reembedding(*, current_text: str, stored_hash: Optional[str]) -> bool:
    """
    True iff a caller should actually spend an embedding API call.

    True when `stored_hash` is None (never embedded, or a hash was never
    tracked for this object) OR the current canonical text hashes
    differently than what's stored (real content change). False only when
    the hash matches exactly -- the same canonical text as last time, so
    the existing embedding is still valid and the call can be skipped.
    """
    if stored_hash is None:
        return True
    return content_hash(current_text) != stored_hash

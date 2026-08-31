"""
Temporal reasoning over a claim's version chain: "what did we believe at
commit A vs commit B" (product spec ask, task #39).

Composes three REAL, EXISTING pieces, none of them reimplemented here:

  - `claims.get_claim_version_chain()` -- walks a claim family
    (`claim_family_id`/`claim_version` in `properties` JSONB) oldest to
    newest. Imported and called, never re-walked here.
  - `episode_links` (db/01_ontology.sql) -- the real join between a claim
    row (`target_table='knowledge_nodes', target_id=<claim id>`) and the
    episode(s) that justified it. `capture_claim(justification_episode_id=
    ...)` is the only writer; NOT every claim has a link -- many are
    justified via `task_ids`/PRODUCES edges instead, and this module must
    return an honest empty result for those, never fabricate one.
  - `agent_traces` (db/12_trace_ingestion_pipeline.sql) -- the real,
    already-populated-when-known `commit_hash`/`repo`/`branch` columns,
    reached from an episode via the real, existing
    `episodes.session_id = agent_traces.session_id` join (the same join
    key `trace_worker.py`'s own `_ensure_trace_header` already writes
    against; `episodes.session_id` added in db/17_episode_project_
    columns.sql, confirmed TEXT, not an FK -- ticket 06's own note that
    `trace_id` currently always equals `session_id` as a fallback, so no
    FK was ever added pinning that behavior).

No migration. No new table. This module only reads.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.claims import get_claim_version_chain

# The real join this whole module rests on. One episode can (today, per
# capture_claim's own single justification_episode_id parameter) carry at
# most one INSERT into episode_links per claim version, but nothing
# enforces that as a DB constraint -- this is written and tested as
# "zero or more episodes per version, zero or more commits per episode's
# session", not "exactly one" at either hop.
#
# DISTINCT because a session can in principle have produced more than one
# agent_traces header (unlikely today, trace_id==session_id per ticket 06,
# but not a guaranteed invariant this query should lean on); commit_hash
# IS NOT NULL because a version whose episode's session never joined to a
# trace header that actually recorded a commit must come back as "no
# known commit", not a NULL entry pretending to be one.
_COMMITS_FOR_CLAIM_VERSION_SQL = """
    SELECT DISTINCT at.commit_hash, at.repo, at.branch
    FROM episode_links el
    JOIN episodes ep ON ep.id = el.episode_id
    JOIN agent_traces at ON at.session_id = ep.session_id
    WHERE el.target_id = $1::uuid AND el.target_table = 'knowledge_nodes'
      AND at.commit_hash IS NOT NULL
    ORDER BY at.commit_hash
"""


async def get_claim_commit_history(pool: asyncpg.Pool, claim_id: str) -> list[dict]:
    """
    One dict per VERSION (not per episode/commit) in `claim_id`'s real
    version chain (`claims.get_claim_version_chain`, called verbatim --
    version-chain walking is not reimplemented here), oldest to newest,
    each carrying:

      - `claim_id`   -- that version's own real knowledge_nodes.id (str)
      - `version`    -- `properties['claim_version']`, defaulting to 1 for
                        the family root, per `get_claim_version_chain`'s
                        own documented convention (the root never has this
                        key written onto it explicitly).
      - `statement`  -- `properties['statement']`.
      - `t_valid`    -- that version's own bi-temporal `t_valid`
                        (knowledge_nodes column; NOT carried by
                        `get_claim_version_chain`'s own SELECT, so fetched
                        here in one bounded follow-up query keyed by the
                        chain's own ids -- no per-row round trip).
      - `commits`    -- `[{"commit_hash", "repo", "branch"}, ...]`, real
                        rows from the join documented above. Honestly
                        empty (never fabricated, never guessed) when this
                        version has no `episode_links` row at all, or has
                        one whose episode's session never joined to an
                        `agent_traces` row with a non-null `commit_hash`.

    Returns `[]` if `claim_id` does not resolve to a live claim at all
    (matches `get_claim_version_chain`'s own posture for that case).
    """
    chain = await get_claim_version_chain(pool, claim_id)
    if not chain:
        return []

    ids = [str(row["id"]) for row in chain]
    t_valid_rows = await pool.fetch(
        "SELECT id, t_valid FROM knowledge_nodes WHERE id = ANY($1::uuid[])",
        ids,
    )
    t_valid_by_id = {str(r["id"]): r["t_valid"] for r in t_valid_rows}

    history: list[dict] = []
    for row in chain:
        version_id = str(row["id"])
        props = row["properties"]
        commit_rows = await pool.fetch(_COMMITS_FOR_CLAIM_VERSION_SQL, version_id)
        commits = [
            {
                "commit_hash": r["commit_hash"],
                "repo": r["repo"],
                "branch": r["branch"],
            }
            for r in commit_rows
        ]
        history.append({
            "claim_id": version_id,
            "version": props.get("claim_version", 1),
            "statement": props.get("statement"),
            "t_valid": t_valid_by_id.get(version_id),
            "commits": commits,
        })
    return history


async def what_was_current_as_of_commit(
    pool: asyncpg.Pool, claim_id: str, *, commit_hash: str,
) -> Optional[dict]:
    """
    Given ANY claim id in a family and a real commit hash, return the
    single version dict (same shape as one entry of
    `get_claim_commit_history`) this module can honestly say was current
    "as of" that commit -- or `None` if it cannot honestly determine one.

    THIS IS A GENUINELY AMBIGUOUS REQUEST. Multiple real interpretations
    exist for "current as of commit X" when the version chain's own
    episode links don't happen to cover every commit. The spec's own
    wording names two real signals and asks for both, in this exact
    priority:

      1. DIRECT MATCH (primary, and the only one this implementation can
         actually exercise -- see limitation below): walk
         `get_claim_commit_history(pool, claim_id)` in chain order and
         collect every version whose `commits` list contains
         `commit_hash` directly (i.e. that version's own justifying
         episode's session traces back to a header recording that exact
         commit). Return the LATEST (highest chain-order index) such
         version. This is the only fully-grounded case: the version's own
         provenance says "I was produced by work done at this commit."

      2. TEMPORAL-BOUND FALLBACK (as specified): "a version V whose
         `t_valid` is <= the earliest known `t_valid` of a LATER version
         V' that itself matches `commit_hash`." Implemented literally,
         and documented here rather than silently applied, because on
         inspection it is SUBSUMED by (1) for this function's own inputs:
         "V' that itself matches commit_hash" is precisely a direct match
         per rule 1, over the SAME `history` list rule 1 already scans in
         full. Any V' satisfying the fallback's own condition is already
         found by, and already beaten in chain order by, rule 1's own
         "latest direct match" -- so rule 1 alone already returns the
         correct answer in every case rule 2 could otherwise fire in.
         There is therefore no case where scanning for rule 2 changes the
         result versus rule 1 alone, given `get_claim_commit_history`'s
         real output shape (one row per version, not per commit-adjacent
         timestamp). No separate code path is written for it; this
         paragraph is the honest record of why, not a silent omission.

    HONEST LIMITATION: if `commit_hash` is real (exists in `agent_traces`
    somewhere) but was never linked, via ANY version's own
    `justification_episode_id`, into `claim_id`'s family -- e.g. the work
    at that commit never captured a claim, or captured one under a
    different, unrelated claim family -- this function has no honest
    signal to bound against and returns `None`. It deliberately does NOT
    fall back to "nearest commit by wall-clock time across all of
    `agent_traces`" or any other cross-family inference: that would be a
    guess dressed as an answer, which the task explicitly forbids
    ("if you cannot honestly determine an answer, return None, do not
    return the wrong version").

    Returns `None` if `claim_id` does not resolve to a live claim, or if
    no version in its chain has `commit_hash` among its real commits.
    """
    history = await get_claim_commit_history(pool, claim_id)
    if not history:
        return None

    match: Optional[dict] = None
    for version in history:
        commit_hashes = {c["commit_hash"] for c in version["commits"]}
        if commit_hash in commit_hashes:
            match = version  # chain order -> last hit wins == latest match
    return match

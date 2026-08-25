"""
Replayability boundary (Band 2.8, spec.md REPLAYABILITY section):
regenerates the derived chain -- observations -> claims -> procedure
candidates -- from raw trace_events and proves each layer equals what
was actually stored, with every extractor's id+version stamped.

WHY THIS LIVES HERE (app/execution/): same pattern as plans.py and
evidence.py in this package -- a pure/near-pure contract boundary over
storage concerns, offline-provable, no behavior change for existing
callers until one adopts it. The extractors themselves stay where they
belong (observations.py, procedure_extraction/*); this module only
orchestrates and fingerprints. Importing them lazily inside functions
keeps this module's import surface DB- and network-free.

WHAT IS PROVABLY DETERMINISTIC, stated rather than implied: the
deterministic path is (trace events + project_state(as_of)) ->
observations -> claim shapes -> candidate shapes, bit-identical on
re-run -- that is what replay_session() verifies. Model-based stages
(semantic_label_v1, GroundedHybridExtractor) are NOT bit-reproducible;
spec handles them by version stamping ("Version all extraction/
normalization logic"), so replay verifies their stamps are complete and
their outputs remain re-derivable at the pinned prompt/model/decoding
hashes, not that a model regenerates its own prose.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Optional

import asyncpg

from app.services.access import TenantScope, tenant_predicate

REPLAY_PIPELINE_VERSION = "1"

# Claim promotion is itself an extraction step with its own version --
# promote_observation_to_claim()'s transform (label->statement,
# epistemic_status from extractor_kind, composite extraction_version) is
# logic someone will change someday, and when they do this constant must
# move with it, exactly like observations.py's own code-version pair.
CLAIM_PROMOTION_EXTRACTOR_NAME = "claim_promotion"
CLAIM_PROMOTION_CODE_VERSION = "1"

PROCEDURE_CANDIDATE_DETERMINISTIC_TAG = "deterministic_v1@1"


def fingerprint(value: Any) -> str:
    """
    Canonical content hash: dict key order can never change a
    fingerprint, anything else about the value can. default=str keeps
    datetimes/Decimals/UUIDs comparable across asyncpg rows and plain
    dicts without callers pre-normalizing anything.
    """
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extractor_stamps() -> dict[str, str]:
    """
    The stage -> "extractor@version" map for the whole pipeline, pulled
    from the constants that AUTHORITATIVELY govern each stage's write
    path (not redeclared here): trace_worker.SCHEMA_VERSION for raw
    normalization, observations.py's name/version pairs for both
    observation extractors, migration 20's seeded deterministic tag for
    candidates. A stage renaming upstream breaks the tests that pin
    these values loudly, instead of silently forking provenance.
    """
    from app.services import observations as _obs
    from app.services import trace_worker as _tw

    return {
        "normalize_trace_events": f"trace_worker@{_tw.SCHEMA_VERSION}",
        "observation.deterministic": (
            f"{_obs.DETERMINISTIC_EXTRACTOR_NAME}@{_obs.DETERMINISTIC_CODE_VERSION}"
        ),
        "observation.model": f"{_obs.MODEL_EXTRACTOR_NAME}@{_obs.MODEL_CODE_VERSION}",
        "claim.promotion": (
            f"{CLAIM_PROMOTION_EXTRACTOR_NAME}@{CLAIM_PROMOTION_CODE_VERSION}"
        ),
        "procedure_candidate.deterministic": PROCEDURE_CANDIDATE_DETERMINISTIC_TAG,
    }


def regenerate_observations(events: list[dict]) -> list[dict]:
    """
    Pure re-run of the deterministic observation extractor over raw
    trace_event rows (asyncpg Records fine -- dict()-compatible), in
    sequence order regardless of input order. Each produced shape
    carries the full component stamp persist_observation() writes, so
    "what SHOULD be stored" and "what WAS stored" are compared like for
    like. Events that yield nothing (unknown tools, empty commands)
    yield nothing here either -- absence is part of the contract.
    """
    from app.services.observations import (
        DETERMINISTIC_CODE_VERSION,
        DETERMINISTIC_EXTRACTOR_NAME,
        extract_deterministic_observations,
    )

    regenerated: list[dict] = []
    for event in sorted((dict(e) for e in events), key=lambda e: e["sequence"]):
        for produced in extract_deterministic_observations(event):
            regenerated.append({
                "event_id": str(event["id"]) if event.get("id") is not None else None,
                "observation_type": produced["observation_type"],
                "label": produced["label"],
                "properties": produced.get("properties") or {},
                "extractor_kind": "deterministic",
                "extractor_name": DETERMINISTIC_EXTRACTOR_NAME,
                "code_version": DETERMINISTIC_CODE_VERSION,
            })
    return regenerated


def expected_claim_shape(observation_row: dict) -> dict:
    """
    The claim promotion transform as a pure function -- byte-for-byte
    the same mapping promote_observation_to_claim() applies (statement
    from label, claim_type from observation_type, epistemic_status from
    extractor_kind, extraction_version as the extractor_name:
   code_version[:model_id] composite). Replay compares stored claims
    against THIS shape; if the live transform ever drifts from this
    mirror, replay_session reports the mismatch -- which is the point.
    """
    row = dict(observation_row)
    epistemic_status = "observed" if row["extractor_kind"] == "deterministic" else "inferred"
    version_parts = [row["extractor_name"], row["code_version"]]
    if row.get("model_id"):
        version_parts.append(row["model_id"])
    return {
        "statement": row["label"],
        "claim_type": row["observation_type"],
        "epistemic_status": epistemic_status,
        "extraction_version": ":".join(version_parts),
        "promoted_by": f"{CLAIM_PROMOTION_EXTRACTOR_NAME}@{CLAIM_PROMOTION_CODE_VERSION}",
    }


def _content_fingerprint(observation: dict) -> str:
    """What identifies an observation by CONTENT (ids and stamps aside):
    type + label + properties. Two runs of the pipeline produce equal
    multisets of these or replay fails."""
    return fingerprint((
        observation["observation_type"],
        observation["label"],
        observation.get("properties") or {},
    ))


def _compare_multiset(
    regenerated: list[dict], stored: list[dict],
) -> tuple[bool, list[str], list[str]]:
    """Multiset equality on content fingerprints; returns
    (match, missing_from_stored, unexpected_in_stored) with readable
    labels so a mismatch names the actual rows, not just counts."""
    reg_counts = Counter(_content_fingerprint(o) for o in regenerated)
    stored_counts = Counter(_content_fingerprint(o) for o in stored)
    missing = [f"{o['observation_type']}: {o['label']}" for o in regenerated
               if reg_counts[_content_fingerprint(o)] > stored_counts[_content_fingerprint(o)]]
    unexpected = [f"{o['observation_type']}: {o['label']}" for o in stored
                  if stored_counts[_content_fingerprint(o)] > reg_counts[_content_fingerprint(o)]]
    # dedupe while preserving order -- a duplicated row would otherwise
    # repeat in the report once per surplus copy
    return (not missing and not unexpected,
            list(dict.fromkeys(missing)), list(dict.fromkeys(unexpected)))


async def replay_session(
    pool: asyncpg.Pool,
    *,
    session_id: str,
    episode_ids: Optional[list[str]] = None,
    project_id: Optional[str] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> dict:
    """
    End-to-end replay verifier for one session's raw traces. Loads the
    raw trace_events, REGENERATES each derived layer from them, and
    compares against what is actually stored:

    `tenant_scope` scopes the claims-layer read (knowledge_nodes is a
    tenant-bearing [V] table — HARDENING H1's builder supplies the
    fragment). Default None resolves to TenantScope.unrestricted()
    because this verifier is an internal maintenance path; passing a
    real scope keeps an integrity audit from crossing tenant lines.
      observations -- multiset equality on content; every stored row
                      must carry non-empty extractor_name+code_version.
      claims       -- every claim linked (via claim_sources) to an
                      observation of this session must equal
                      expected_claim_shape() of that observation, with
                      its promotion stamp present.
      procedures   -- only when episode_ids is given: every candidate
                      row citing one of those episodes must regenerate
                      field-for-field from the REGENERATED evidence via
                      DeterministicExtractor (the strategy its
                      extracted_by stamp claims produced it).
                      Preconditions derive from project_state(as_of);
                      pass project_id to reproduce them, otherwise both
                      sides come out empty and the verdict stays honest.

    Returns a JSON-safe report; "match" is False whenever ANY checked
    object mismatches, is missing its stamp, or (for observations/
    claims) when stored and regenerated multisets disagree. Layers with
    nothing to check report match=True vacuously -- checked counts make
    that distinguishable from a real proof.
    """
    events = [
        dict(r) for r in await pool.fetch(
            "SELECT id, sequence, event_type, tool_name, tool_input "
            "FROM trace_events WHERE session_id = $1 ORDER BY sequence ASC",
            session_id,
        )
    ]
    regenerated = regenerate_observations(events)

    # ---- observations -------------------------------------------------
    obs_rows = await pool.fetch(
        """
        SELECT DISTINCT o.id, o.observation_type, o.label, o.properties,
               o.extractor_name, o.code_version
        FROM observations o
        JOIN observation_events oe ON oe.observation_id = o.id
        JOIN trace_events te ON te.id = oe.event_id
        WHERE te.session_id = $1
        ORDER BY o.label
        """,
        session_id,
    )
    stored_obs = [dict(r) for r in obs_rows]
    obs_match, missing, unexpected = _compare_multiset(regenerated, stored_obs)
    stamp_gaps = [
        str(r["id"]) for r in obs_rows
        if not r["extractor_name"] or not r["code_version"]
    ]
    observations_report = {
        "checked": len(stored_obs),
        "regenerated": len(regenerated),
        "match": obs_match and not stamp_gaps,
        "missing": missing,
        "unexpected": unexpected,
        "stamp_gaps": stamp_gaps,
    }

    # ---- claims --------------------------------------------------------
    # knowledge_nodes carries tenant_id (V0 column, H1 predicate): the
    # fragment is ALWAYS present — `TRUE` when unrestricted — so a
    # tenant-bounded audit can never quietly widen itself.
    scope = tenant_scope if tenant_scope is not None else TenantScope.unrestricted()
    ten_sql, ten_params = tenant_predicate(scope, alias="k", param_index=2)
    claim_rows = await pool.fetch(
        f"""
        SELECT k.id AS claim_id, k.properties, cs.observation_id,
               o.observation_type, o.label, o.extractor_kind,
               o.extractor_name, o.code_version, o.model_id
        FROM knowledge_nodes k
        JOIN claim_sources cs ON cs.claim_id = k.id
        JOIN observations o ON o.id = cs.observation_id
        WHERE k.node_type = 'claim'
          AND {ten_sql}
          AND EXISTS (
              SELECT 1 FROM observation_events oe
              JOIN trace_events te ON te.id = oe.event_id
              WHERE oe.observation_id = o.id AND te.session_id = $1)
        ORDER BY k.t_created ASC
        """,
        session_id,
        *ten_params,
    )
    claim_mismatches: list[str] = []
    for row in claim_rows:
        props = dict(row["properties"] or {})
        expected = expected_claim_shape(dict(row))
        for key in ("statement", "claim_type", "epistemic_status", "extraction_version"):
            if props.get(key) != expected[key]:
                claim_mismatches.append(
                    f"claim {row['claim_id']} {key}: stored={props.get(key)!r} "
                    f"expected={expected[key]!r}"
                )
        if not props.get("promoted_by"):
            # A claim without its promotion stamp fails the verdict:
            # fresh-start means every claim has one, and a silent pass
            # here would hollow out exactly the guarantee Band 2.8 adds.
            claim_mismatches.append(f"claim {row['claim_id']} missing promoted_by stamp")
    claims_report = {
        "checked": len(claim_rows),
        "match": not claim_mismatches,
        "mismatches": claim_mismatches,
    }

    # ---- procedure candidates ------------------------------------------
    procedures_report: dict = {"checked": 0, "match": True, "mismatches": []}
    if episode_ids:
        from app.services.procedure_extraction.evidence import AgentRunEvidenceSource
        from app.services.procedure_extraction.strategies import DeterministicExtractor

        proc_rows = await pool.fetch(
            "SELECT id, name, goal, capability_statement, steps, parameter_schema, "
            "preconditions, scope, failure_conditions, extracted_by "
            "FROM procedures WHERE source_episode_ids && $1::uuid[] "
            "AND extracted_by IS NOT NULL",
            list(episode_ids),
        )
        tool_sequence = [e["tool_name"] for e in events if e.get("tool_name")]
        evidence_shapes = [
            {"observation_type": o["observation_type"], "label": o["label"],
             "properties": o.get("properties") or {}}
            for o in regenerated
        ]
        for row in proc_rows:
            mismatches: list[str] = []
            evidence = await AgentRunEvidenceSource(
                goal_text=row["goal"], outcome="success",
                observations=evidence_shapes, tool_sequence=tool_sequence,
                project_id=project_id,
            ).collect()
            re_extracted = await DeterministicExtractor().extract(pool, evidence)

            if row["name"] != re_extracted.name:
                mismatches.append("name")
            if row["goal"] != re_extracted.goal:
                mismatches.append("goal")
            if row["capability_statement"] != re_extracted.capability_statement:
                mismatches.append("capability_statement")
            if fingerprint(row["steps"]) != fingerprint(
                    [s.model_dump() for s in re_extracted.steps]):
                mismatches.append("steps")
            if fingerprint(row["failure_conditions"]) != fingerprint(
                    re_extracted.failure_conditions):
                mismatches.append("failure_conditions")
            if fingerprint(row["scope"] or {}) != fingerprint(re_extracted.scope):
                mismatches.append("scope")
            parameter_schema = dict(row["parameter_schema"] or {})
            if fingerprint(parameter_schema.get("slots") or []) != fingerprint(
                    [s.model_dump() for s in re_extracted.slots]):
                mismatches.append("slots")
            if parameter_schema.get("extraction_method") != row["extracted_by"]:
                mismatches.append("parameter_schema.extraction_method vs extracted_by")
            if row["extracted_by"] != PROCEDURE_CANDIDATE_DETERMINISTIC_TAG:
                # replay proves the deterministic baseline; a model-tagged
                # candidate is out of ITS scope by design (stamped, not
                # bit-reproducible) -- flagged honestly rather than failed
                mismatches.append(
                    f"extracted_by={row['extracted_by']!r} is not the "
                    "deterministic baseline this prover regenerates"
                )
            if fingerprint(row["preconditions"] or []) != fingerprint(
                    [p.model_dump() for p in re_extracted.preconditions]):
                mismatches.append(
                    "preconditions (derive from project_state(as_of); pass "
                    "project_id to reproduce, see docstring)"
                )
            procedures_report["checked"] += 1
            if mismatches:
                procedures_report["mismatches"].append(
                    f"procedure {row['id']}: " + "; ".join(mismatches)
                )
        procedures_report["match"] = not procedures_report["mismatches"]

    return {
        "session_id": session_id,
        "pipeline_version": REPLAY_PIPELINE_VERSION,
        "stamps": extractor_stamps(),
        "raw_events": len(events),
        "observations": observations_report,
        "claims": claims_report,
        "procedures": procedures_report,
    }

"""
MCP hardening B34/B32: the verification ladder.

The core invariant, enforced HERE (not just documented): the host cannot
directly write the strongest state. Every `record_*` function below is
named after one evidence class and computes the resulting `state`
itself from what that class can actually support -- none of them accept
`state` as a caller-supplied parameter. `evaluate_run_completion` (the
`verify_completion` MCP tool's engine) only ever READS results these
functions produced; it never grades anything itself.

State ladder (weakest to strongest, non-failure states):
    CLAIMED_DONE < CHECKED < VERIFIED < INDEPENDENTLY_VERIFIED
`FAILED_VERIFICATION` and `INCONCLUSIVE` are outcomes, not rungs on this
ladder -- a criterion can fail or stay inconclusive from any method.

Criteria themselves are NOT a new stored entity -- see migration 55's
own comment: `procedures.postconditions` (a plain array of statement
strings, confirmed live -- 2 of 2625 procedures even have any yet) is
already the durable definition. `derive_criteria` below only computes a
stable `criterion_id` for each one; it never copies or mutates the
Procedure row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import asyncpg

METHODS: tuple[str, ...] = (
    "self_report", "artifact_inspection", "deterministic_check",
    "independent_agent", "human_review", "real_world_outcome",
)
STATES: tuple[str, ...] = (
    "claimed_done", "checked", "verified", "independently_verified",
    "failed_verification", "inconclusive",
)
# Ladder rank for the non-failure states only -- used to report "the
# strongest state actually reached", never to let a caller pick a rank.
_LADDER_RANK = {"claimed_done": 0, "checked": 1, "verified": 2, "independently_verified": 3}


class VerificationError(Exception):
    """Raised on a malformed verification request -- never silently coerced."""


@dataclass
class Criterion:
    criterion_id: str
    statement: str
    required: bool = True
    # Meta-harness Sec 14 node-scoping (migration 82): None means this
    # criterion is run-scoped (the overwhelming case today -- every
    # postcondition-derived criterion below), a real value ties it to one
    # specific execution_run_node. Never fabricated -- only set when a
    # caller-supplied step-order -> node-id mapping actually names one.
    execution_run_node_id: Optional[str] = None


def derive_criteria(
    procedure: dict, *, node_id_by_step_order: Optional[dict[int, str]] = None,
) -> list[Criterion]:
    """One Criterion per `postconditions` entry (run-scoped, unchanged),
    PLUS one Criterion per step whose own `verification` field is set
    (node-scoped, meta-harness Sec 14 -- new this pass).

    A postcondition may be a plain string (every real one today) or a
    dict with a `statement` key and an optional `required` flag
    (`required=False` is the one thing an author can declare today;
    there is no `method` field to read yet -- see this module's own
    docstring for why that is a real, current limitation, not an
    oversight). Postcondition criteria never carry a node id -- a
    postcondition names no particular step, so it stays run-scoped by
    construction, not by omission.

    `node_id_by_step_order`: `{step["order"]: execution_run_node_id}`,
    supplied by a caller that has already compiled/persisted the plan
    (this function itself stays pool-free and pure, same discipline
    `compile_plan()` holds). A step whose `order` has no entry, or whose
    own `verification` field is unset, contributes nothing here --
    HONEST SCOPE (confirmed live, this pass): zero real procedures in
    the corpus have step-level `verification` data today, so this path
    is real, additive, and currently unexercised by real data, not a
    guess about what would happen if it were.

    `steps[i]["verification"]` shape mirrors postconditions: a plain
    string, or a dict with `statement`/optional `required`.
    """
    criteria: list[Criterion] = []
    for i, raw in enumerate(procedure.get("postconditions") or []):
        if isinstance(raw, str):
            criteria.append(Criterion(criterion_id=f"postcondition:{i}", statement=raw))
        elif isinstance(raw, dict) and raw.get("statement"):
            criteria.append(Criterion(
                criterion_id=f"postcondition:{i}", statement=raw["statement"],
                required=bool(raw.get("required", True)),
            ))
        # A malformed entry (empty dict, no statement) is skipped, not
        # fabricated into a criterion with empty text.

    node_id_by_step_order = node_id_by_step_order or {}
    for step in procedure.get("steps") or []:
        order = step.get("order")
        raw = step.get("verification")
        if raw is None or order is None:
            continue
        node_id = node_id_by_step_order.get(order)
        if isinstance(raw, str) and raw.strip():
            criteria.append(Criterion(
                criterion_id=f"step:{order}:verification", statement=raw,
                execution_run_node_id=node_id,
            ))
        elif isinstance(raw, dict) and raw.get("statement"):
            criteria.append(Criterion(
                criterion_id=f"step:{order}:verification", statement=raw["statement"],
                required=bool(raw.get("required", True)), execution_run_node_id=node_id,
            ))
        # A malformed entry is skipped, same discipline as postconditions above.
    return criteria


def compute_verification_plan_id(procedure: dict) -> Optional[str]:
    """B3's literal `verification_plan_id` context field. A STABLE
    fingerprint of the ordered (criterion_id, statement, required) tuples
    `derive_criteria(procedure)` would produce for this exact procedure
    payload -- NOT a new stored entity (this module's own docstring:
    "postconditions ARE the plan"). `None` for a procedure with no real
    postconditions -- nothing to fingerprint, never a fabricated id for
    an empty plan."""
    criteria = derive_criteria(procedure)
    if not criteria:
        return None
    import hashlib
    import json

    fingerprint_input = json.dumps(
        [(c.criterion_id, c.statement, c.required) for c in criteria], sort_keys=True,
    )
    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()


async def _upsert_result(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    method: str, required: bool, state: str, evidence_refs: list,
    reviewer: Optional[str] = None, reviewed_targets: Optional[list] = None,
    criterion_answers: Optional[dict] = None, detail: Optional[str] = None,
    created_by: Optional[str] = None, execution_run_node_id: Optional[str] = None,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO verification_results (
            execution_run_id, criterion_id, statement, method, required, state,
            evidence_refs, reviewer, reviewed_targets, criterion_answers, detail, created_by,
            execution_run_node_id
        ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::uuid)
        ON CONFLICT (execution_run_id, criterion_id) DO UPDATE SET
            statement = EXCLUDED.statement, method = EXCLUDED.method,
            required = EXCLUDED.required, state = EXCLUDED.state,
            evidence_refs = EXCLUDED.evidence_refs, reviewer = EXCLUDED.reviewer,
            reviewed_targets = EXCLUDED.reviewed_targets,
            criterion_answers = EXCLUDED.criterion_answers, detail = EXCLUDED.detail,
            execution_run_node_id = EXCLUDED.execution_run_node_id
        RETURNING *
        """,
        execution_run_id, criterion_id, statement, method, required, state,
        evidence_refs or [], reviewer, reviewed_targets or [], criterion_answers or {},
        detail, created_by, execution_run_node_id,
    )
    from app.execution.recorder import record_verification_started
    await record_verification_started(pool, execution_run_id, criterion_id=criterion_id, method=method)
    return dict(row)


async def record_self_report(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    claimed_success: bool, required: bool = True, detail: Optional[str] = None,
    created_by: Optional[str] = None,
    execution_run_node_id: Optional[str] = None,
) -> dict:
    """SELF_REPORT can reach `claimed_done` on success or
    `failed_verification` on an admitted failure -- it can NEVER reach
    `checked`/`verified`/`independently_verified` (B34's own rule: "A
    self-report MUST NOT satisfy an independently verifiable
    deterministic criterion" -- generalized here to every stronger
    state, not just the deterministic one)."""
    state = "claimed_done" if claimed_success else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="self_report", required=required, state=state, evidence_refs=[],
        detail=detail, created_by=created_by, execution_run_node_id=execution_run_node_id,
    )


async def record_artifact_inspection(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    passed: bool, evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
    execution_run_node_id: Optional[str] = None,
) -> dict:
    """A real artifact was inspected (e.g. artifact_validation.py's own
    gate) -- reaches `checked` on success, one rung above a bare claim,
    still below a deterministic check's `verified`."""
    state = "checked" if passed else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="artifact_inspection", required=required, state=state,
        evidence_refs=evidence_refs or [], detail=detail, created_by=created_by, execution_run_node_id=execution_run_node_id,
    )


async def record_deterministic_check(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    passed: bool, evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
    execution_run_node_id: Optional[str] = None,
) -> dict:
    """A real, repeatable, machine-executed check (a command/probe) --
    reaches `verified` on success."""
    state = "verified" if passed else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="deterministic_check", required=required, state=state,
        evidence_refs=evidence_refs or [], detail=detail, created_by=created_by, execution_run_node_id=execution_run_node_id,
    )


async def record_independent_agent(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    passed: bool, evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
    execution_run_node_id: Optional[str] = None,
) -> dict:
    """A SEPARATE agent/model (not the one that performed the work)
    checked it -- reaches `independently_verified`, the strongest
    non-human-review state, on success."""
    state = "independently_verified" if passed else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="independent_agent", required=required, state=state,
        evidence_refs=evidence_refs or [], detail=detail, created_by=created_by, execution_run_node_id=execution_run_node_id,
    )


async def record_human_review(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    reviewer: str, reviewed_targets: list, criterion_answers: dict, verdict: bool,
    evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
    execution_run_node_id: Optional[str] = None,
) -> dict:
    """B34: "Do not accept approved=true without reviewer identity,
    reviewed targets, criterion answers, timestamp, and Evidence
    linkage." `reviewer`/`reviewed_targets` are required parameters
    here (no default), and migration 55's own CHECK constraint enforces
    the same rule at the DB layer as a backstop. `verdict=True` reaches
    `independently_verified` (a human reviewer is at least as strong a
    signal as an independent agent); `verdict=False` fails."""
    if not reviewer or not reviewed_targets:
        raise VerificationError(
            "human review requires a real reviewer identity and at least one reviewed target"
        )
    state = "independently_verified" if verdict else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="human_review", required=required, state=state, evidence_refs=evidence_refs or [],
        reviewer=reviewer, reviewed_targets=reviewed_targets, criterion_answers=criterion_answers,
        detail=detail, created_by=created_by, execution_run_node_id=execution_run_node_id,
    )


async def record_real_world_outcome(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, statement: str,
    passed: bool, evidence_refs: Optional[list] = None, required: bool = True,
    detail: Optional[str] = None, created_by: Optional[str] = None,
    execution_run_node_id: Optional[str] = None,
) -> dict:
    """An observed real-world effect (e.g. a production metric moved,
    a downstream system behaved as expected) -- reaches `verified`, the
    same rung as a deterministic check (both are real, observed outcomes;
    neither is inherently stronger than the other in general)."""
    state = "verified" if passed else "failed_verification"
    return await _upsert_result(
        pool, execution_run_id=execution_run_id, criterion_id=criterion_id, statement=statement,
        method="real_world_outcome", required=required, state=state,
        evidence_refs=evidence_refs or [], detail=detail, created_by=created_by, execution_run_node_id=execution_run_node_id,
    )


async def compute_verification_satisfaction(
    pool: asyncpg.Pool, *, execution_run_id: str, procedure: dict,
) -> dict:
    """
    The pure verification-satisfaction half of `evaluate_run_completion`,
    extracted so a caller can ask "is required verification satisfied"
    WITHOUT first requiring the run to already be terminal -- the
    question `durable_run.py::_finalize` (B16/B33's real enforcement
    point) must answer BEFORE it decides whether a run may become
    'succeeded' at all. `evaluate_run_completion` below calls this same
    function and layers the terminal-status/trace_id checks on top for
    its own caller-facing `procedure_run_complete` answer -- one real
    computation, two real callers, never two competing ones.

    Derives the real criteria from `procedure["postconditions"]`, loads
    whatever `verification_results` already exist for this run, and
    reports -- per criterion -- the strongest state reached, the
    evidence/method behind it, and which required criteria have NO
    result at all yet (`inconclusive`, never silently treated as
    passing). Never grades anything itself; a criterion with no recorded
    result stays `inconclusive` until a `record_*` call above is made
    for it.
    """
    criteria = derive_criteria(procedure)
    rows = await pool.fetch(
        "SELECT * FROM verification_results WHERE execution_run_id = $1::uuid", execution_run_id,
    )
    results_by_criterion = {r["criterion_id"]: dict(r) for r in rows}

    per_criterion = []
    for c in criteria:
        result = results_by_criterion.get(c.criterion_id)
        if result is None:
            per_criterion.append({
                "criterion_id": c.criterion_id, "statement": c.statement,
                "required": c.required, "state": "inconclusive", "method": None,
                "evidence_refs": [],
            })
        else:
            per_criterion.append({
                "criterion_id": c.criterion_id, "statement": c.statement,
                "required": c.required, "state": result["state"], "method": result["method"],
                "evidence_refs": result["evidence_refs"],
            })

    required_unmet = [
        c for c in per_criterion
        if c["required"] and c["state"] in ("inconclusive", "failed_verification")
    ]
    if any(c["state"] == "failed_verification" for c in per_criterion if c["required"]):
        overall = "failed_verification"
    elif required_unmet:
        overall = "inconclusive"
    elif per_criterion:
        # Every required criterion has a real, non-failing result --
        # the run's overall state is the WEAKEST rung among them (a
        # chain is only as strong as its weakest verified link).
        ranked = [c for c in per_criterion if c["required"]]
        overall = min(ranked, key=lambda c: _LADDER_RANK.get(c["state"], 0))["state"]
    else:
        # No criteria at all (the overwhelmingly common case today,
        # postconditions being almost universally empty) -- honestly
        # inconclusive, never silently "verified".
        overall = "inconclusive"

    return {
        "per_criterion": per_criterion, "overall": overall, "required_unmet": required_unmet,
    }


async def evaluate_run_completion(
    pool: asyncpg.Pool, *, execution_run_id: str, procedure: dict,
) -> dict:
    """
    `verify_completion`'s engine: see `compute_verification_satisfaction`
    for the criteria/ladder computation this wraps; this function adds
    the terminal-status/trace_id checks needed for a caller-facing
    `procedure_run_complete` answer.
    """
    satisfaction = await compute_verification_satisfaction(
        pool, execution_run_id=execution_run_id, procedure=procedure,
    )
    per_criterion = satisfaction["per_criterion"]
    overall = satisfaction["overall"]
    required_unmet = satisfaction["required_unmet"]

    from app.execution.recorder import record_verification_completed
    await record_verification_completed(
        pool, execution_run_id, overall_state=overall, criteria_count=len(per_criterion),
    )

    # B16/B33: "refusing to mark the Procedure complete until required
    # verification is satisfied" / "successful execution without
    # persisted terminal reporting is a system failure". `procedure_run_
    # complete` is the real, literal answer to that -- DISTINCT from
    # `overall_state` above (which answers "how strongly is the work
    # verified", an honest 'inconclusive' default even for a procedure
    # with NOTHING required). Completion requires: (a) the run reached a
    # REAL terminal state (never inferred from a caller's own claim),
    # (b) every REQUIRED criterion is satisfied -- vacuously true when
    # there are none (nothing required was never withheld), (c) a real
    # trace_id exists (B16's own named field, always populated since the
    # earlier B16 pass). Never silently coerced to True for a run that
    # doesn't meet this.
    run_row = await pool.fetchrow(
        "SELECT status, trace_id FROM execution_runs WHERE id = $1::uuid", execution_run_id,
    )
    missing_for_completion: list[str] = []
    if run_row is None:
        missing_for_completion.append("execution_run not found")
    else:
        if run_row["status"] not in ("succeeded", "failed"):
            missing_for_completion.append(f"run has not reached a terminal state (status={run_row['status']!r})")
        if required_unmet:
            missing_for_completion.append(
                f"{len(required_unmet)} required criterion/criteria not yet satisfied: "
                f"{[c['criterion_id'] for c in required_unmet]}"
            )
        if overall == "failed_verification":
            missing_for_completion.append("at least one required criterion failed verification")
        if run_row["trace_id"] is None:
            missing_for_completion.append("no trace_id recorded on this run")

    return {
        "execution_run_id": execution_run_id,
        "overall_state": overall,
        "criteria": per_criterion,
        "procedure_run_complete": not missing_for_completion,
        "missing_for_completion": missing_for_completion,
    }


class ReviewPacketError(Exception):
    """Raised when a bounded human-review packet cannot be generated
    honestly -- an unknown run, an unknown criterion_id, or a criterion
    whose method is not `human_review` at all. Never fabricates a packet
    for something that doesn't exist."""


async def generate_human_review_packet(
    pool: asyncpg.Pool, *, execution_run_id: str, criterion_id: str, procedure: dict,
) -> dict:
    """
    B34's own literal requirement: "For human review, generate a bounded
    review packet" naming exactly 7 fields -- built here from real,
    already-persisted facts only, never fabricated:

      objective                    -- this run's real pinned goal
                                       (`get_run_context`'s own `objective`
                                       field, the SAME value `continue_run`
                                       surfaces -- not re-derived).
      exact Procedure/version      -- `procedure["procedure_id"]`/
                                       `procedure["version"]`, the pinned
                                       identity this run is anchored to.
      criterion(s) being reviewed  -- the ONE real `Criterion` named by
                                       `criterion_id`, via `derive_criteria`
                                       -- `ReviewPacketError` if it does not
                                       exist on this pinned Procedure
                                       version.
      exact files/diff/ranges      -- every real `artifact_recorded` event
                                       on this run (B7/B8's own recorder,
                                       real sha256-hashed file refs) --
                                       honestly empty if none were ever
                                       recorded, never a guessed path.
      relevant Claim refs          -- B30's own real `get_relevant_claims`,
                                       keyed on the objective -- the SAME
                                       bounded retrieval `continue_run`
                                       already uses, not a second one.
      automated evidence already
      collected                    -- every OTHER real `verification_
                                       results` row already recorded for
                                       this run (self_report/artifact_
                                       inspection/deterministic_check/
                                       independent_agent/real_world_
                                       outcome) -- what a human reviewer
                                       should read before answering, not
                                       re-collected here.
      specific yes/no/structured
      questions                    -- ONE real, literal yes/no question
                                       grounded in the criterion's own
                                       `statement` -- never a vague "review
                                       the repo" (B34's own explicit
                                       prohibition).

    Raises `ReviewPacketError` for an unknown run or criterion_id --
    never returns a partial/guessed packet.
    """
    criteria_by_id = {c.criterion_id: c for c in derive_criteria(procedure)}
    criterion = criteria_by_id.get(criterion_id)
    if criterion is None:
        raise ReviewPacketError(
            f"unknown criterion_id {criterion_id!r} for this pinned procedure version "
            f"(known: {sorted(criteria_by_id)})"
        )

    run_row = await pool.fetchrow(
        "SELECT id FROM execution_runs WHERE id = $1::uuid", execution_run_id,
    )
    if run_row is None:
        raise ReviewPacketError(f"execution_run_id {execution_run_id!r} not found")

    from app.execution.durable_resume import get_run_context
    context = await get_run_context(pool, execution_run_id) or {}
    objective = context.get("objective") or procedure.get("goal") or procedure.get("name")

    from app.execution.recorder import get_run_events
    events = await get_run_events(pool, execution_run_id)
    target_artifacts = [
        {
            "kind": e["payload"].get("kind"), "ref": e["payload"].get("ref"),
            "sha256": e["payload"].get("sha256"), "size_bytes": e["payload"].get("size_bytes"),
        }
        for e in events if e["event_type"] == "artifact_recorded"
    ]

    relevant_claim_refs: list[dict] = []
    if objective:
        try:
            from app.services.relevant_claims import get_relevant_claims
            relevant_claim_refs = await get_relevant_claims(pool, goal=objective, top_k=5)
        except Exception:  # noqa: BLE001 -- informational; must never block packet generation
            relevant_claim_refs = []

    other_results = await pool.fetch(
        "SELECT criterion_id, statement, method, state, evidence_refs, detail "
        "FROM verification_results WHERE execution_run_id = $1::uuid AND criterion_id != $2",
        execution_run_id, criterion_id,
    )
    automated_evidence_collected = [dict(r) for r in other_results]

    return {
        "execution_run_id": execution_run_id,
        "objective": objective,
        "procedure_id": str(procedure.get("procedure_id")),
        "procedure_version": procedure.get("version"),
        "criterion": {
            "criterion_id": criterion.criterion_id, "statement": criterion.statement,
            "required": criterion.required,
        },
        "target_artifacts_or_files": target_artifacts,
        "relevant_claim_refs": relevant_claim_refs,
        "automated_evidence_already_collected": automated_evidence_collected,
        "questions": [
            {
                "id": f"{criterion.criterion_id}:confirm",
                "type": "yes_no",
                "text": f"Does the evidence above confirm the following is true: {criterion.statement!r}?",
            },
        ],
    }


# ---------------------------------------------------------------------------------------------------------------------
# Client-reported check evidence (migration 152, docs/evidence_upload_design.md).
#
# The client records one EVIDENCE line per check run and, with upload switched on, reports it through the MCP tool
# `report_check_evidence`. These rows have no execution run, so they do not use `verification_results`; they keep that
# table's core invariant: a caller NEVER passes a state. `local_check_state` computes it from (method, result), and the
# table's CHECK refuses any pairing this function could not produce.
#
# The server did not run these checks, so a reported pass is `checked` at most (compare execution/durable_run.py: a
# provider-reported result stays `unverified`). `verified` is not stored: the view `local_check_effective` derives it
# when two independent groups (distinct accounts) pass on the same check and the same files.
# ---------------------------------------------------------------------------------------------------------------------

import re as _re
import uuid as _uuid
from datetime import datetime, timedelta, timezone

# The only fields a report may carry. The client's whitelist (packaging/npm/lib/evidence_upload.mjs CHECK_EVIDENCE_FIELDS)
# must equal this tuple: a test compares the two, so neither can drift without the other.
LOCAL_CHECK_FIELDS: tuple[str, ...] = (
    "repo_ref", "target_kind", "target_id", "goal_id", "procedure_id", "method", "result", "observed_at", "commit_sha",
    "tree_id", "hashes", "where_ran", "check_hash", "client_event_id", "client_version", "reviewer_model", "executor_model",
)
LOCAL_CHECK_REQUIRED: tuple[str, ...] = (
    "repo_ref", "target_kind", "target_id", "method", "result", "observed_at", "check_hash", "client_event_id",
)
LOCAL_CHECK_METHODS: tuple[str, ...] = ("deterministic_check", "independent_agent")
LOCAL_CHECK_STATES: tuple[str, ...] = ("checked", "failed_verification")
# per signed-in account, and one bucket across all accounts (the abuse limits the other report tools use)
LOCAL_CHECK_RATE_HOURLY = 600
LOCAL_CHECK_RATE_DAILY = 5000
LOCAL_CHECK_RATE_GLOBAL_HOURLY = 20000

_RE_REPO_REF = _re.compile(r"^[a-z]:[0-9a-f]{16}$")
_RE_TARGET = _re.compile(r"^[NL]-[A-Za-z0-9_.-]{1,40}$")
_RE_HEX64 = _re.compile(r"^[0-9a-f]{64}$")
_RE_GIT = _re.compile(r"^[0-9a-f]{40}$")
_RE_SHA = _re.compile(r"^[0-9a-f]{4,64}$")
_RE_MODEL = _re.compile(r"^[A-Za-z0-9._:/-]{1,80}$")
_RE_VERSION = _re.compile(r"^[0-9A-Za-z.+-]{1,40}$")


def local_check_state(method: str, result: str) -> str:
    """The state of a client-reported check. A pass is `checked` at most, whatever the method: the server did not run it
    (`verified` needs corroboration, see the view). A fail is `failed_verification`. There is no way to ask for more."""
    if method not in LOCAL_CHECK_METHODS:
        raise VerificationError(f"method must be one of {LOCAL_CHECK_METHODS}")
    if result == "pass":
        return "checked"
    if result == "fail":
        return "failed_verification"
    raise VerificationError("result must be 'pass' or 'fail'")


def _uuid_str(value, label: str) -> str:
    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise VerificationError(f"{label} must be a uuid") from exc


def validate_local_check(payload) -> dict:
    """Refuse (never ignore) anything outside the whitelist, and every malformed value. Returns the cleaned fields."""
    if not isinstance(payload, dict):
        raise VerificationError("evidence must be a JSON object")
    extra = sorted(set(payload) - set(LOCAL_CHECK_FIELDS))
    if extra:
        raise VerificationError(f"fields not allowed: {extra} (allowed: {list(LOCAL_CHECK_FIELDS)})")
    missing = [k for k in LOCAL_CHECK_REQUIRED if payload.get(k) in (None, "")]
    if missing:
        raise VerificationError(f"missing required fields: {missing}")

    def text(key: str, rx, what: str) -> str:
        v = payload.get(key)
        if not isinstance(v, str) or not rx.match(v):
            raise VerificationError(f"{key} is not {what}")
        return v

    out: dict = {
        "repo_ref": text("repo_ref", _RE_REPO_REF, "a hashed repository identity"),
        "target_kind": payload["target_kind"],
        "target_id": text("target_id", _RE_TARGET, "a plan node or library entry id"),
        "method": payload["method"],
        "result": payload["result"],
        "check_hash": text("check_hash", _RE_HEX64, "a 64-hex HMAC"),
        "client_event_id": _uuid_str(payload["client_event_id"], "client_event_id"),
    }
    if out["target_kind"] not in ("library_entry", "plan_node"):
        raise VerificationError("target_kind must be library_entry or plan_node")
    if out["method"] not in LOCAL_CHECK_METHODS:
        raise VerificationError(f"method must be one of {LOCAL_CHECK_METHODS}")
    if out["result"] not in ("pass", "fail"):
        raise VerificationError("result must be 'pass' or 'fail'")
    try:
        observed = datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError("observed_at must be an ISO-8601 timestamp") from exc
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    if observed > datetime.now(timezone.utc) + timedelta(days=1):
        raise VerificationError("observed_at is in the future")
    out["observed_at"] = observed
    for key in ("commit_sha", "tree_id"):
        if payload.get(key) is not None:
            out[key] = text(key, _RE_GIT, "a 40-hex git object id")
    if payload.get("where_ran") is not None:
        if payload["where_ran"] not in ("worktree", "in-place"):
            raise VerificationError("where_ran must be worktree or in-place")
        out["where_ran"] = payload["where_ran"]
    for key in ("goal_id", "procedure_id"):
        if payload.get(key) is not None:
            out[key] = _uuid_str(payload[key], key)
    if payload.get("client_version") is not None:
        out["client_version"] = text("client_version", _RE_VERSION, "a version string")
    hashes = payload.get("hashes", [])
    if not isinstance(hashes, list) or len(hashes) > 500:
        raise VerificationError("hashes must be a list of at most 500 {path_hmac, sha} pairs")
    cleaned = []
    for h in hashes:
        if not isinstance(h, dict) or set(h) != {"path_hmac", "sha"}:
            raise VerificationError("each hashes item must be exactly {path_hmac, sha}")
        if not isinstance(h["path_hmac"], str) or not _RE_HEX64.match(h["path_hmac"]) or not isinstance(h["sha"], str) or not _RE_SHA.match(h["sha"]):
            raise VerificationError("hashes items need a 64-hex path_hmac and a 4-64 hex sha")
        cleaned.append({"path_hmac": h["path_hmac"], "sha": h["sha"]})
    out["hashes"] = cleaned
    reviewer, executor = payload.get("reviewer_model"), payload.get("executor_model")
    reviewer = reviewer.strip() if isinstance(reviewer, str) else reviewer
    executor = executor.strip() if isinstance(executor, str) else executor
    if out["method"] == "independent_agent":
        if not isinstance(reviewer, str) or not isinstance(executor, str) or not _RE_MODEL.match(reviewer) or not _RE_MODEL.match(executor):
            raise VerificationError("independent_agent evidence needs reviewer_model and executor_model")
        if reviewer.strip().lower() == executor.strip().lower():
            raise VerificationError(
                "the reviewer is the same model as the executor: independent_agent evidence needs a different model")
        out["reviewer_model"], out["executor_model"] = reviewer, executor
    elif reviewer is not None or executor is not None:
        raise VerificationError("reviewer_model and executor_model belong to independent_agent evidence only")
    return out


async def record_local_check(pool, *, payload, viewer_id: str, tenant_scope) -> dict:
    """Record one client-reported check line for `viewer_id`. The caller never supplies a state. Idempotent: the same
    (account, client_event_id) twice leaves one row. -> {recorded, duplicate, id, state, effective?}"""
    from app.services.access import bind_scope, tenant_transaction
    from app.services.v0_gate import validate_provenance, validate_scope
    from app.utils.ids import uuid7_str

    if not viewer_id:
        raise VerificationError("a signed-in account is required to report check evidence")
    clean = validate_local_check(payload)
    state = local_check_state(clean["method"], clean["result"])
    # V0 gate: the scope is the repository, the provenance says it came from the company's own machine and that the server
    # did not observe it. The client's version stands in for the extractor version.
    scope_type, scope_entity = validate_scope("repository", clean["repo_ref"])
    provenance = validate_provenance("company_ingested", derived=False)
    async with tenant_transaction(pool, tenant_scope) as conn:
        await bind_scope(conn, owner=viewer_id)
        if clean.get("goal_id") and not await conn.fetchval("SELECT 1 FROM goals WHERE id = $1::uuid", clean["goal_id"]):
            raise VerificationError(f"goal {clean['goal_id']} was not found")
        row = await conn.fetchrow(
            """
            INSERT INTO local_check_evidence (
                id, client_event_id, tenant_id, repo_ref, target_kind, target_id, goal_id, procedure_id, method, result, state,
                independence_group, observed_at, commit_sha, tree_id, hashes, where_ran, check_hash, reviewer_model,
                executor_model, client_version, provenance, extractor_version, created_by, visibility, owner_id, scope_type,
                scope_entity_id
            ) VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::uuid, $8::uuid, $9, $10, $11,
                $12, $13, $14, $15, $16::jsonb, $17, $18, $19,
                $20, $21, $22, $23, $24, 'private', $24, $25,
                $26
            )
            ON CONFLICT (owner_id, client_event_id) DO NOTHING
            RETURNING id, state
            """,
            uuid7_str(), clean["client_event_id"], str(tenant_scope.tenant_id), clean["repo_ref"], clean["target_kind"],
            clean["target_id"], clean.get("goal_id"), clean.get("procedure_id"), clean["method"], clean["result"], state,
            f"account:{viewer_id}", clean["observed_at"], clean.get("commit_sha"), clean.get("tree_id"),
            clean["hashes"], clean.get("where_ran"), clean["check_hash"], clean.get("reviewer_model"),
            clean.get("executor_model"), clean.get("client_version"), provenance, clean.get("client_version"), viewer_id,
            scope_type, scope_entity,
        )
        if row is None:                                            # a retry of a line already recorded
            existing = await conn.fetchrow(
                "SELECT id, state FROM local_check_evidence WHERE owner_id = $1 AND client_event_id = $2::uuid",
                viewer_id, clean["client_event_id"])
            return {"recorded": False, "duplicate": True, "id": str(existing["id"]) if existing else None,
                    "state": existing["state"] if existing else state}
    return {"recorded": True, "duplicate": False, "id": str(row["id"]), "state": row["state"]}


async def local_check_effective(pool, *, tenant_scope, repo_ref: str, target_id: str) -> list[dict]:
    """The effective state of each check on one target (view `local_check_effective`): counts of independent groups that
    pass and fail on the newest files reported, never another account's rows. Reads under the system scope because it
    counts across accounts; the tenant stays an explicit filter."""
    from app.services.access import scoped_transaction

    async with scoped_transaction(pool, system=True) as conn:
        rows = await conn.fetch(
            "SELECT check_hash, passing_groups, failing_groups, effective_state, last_observed_at "
            "FROM local_check_effective WHERE tenant_id = $1::uuid AND repo_ref = $2 AND target_id = $3 "
            "ORDER BY last_observed_at DESC",
            str(tenant_scope.tenant_id), repo_ref, target_id)
    return [dict(r) for r in rows]

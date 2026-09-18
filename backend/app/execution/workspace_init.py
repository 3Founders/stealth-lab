"""
`init_workspace` -- the "connect -> identify workspace -> bootstrap if
needed -> return continuation context" onboarding entrypoint this server
never had. `probe_environment()` (app.services.environment_facts) has
existed for a while but was only ever called from `reproduce_procedure`'s
own internal flow -- nothing exposed a real "an agent just connected to
this repo_path for the first time, set it up" tool. This module is that
tool's logic; `init_workspace` in server.py is a thin MCP wrapper.

IDEMPOTENT BY DESIGN: safe to call on every connect, not just once.
`_is_recognized_projection` is the one real signal this module trusts to
decide "have we been here before" -- a `.stealth/meta.json` whose
`schema` key starts with `stealth-projection` (the exact string
`generate_projection` stamps, see `app.stealth.generator`). Anything else
(no `.stealth/` at all, a `.stealth/` some other tool wrote, a corrupt
`meta.json`) is treated as a first connection -- bootstrapping again is
always safe (probing the filesystem and writing claims is itself
idempotent per (subject, predicate), see `assert_environment_claims`'s
own docstring; a fresh `generate_projection` call is idempotent by
construction).

NO FABRICATION: every fact this module persists as a Claim was either (a)
`probe_environment()`'s own existing 8 predicates (deterministic
filesystem reads, unchanged, reused verbatim via
`assert_environment_claims`), or (b) one of the five extra file-presence
checks this module adds (AGENTS.md / CLAUDE.md / README(.md) / a CI
config marker / a migrations-shaped directory) -- plain `os.path`
existence/listing checks, nothing inferred from file CONTENT beyond a
first-few-lines presence probe is ever attempted. A file that is absent
is reported as an honest `False` in the returned fact list; no Claim is
written for an absence (same "only the positive, defensible detection
becomes a Claim" discipline `environment_facts.py`'s own dependency
checks already use -- there is no `has_framework=none` claim there
either). Nothing here builds a general repo-understanding engine: five
mechanical existence checks, no more.

SECOND CONNECTION: reuses `app.execution.durable_resume.run_status_by_id`
/ `app.execution.run_collaboration.list_run_collaboration` -- the SAME
functions `inspect_run` / `record_run_update`'s own projection refresh
already use -- rather than inventing a second run-discovery mechanism.
The run id to look up comes from the LOCAL `.stealth/run.json`'s own
`procedure_run_id` field (written by the same `generate_projection` this
module's own last step calls) -- the local projection already durably
remembers which run was last worked in this workspace; nothing new is
introduced to track that.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import asyncpg

from app.stealth.legacy_context import STEALTH_DIRNAME

_DOC_FILES = ("AGENTS.md", "CLAUDE.md", "README.md", "README")
_DB_DIR_CANDIDATES = ("migrations", "alembic", "db")


def _is_recognized_projection(repo_path: str) -> bool:
    meta_path = os.path.join(repo_path, STEALTH_DIRNAME, "meta.json")
    if not os.path.isfile(meta_path):
        return False
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    # Recognizes both a full run-scoped projection (`stealth-projection/N`,
    # `app.stealth.generator`) AND this module's own minimal no-run
    # bootstrap marker (`stealth-workspace-init/N`, written below when a
    # repo has no execution run yet to project) -- either one means "this
    # server has already set this workspace up once".
    return isinstance(meta, dict) and str(meta.get("schema", "")).startswith("stealth-")


def _project_id_from_repo_path(repo_path: str) -> str:
    """Same derivation `procedure_extraction/__init__.py::
    _project_id_from_repo_root` already uses -- a stable, deterministic id
    from the real filesystem path, reused rather than a second convention
    invented here (both need the same property: the exact same repo_path
    always yields the exact same project_id, so `assert_environment_claims`'
    own idempotency-per-subject holds across repeated `init_workspace`
    calls against the same repo)."""
    from app.services.procedure_extraction import _project_id_from_repo_root
    return _project_id_from_repo_root(repo_path)


def detect_workspace_files(repo_path: str) -> dict[str, Any]:
    """Pure, synchronous, filesystem-only -- mirrors `probe_environment`'s
    own discipline (no LLM, no network, no content understanding beyond a
    plain existence/listing check). Returns
    `{doc_files: {name: bool}, has_ci_config: bool, db_dir: str|None}`."""
    doc_files = {name: os.path.isfile(os.path.join(repo_path, name)) for name in _DOC_FILES}

    workflows_dir = os.path.join(repo_path, ".github", "workflows")
    has_ci_config = False
    if os.path.isdir(workflows_dir):
        try:
            has_ci_config = any(
                f.endswith((".yml", ".yaml")) for f in os.listdir(workflows_dir)
            )
        except OSError:
            has_ci_config = False

    db_dir: Optional[str] = None
    for candidate in _DB_DIR_CANDIDATES:
        if os.path.isdir(os.path.join(repo_path, candidate)):
            db_dir = candidate
            break

    return {"doc_files": doc_files, "has_ci_config": has_ci_config, "db_dir": db_dir}


async def _persist_workspace_file_claims(
    pool: asyncpg.Pool, *, project_id: str, repo_path: str, facts: dict[str, Any], created_by: str,
) -> list[str]:
    """Same idempotent-per-(subject,predicate) writer idiom
    `assert_environment_claims` established (see that function's own
    docstring) -- reimplemented narrowly here rather than importing it,
    because that function's own fact source is hardcoded to
    `probe_environment()`'s 8 predicates and does not take an arbitrary
    fact list. ONLY positive detections are ever written (see module
    docstring's "no fabrication" section) -- an absent file writes
    nothing."""
    from app.services.claims import ClaimProperties
    from app.services.embeddings import Embedder, to_pgvector
    from app.services.state import project_state

    subject = f"project:{project_id}"
    positive: list[tuple[str, str]] = []
    for name, present in facts["doc_files"].items():
        if present:
            positive.append((f"has_doc_file:{name}", name))
    if facts["has_ci_config"]:
        positive.append(("has_ci_config", "github_actions"))
    if facts["db_dir"]:
        positive.append(("has_db_migrations_dir", facts["db_dir"]))

    if not positive:
        return []

    existing = await project_state(pool, subjects=[subject])
    existing_by_predicate = {c["predicate"]: c for c in existing}

    embedder = Embedder()
    written: list[str] = []
    async with pool.acquire() as conn:
        for predicate, obj in positive:
            prior = existing_by_predicate.get(predicate)
            if prior is not None and prior["object"] == obj:
                continue
            statement = f"{subject} {predicate}={obj}"
            validated = ClaimProperties(
                statement=statement, subject=subject, predicate=predicate, object=obj,
                truth_state="IN", claim_type="workspace_structure_fact",
                epistemic_status="observed", extraction_version="init_workspace:1",
            )
            embedding = await embedder.embed_one(statement, input_type="document")
            new_id = await conn.fetchval(
                "INSERT INTO knowledge_nodes "
                "(node_type, name, properties, embedding, created_by, provenance) "
                "VALUES ('claim', $1, $2, $3::vector, $4, 'company_ingested') "
                "RETURNING id",
                statement[:200], validated.model_dump(exclude_none=True),
                to_pgvector(embedding), created_by,
            )
            written.append(str(new_id))
    return written


async def _read_local_run_id(repo_path: str) -> Optional[str]:
    run_json_path = os.path.join(repo_path, STEALTH_DIRNAME, "run.json")
    try:
        with open(run_json_path, encoding="utf-8") as f:
            run_json = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    run_id = run_json.get("procedure_run_id")
    return str(run_id) if run_id else None


async def _continuation_context(pool: asyncpg.Pool, repo_path: str) -> dict[str, Any]:
    """Second-connection path: find the run this workspace was last
    working (from its own local `run.json`, written by the SAME
    `generate_projection` this module's caller already refreshes), and
    surface enough of its state that a fresh agent -- no prior chat
    history -- could continue without re-asking. Reuses
    `run_status_by_id`/`list_run_collaboration` verbatim; no new
    run-discovery mechanism."""
    run_id = await _read_local_run_id(repo_path)
    if run_id is None:
        return {"active_run": None, "reason": "no run.json on record for this workspace"}

    from app.execution import durable_resume as _dres
    from app.execution.run_collaboration import list_run_collaboration

    status = await _dres.run_status_by_id(pool, run_id)
    if status is None:
        return {"active_run": None, "reason": f"run {run_id!r} recorded locally no longer exists"}

    collab = await list_run_collaboration(pool, run_id)
    answered_ids = {c["answers_id"] for c in collab if c["kind"] == "ANSWER" and c.get("answers_id")}
    resolved_blocker_ids = {c["answers_id"] for c in collab if c["kind"] == "BLOCKER_RESOLVED" and c.get("answers_id")}
    accepted_handoff_ids = {c["answers_id"] for c in collab if c["kind"] == "HANDOFF_ACCEPTED" and c.get("answers_id")}
    open_blockers = [c for c in collab if c["kind"] == "BLOCKER" and str(c["id"]) not in {str(x) for x in resolved_blocker_ids}]
    pending_handoffs = [c for c in collab if c["kind"] == "HANDOFF" and str(c["id"]) not in {str(x) for x in accepted_handoff_ids}]
    unanswered_questions = [c for c in collab if c["kind"] == "QUESTION" and str(c["id"]) not in {str(x) for x in answered_ids}]

    def _brief(rows: list[dict]) -> list[dict]:
        return [
            {"id": str(r["id"]), "node_order": r.get("node_order"), "actor": r["actor_agent_id"], "body": r["body"]}
            for r in rows
        ]

    return {
        "active_run": run_id,
        "run_status": status,
        "open_blockers": _brief(open_blockers),
        "pending_handoffs": _brief(pending_handoffs),
        "unanswered_questions": _brief(unanswered_questions),
        "is_terminal": bool(status.get("status") in ("succeeded", "failed", "cancelled")),
    }


def _write_bootstrap_marker(
    repo_path: str, *, project_id: str, environment_facts: list[dict[str, str]],
    workspace_facts: dict[str, Any], first_connection: bool,
) -> str:
    """Write `.stealth/meta.json` (+ a human-readable `workspace.md`) for a
    repo with no execution run yet -- the no-run counterpart of
    `generate_projection`'s own atomic write, deliberately smaller (no
    run/claims/procedures pages exist to render without a run). Returns
    `"written"`. Raises `OSError` on a real filesystem failure -- the
    caller treats that as best-effort, same as every other `.stealth/`
    write path in this codebase."""
    from datetime import datetime, timezone

    from app.stealth.atomic import atomic_write_batch

    stealth_dir = os.path.join(repo_path, STEALTH_DIRNAME)
    os.makedirs(stealth_dir, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    meta = {
        "schema": "stealth-workspace-init/1",
        "workspace_id": project_id,
        "generated_at": now_iso,
        "first_connection": first_connection,
        "active_run": None,
        "note": "no execution run exists for this workspace yet -- global retrieval "
                "(search_procedures/find_best_way/...) remains fully available; this "
                "marker only means no run-scoped .stealth/ working set has been "
                "generated here yet.",
    }
    lines = [
        "# workspace.md -- GENERATED, not canonical. Do not hand-edit.",
        f"# project_id={project_id}",
        "",
        "## environment_facts (probe_environment)",
    ]
    if environment_facts:
        lines += [f"FACT|{f['predicate']}|{f['object']}" for f in environment_facts]
    else:
        lines.append("(none observed)")
    lines += ["", "## workspace_facts"]
    doc_files = workspace_facts.get("doc_files") or {}
    if doc_files:
        lines += [f"DOC_FILE|{name}|{'present' if present else 'absent'}" for name, present in doc_files.items()]
    lines.append(f"CI_CONFIG|{'present' if workspace_facts.get('has_ci_config') else 'absent'}")
    lines.append(f"DB_DIR|{workspace_facts.get('db_dir') or 'absent'}")

    plan = [
        (os.path.join(stealth_dir, "workspace.md"), "\n".join(lines) + "\n"),
        (os.path.join(stealth_dir, "meta.json"), json.dumps(meta, indent=2, default=str)),
    ]
    atomic_write_batch(plan)
    return "written"


async def init_workspace(pool: asyncpg.Pool, repo_path: str, *, created_by: str = "init_workspace") -> dict[str, Any]:
    """The real bootstrap/onboarding logic behind the `init_workspace` MCP
    tool. See module docstring for the idempotency/no-fabrication rules.

    Returns `{repo_path, first_connection: bool, environment_facts: [...],
    workspace_facts: {...}, claims_written: [...], continuation: {...}|None,
    projection: "written"|"write_failed: ..."}`.
    """
    if not os.path.isdir(repo_path):
        raise NotADirectoryError(f"repo_path {repo_path!r} is not a directory on this server.")

    first_connection = not _is_recognized_projection(repo_path)
    project_id = _project_id_from_repo_path(repo_path)

    environment_fact_dicts: list[dict[str, str]] = []
    workspace_facts: dict[str, Any] = {}
    claims_written: list[str] = []

    if first_connection:
        from app.services.environment_probe import assert_environment_claims, probe_environment

        facts = probe_environment(repo_path)
        environment_fact_dicts = [{"predicate": f.predicate, "object": f.object} for f in facts]
        env_claim_ids = await assert_environment_claims(pool, project_id=project_id, repo_root=repo_path, created_by=created_by)

        workspace_facts = detect_workspace_files(repo_path)
        file_claim_ids = await _persist_workspace_file_claims(
            pool, project_id=project_id, repo_path=repo_path, facts=workspace_facts, created_by=created_by,
        )
        claims_written = env_claim_ids + file_claim_ids

    continuation = None if first_connection else await _continuation_context(pool, repo_path)

    # --- best-effort projection refresh, last step (same convention as
    # find_best_way/continue_run/record_run_update: never turn a write
    # failure into a raised exception here -- surfaced in the return value) -
    run_id_for_projection = await _read_local_run_id(repo_path)
    if run_id_for_projection:
        from app.execution.stealth_projection import generate_projection
        try:
            await generate_projection(pool, workspace_root=repo_path, procedure_run_id=run_id_for_projection)
            projection = "written"
        except Exception as exc:  # noqa: BLE001 -- best-effort, mirrors record_run_update's own catch-all
            projection = f"write_failed: {exc}"
    else:
        # No execution run exists for this workspace yet -- `generate_
        # projection` is run-scoped and has nothing to project. Still write
        # a minimal, honest `.stealth/` marker (product spec Case 1: an
        # empty/new repo still gets ".stealth/ projection created", with
        # global retrieval remaining available and no fabricated Claims) --
        # NOT a fake run-scoped projection, a distinct, smaller schema this
        # module owns (see `_is_recognized_projection`).
        try:
            projection = _write_bootstrap_marker(
                repo_path, project_id=project_id, environment_facts=environment_fact_dicts,
                workspace_facts=workspace_facts, first_connection=first_connection,
            )
        except OSError as exc:
            projection = f"write_failed: {exc}"

    return {
        "repo_path": repo_path,
        "project_id": project_id,
        "first_connection": first_connection,
        "environment_facts": environment_fact_dicts,
        "workspace_facts": workspace_facts,
        "claims_written": claims_written,
        "continuation": continuation,
        "projection": projection,
    }

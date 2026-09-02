"""
DB-facing half of environment probing. The pure, dependency-free
detection logic (PROBE_PREDICATE_VOCABULARY, EnvironmentFact,
probe_environment, invariant_bindings_from_facts) lives in
environment_facts.py and is re-exported here unchanged -- see that
module's docstring for why the split is real (a caller like
app.local_agent.runner must have ZERO database dependency, and this
file's own imports -- claims/state/embeddings -- pull in asyncpg at
their own top level).

This module keeps ONLY what genuinely needs the database:
assert_environment_claims(), the one async, DB-touching entry point.
Same boundary discipline as call_graph.py/code_index.py/import_deps.py:
host-side analysis functions are pure and side-effect free; the write
path is the only place that isn't.

WHY THIS IS A HARD PREREQUISITE, NOT A LATER NICETY: procedure
extraction's whole design (see procedure_extraction/derive.py) derives
`preconditions` from `project_state(as_of=episode_start)` -- the
episode's state_before projection, per migration 18's own comment
("structured predicates derived from the source episode's state_before
projection... NOT hand-authored tags"). If nothing has ever asserted an
environment claim, project_state() returns [], and a procedure extracted
from that episode has NO preconditions at all -- silently unconstrained,
not silently correct. This module is what makes the derivation have
something real to find.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.services.claims import ClaimProperties
from app.services.embeddings import Embedder, to_pgvector
from app.services.environment_facts import (
    PROBE_PREDICATE_VOCABULARY,
    EnvironmentFact,
    invariant_bindings_from_facts,
    probe_environment,
    probe_installed_package_versions,
    probe_python_version,
)
from app.services.state import project_state

if TYPE_CHECKING:
    import asyncpg

__all__ = [
    "PROBE_PREDICATE_VOCABULARY",
    "EnvironmentFact",
    "invariant_bindings_from_facts",
    "probe_environment",
    "probe_installed_package_versions",
    "probe_python_version",
    "assert_environment_claims",
    "CREATED_BY",
]

CREATED_BY = "environment_probe"


def _subject_for(project_id: str) -> str:
    """The claim subject convention environment facts use -- distinct
    from a task's skill_ref (capture_claim's subject convention) because
    an environment fact is about the PROJECT, not about any one task.
    Matches project_id's own meaning (migration 17: derived from a real
    transcript's cwd/gitBranch, stable per checkout)."""
    return f"project:{project_id}"


async def assert_environment_claims(
    pool: "asyncpg.Pool",
    *,
    project_id: str,
    repo_root: str,
    embedder: Optional[Embedder] = None,
    created_by: str = CREATED_BY,
) -> list[str]:
    """
    Real DB write path. Deliberately does NOT go through claims.py's
    capture_claim() -- that function requires task_ids resolving to a
    live task_node, and drops the claim silently otherwise ("a claim
    that supports nothing has nothing to link to"). An environment fact
    is not about a task, it's about a project; forcing it through a
    synthetic/fake task_node would be a hack this module refuses.
    project_state() (which reads claims for applicability) needs no
    task_node edge at all -- it queries knowledge_nodes directly by
    subject -- so this function writes the same claim SHAPE
    (ClaimProperties-validated properties, same embedding discipline)
    without capture_claim's task-linkage requirement.

    Idempotent per (subject, predicate): if a live claim with the SAME
    object already exists, this is a no-op (re-probing an unchanged repo
    must not create duplicate rows). If a live claim exists with a
    DIFFERENT object (the repo's environment genuinely changed -- a
    build tool was swapped), the old claim is superseded via
    claims.relate_claims(), preserving history rather than overwritten
    in place -- same discipline every other claim in this codebase uses.

    Returns the ids of claims newly written this call (empty if nothing
    changed).
    """
    from app.services.claims import relate_claims  # local import: avoids a
    # circular dependency at module load time (claims.py does not import
    # this module, but importing it at top-level here is unnecessary
    # coupling for the one function that needs it).

    facts = probe_environment(repo_root)
    if not facts:
        return []

    subject = _subject_for(project_id)
    embedder = embedder or Embedder()

    existing = await project_state(pool, subjects=[subject])
    existing_by_predicate: dict[str, dict] = {c["predicate"]: c for c in existing}

    written: list[str] = []
    async with pool.acquire() as conn:
        for fact in facts:
            prior = existing_by_predicate.get(fact.predicate)
            if prior is not None and prior["object"] == fact.object:
                continue  # unchanged -- no-op, not a duplicate write

            statement = f"{subject} {fact.predicate}={fact.object}"
            validated = ClaimProperties(
                statement=statement,
                subject=subject,
                predicate=fact.predicate,
                object=fact.object,
                truth_state="IN",
                claim_type="environment_fact",
                epistemic_status="observed",  # deterministic filesystem read,
                # never model-derived -- ticket 10's exact distinction.
                extraction_version=f"{CREATED_BY}:1",
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

            if prior is not None:
                await relate_claims(
                    pool, from_claim_id=str(new_id), to_claim_id=prior["id"],
                    relation="SUPERSEDES", created_by=created_by,
                )

    return written

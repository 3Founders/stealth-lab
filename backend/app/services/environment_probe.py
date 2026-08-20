"""
Deterministic environment probe (prerequisite for procedure extraction,
memory-substrate map). Reads a real checkout and asserts environment
CLAIMS about it -- has_framework, has_test_runner, package_manager,
language -- so applicability.py's preconditions have something real to
be checked against.

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

THIS MODULE OWNS THE PREDICATE VOCABULARY procedure_extraction's V1
validator checks imported/LLM-supplied preconditions against
(PROBE_PREDICATE_VOCABULARY, below) -- one exported constant, so probe
and validator cannot drift apart. Anything this module doesn't assert is
not a real precondition anyone can check yet.

Deterministic, no LLM, no network -- pure filesystem reads plus one real
DB write path. Same boundary discipline as call_graph.py/code_index.py/
import_deps.py: host-side analysis functions are pure and side-effect
free; the one function that writes (assert_environment_claims) is the
only async, DB-touching entry point.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import asyncpg

from app.services.claims import ClaimProperties
from app.services.embeddings import Embedder, to_pgvector
from app.services.state import project_state

CREATED_BY = "environment_probe"

# The single source of truth for "which predicates can a precondition
# name and actually be checked". procedure_extraction/validators.py's V1
# rule imports this exact constant rather than re-declaring the list --
# that duplication is precisely how probe and validator would drift.
PROBE_PREDICATE_VOCABULARY: tuple[str, ...] = (
    "has_framework",
    "has_build_tool",
    "has_test_runner",
    "has_dev_server",
    "package_manager",
    "language",
)


def _subject_for(project_id: str) -> str:
    """The claim subject convention environment facts use -- distinct
    from a task's skill_ref (capture_claim's subject convention) because
    an environment fact is about the PROJECT, not about any one task.
    Matches project_id's own meaning (migration 17: derived from a real
    transcript's cwd/gitBranch, stable per checkout)."""
    return f"project:{project_id}"


@dataclass
class EnvironmentFact:
    predicate: str
    object: str


def _read_json(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


_FRAMEWORK_DEPS = (
    ("next", "next"),
    ("react", "react"),
    ("vue", "vue"),
    ("svelte", "svelte"),
    ("@angular/core", "angular"),
)
_BUILD_TOOL_DEPS = (
    ("vite", "vite"),
    ("webpack", "webpack"),
    ("next", "next"),  # next.config.* covers this too; dep-based check first
    ("esbuild", "esbuild"),
)
_TEST_RUNNER_DEPS = (
    ("jest", "jest"),
    ("vitest", "vitest"),
    ("mocha", "mocha"),
    ("@playwright/test", "playwright"),
)
_DEV_SERVER_DEPS = (
    ("vite", "vite"),
    ("next", "next"),
    ("webpack-dev-server", "webpack-dev-server"),
)


def _js_facts(root: str) -> list[EnvironmentFact]:
    """
    package.json-derived facts. Real, not guessed: checks the ACTUAL
    dependency/devDependency keys, not filename heuristics -- a repo
    with vite.config.js but no `vite` dependency (a vendored config, a
    monorepo root) should not assert has_build_tool=vite.
    """
    pkg = _read_json(os.path.join(root, "package.json"))
    if pkg is None:
        return []

    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    facts: list[EnvironmentFact] = []

    for dep_name, value in _FRAMEWORK_DEPS:
        if dep_name in deps:
            facts.append(EnvironmentFact("has_framework", value))
            break  # first match wins -- a project has one primary framework claim,
            # not a set; ambiguous multi-framework repos are a real case this
            # first pass doesn't model, stated rather than silently guessed at.

    for dep_name, value in _BUILD_TOOL_DEPS:
        if dep_name in deps:
            facts.append(EnvironmentFact("has_build_tool", value))
            break

    for dep_name, value in _TEST_RUNNER_DEPS:
        if dep_name in deps:
            facts.append(EnvironmentFact("has_test_runner", value))
            break

    for dep_name, value in _DEV_SERVER_DEPS:
        if dep_name in deps:
            facts.append(EnvironmentFact("has_dev_server", value))
            break

    if os.path.isfile(os.path.join(root, "package-lock.json")):
        facts.append(EnvironmentFact("package_manager", "npm"))
    elif os.path.isfile(os.path.join(root, "yarn.lock")):
        facts.append(EnvironmentFact("package_manager", "yarn"))
    elif os.path.isfile(os.path.join(root, "pnpm-lock.yaml")):
        facts.append(EnvironmentFact("package_manager", "pnpm"))

    return facts


def _python_facts(root: str) -> list[EnvironmentFact]:
    """
    Language + test-runner + package-manager facts for a Python
    checkout. `language` is asserted whenever ANY real signal for it
    exists (pyproject.toml/requirements.txt/setup.py/setup.cfg) -- the
    weakest of this module's checks, deliberately: file presence alone,
    no dependency parsing, because Python's manifest format is not one
    file the way package.json is.
    """
    facts: list[EnvironmentFact] = []
    has_pyproject = os.path.isfile(os.path.join(root, "pyproject.toml"))
    has_requirements = os.path.isfile(os.path.join(root, "requirements.txt"))
    has_setup = (os.path.isfile(os.path.join(root, "setup.py"))
                 or os.path.isfile(os.path.join(root, "setup.cfg")))
    if has_pyproject or has_requirements or has_setup:
        facts.append(EnvironmentFact("language", "python"))

    if has_pyproject:
        try:
            with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = ""
        if "poetry" in content:
            facts.append(EnvironmentFact("package_manager", "poetry"))
        elif has_requirements:
            facts.append(EnvironmentFact("package_manager", "pip"))
    elif has_requirements:
        facts.append(EnvironmentFact("package_manager", "pip"))

    # Test-runner presence: real dependency-name check against
    # requirements.txt content, same discipline as _js_facts -- not
    # filename-only.
    combined_deps = ""
    if has_requirements:
        try:
            with open(os.path.join(root, "requirements.txt"), encoding="utf-8") as f:
                combined_deps = f.read().lower()
        except OSError:
            pass
    if "pytest" in combined_deps or os.path.isdir(os.path.join(root, "tests")):
        # Directory presence alone is a weaker signal than the dependency
        # check -- kept as an OR because many real repos (this one
        # included) put tests/ at the project root without pytest ever
        # appearing in a plain requirements.txt (it's in a separate
        # dev-requirements file, or installed ambiently). Flagging this
        # honestly rather than hiding the weaker branch: a caller that
        # needs high precision should prefer the dependency-based signal
        # and treat the directory-only case as advisory.
        facts.append(EnvironmentFact("has_test_runner", "pytest"))

    return facts


def probe_environment(repo_root: str) -> list[EnvironmentFact]:
    """
    Pure, synchronous, filesystem-only. Every predicate returned here is
    in PROBE_PREDICATE_VOCABULARY by construction -- there is no other
    code path that produces an EnvironmentFact, so this function cannot
    drift out of sync with the vocabulary it defines.
    """
    if not os.path.isdir(repo_root):
        return []
    facts = _js_facts(repo_root) + _python_facts(repo_root)
    # De-duplicate same (predicate, object) pairs a repo might trigger
    # from more than one heuristic (e.g. package_manager asserted once
    # is enough); order-preserving, not a set, so facts stay reproducible
    # for tests that assert exact output.
    seen: set[tuple[str, str]] = set()
    deduped: list[EnvironmentFact] = []
    for fact in facts:
        key = (fact.predicate, fact.object)
        if key not in seen:
            seen.add(key)
            deduped.append(fact)
    return deduped


async def assert_environment_claims(
    pool: asyncpg.Pool,
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

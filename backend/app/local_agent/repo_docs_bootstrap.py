"""
Repo procedural-document bootstrap adapter -- private-by-default fix.

`scripts/bootstrap.py`'s `--repo-root` source used to route SKILL.md /
AGENTS.md / CLAUDE.md / CI-workflow / runbook documents through
`app/services/skill_ingestion.py::run_skill_ingestion()`, which persists
via `capture_procedure()` into the SHARED global Postgres `procedures`
table (needs DATABASE_URL). That breaks the bootstrap promise every other
local source honours (git history, chat exports): a personal repo's own
docs are exactly the kind of private, one-workspace material
`local_store.py`'s module docstring describes, not something that should
default to a shared table.

This module is the same shape as `git_history_bootstrap.py` sitting next
to it: it drives the EXISTING, UNTOUCHED source adapters
(`app/services/ingestion_sources/skill_md.py`'s `LocalDirSkillSource` and
`app/services/ingestion_sources/repo_procedural.py`'s
`LocalDirAgentsMdSource` / `LocalDirCIWorkflowSource` /
`LocalDirRunbookSource`) for `discover()`/`fetch()`, reuses
`skill_ingestion.py`'s existing PURE parser `parse_skill_md()` UNCHANGED
(it already takes a content string and returns a `ParsedSkill` with no
persistence side effect -- nothing needed extracting, the parse/persist
split already existed), and writes the result through
`LocalProcedureStore`'s existing PUBLIC interface
(`capture_local_procedure` / `merge_bootstrap_evidence`) instead of
`capture_procedure()`. `skill_ingestion.py` itself is untouched: any other
caller of `run_skill_ingestion()` / `compile_skill_artifact()` /
`ingest_skill_md()` keeps its current Postgres-backed behavior verbatim.

CONSERVATISM / PROVENANCE CONTRACT (mirrors `git_history_bootstrap.py`):
  - Every candidate is born `provenance="prior_library"`,
    `verification_state="candidate"` (via `capture_local_procedure`'s own
    always-candidate posture) -- a repo doc proves someone wrote a
    procedure down, never that it was executed or verified.
  - Source path, source type, and content hash are recorded on every
    captured item (evidence_refs + scope.repo_docs), same discipline the
    git-history adapter uses for commit SHAs.
  - `SkillMdParseError` (an adapter's own gate already filtered obvious
    non-candidates before `fetch()`; parse can still reject an
    edge-case-empty document) is counted as skipped, never fabricated.

DEDUP: reuses the exact same light heuristic
`git_history_bootstrap.py::_find_merge_target` uses -- an embedding
similarity >=0.90, OR an existing row whose own `repo_docs` scope block
shares the source path and whose goal text shares a word. A false
non-merge (two rows) is preferred over an unsafe false merge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.local_agent.local_store import LocalProcedureStore, _cosine_similarity
from app.services.skill_ingestion import ParsedSkill, SkillMdParseError, parse_skill_md

PROVENANCE = "prior_library"

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class RepoDocCandidate:
    source_type: str  # "skill_md" | "agents_md" | "ci_workflow" | "runbook"
    path: str
    uri: str
    repository: str
    content_hash: str
    parsed: ParsedSkill

    @property
    def goal(self) -> str:
        return self.parsed.description or self.parsed.name

    def steps(self) -> list[dict]:
        return [
            {"order": i, "goal": s, "properties": {"evidence_status": "recommended"}}
            for i, s in enumerate(self.parsed.steps)
        ]

    def evidence_ref(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_id": self.content_hash,
            "source_location": f"{self.repository}:{self.path}",
            "path": self.path,
            "uri": self.uri,
            "evidence_status": "recommended",
            "privacy": "local",
        }


# One (adapter class, source_type) pair per repo-procedural document kind
# this bootstraps -- the exact same four adapters
# `scripts/bootstrap.py::run_repo_procedural` already drives for the
# global path, imported here unmodified.
def _default_adapters(root):
    from app.services.ingestion_sources.repo_procedural import (
        LocalDirAgentsMdSource,
        LocalDirCIWorkflowSource,
        LocalDirRunbookSource,
    )
    from app.services.ingestion_sources.skill_md import LocalDirSkillSource

    return [
        LocalDirSkillSource(root),
        LocalDirAgentsMdSource(root),
        LocalDirCIWorkflowSource(root),
        LocalDirRunbookSource(root),
    ]


def discover_repo_doc_candidates(root, *, adapters=None) -> tuple[list[RepoDocCandidate], int]:
    """Walk every adapter's real `discover()`/`fetch()`, parse each
    artifact with the shared pure `parse_skill_md()`. Returns
    (candidates, skipped_unparseable) -- never raises for a document an
    adapter's own gate let through but that still turns out empty."""
    adapters = adapters if adapters is not None else _default_adapters(root)
    candidates: list[RepoDocCandidate] = []
    skipped = 0
    for adapter in adapters:
        for ref in adapter.discover():
            artifact = adapter.fetch(ref)
            try:
                parsed = parse_skill_md(artifact.content, fallback_name=artifact.path or "unnamed")
            except SkillMdParseError:
                skipped += 1
                continue
            candidates.append(RepoDocCandidate(
                source_type=artifact.source_type,
                path=artifact.path or "",
                uri=artifact.uri,
                repository=artifact.repository or "",
                content_hash=artifact.content_hash,
                parsed=parsed,
            ))
    return candidates, skipped


def _find_merge_target(
    store: LocalProcedureStore,
    candidate: RepoDocCandidate,
    query_embedding: Optional[list[float]],
) -> Optional[dict]:
    matches = store.search_local_procedures(candidate.goal, query_embedding=query_embedding, limit=5)
    goal_words = set(_WORD_RE.findall(candidate.goal.lower()))
    for row in matches:
        if query_embedding is not None and row.get("embedding"):
            if _cosine_similarity(query_embedding, row["embedding"]) >= 0.90:
                return row
        scope = row.get("scope") or {}
        existing_path = (scope.get("repo_docs") or {}).get("path")
        if not existing_path or existing_path != candidate.path:
            continue
        row_words = set(_WORD_RE.findall((row.get("name") or "").lower()))
        if goal_words & row_words:
            return row
    return None


def converge_repo_doc_candidate(
    store: LocalProcedureStore,
    candidate: RepoDocCandidate,
    *,
    workspace_entity_id: Optional[str] = None,
    embedding: Optional[list[float]] = None,
) -> dict:
    target = _find_merge_target(store, candidate, embedding)
    evidence_ref = candidate.evidence_ref()
    if target is not None:
        store.merge_bootstrap_evidence(
            target["id"], evidence_ref=evidence_ref, source_episode_id=candidate.content_hash,
        )
        return {"status": "merged", "id": target["id"], "procedure_id": target["procedure_id"]}

    result = store.capture_local_procedure(
        name=candidate.parsed.name,
        goal=candidate.goal,
        steps=candidate.steps(),
        scope={
            "bootstrap_sources": [evidence_ref],
            "repo_docs": {
                "source_type": candidate.source_type,
                "path": candidate.path,
                "repository": candidate.repository,
                "applies_when": candidate.parsed.applies_when,
                "frontmatter": candidate.parsed.frontmatter,
            },
        },
        evidence_refs=[evidence_ref],
        source_episode_ids=[candidate.content_hash],
        provenance=PROVENANCE,
        scope_type="repository",
        scope_entity_id=workspace_entity_id or candidate.repository,
        embedding=embedding,
    )
    return {"status": "captured", **result}


def bootstrap_repo_docs(
    store: LocalProcedureStore,
    repo_root: str,
    *,
    embed=None,
    workspace_entity_id: Optional[str] = None,
    adapters=None,
) -> dict:
    """The whole repo-procedural-docs bootstrap pass, private by default:
    walk the real adapters, parse with the shared pure parser, converge
    each candidate into `store` (SQLite, per-workspace) -- never into the
    shared Postgres `procedures` table. Returns an honest summary mirroring
    `bootstrap_git_history()`'s shape."""
    import asyncio

    candidates, skipped = discover_repo_doc_candidates(repo_root, adapters=adapters)
    summary = {
        "artifacts_seen": len(candidates) + skipped,
        "candidates_formed": len(candidates),
        "skipped_unparseable": skipped,
        "captured": 0, "merged": 0, "ids": [],
    }
    loop = None
    for candidate in candidates:
        embedding = None
        if embed is not None:
            try:
                loop = loop or asyncio.new_event_loop()
                embedding = loop.run_until_complete(embed(candidate.goal, input_type="document"))
            except Exception:  # noqa: BLE001 -- embedding is optional; degrade honestly
                embedding = None
        result = converge_repo_doc_candidate(
            store, candidate, workspace_entity_id=workspace_entity_id, embedding=embedding,
        )
        summary[result["status"]] += 1
        summary["ids"].append(result["id"])
    if loop is not None:
        loop.close()
    return summary

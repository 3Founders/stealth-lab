"""
Git/code-history bootstrap adapter -- fills the gap `scripts/bootstrap.py`
itself documents ("Git history is NOT included: this repo has no dedicated
git-log/commit-history procedural-source adapter"). Walks a REAL git
repository's commit history via the `git` CLI (subprocess -- no GitPython;
not a project dependency, see `pyproject.toml`) and converges genuine
workflow PATTERNS -- never single commits -- into the same
`LocalProcedureStore` every other local source writes into.

SELF-CONTAINED ON PURPOSE: `app/local_agent/historical_bootstrap.py` covers
adjacent historical sources (repo SKILL.md/AGENTS.md, chat exports, agent
traces) and this module could in principle share its `HistoricalEpisode`
shape and conservatism constants -- but that file is uncommitted WIP from a
concurrent session at the time this module was written, so nothing here
imports from it. Duplication of a handful of small constants is the
correct trade against depending on a moving target; `local_store.py` and
`v0_gate.py` (both stable, committed) are the only shared substrate.

CONSERVATISM CONTRACT (mirrors the repo's stated posture for every other
historical source -- CLAUDE.md's "Docstrings document *why*, including
honest scope limits"):
  - A commit, alone, is evidence a change happened -- NOT evidence of a
    reusable method. No bare commit is ever promoted to a candidate
    procedure just because its subject line looks like a fix.
  - A candidate is formed ONLY when a real cross-commit WORKFLOW PATTERN is
    observed: a fix-shaped commit whose child commit (real parent/child
    edge, not merely adjacent in `git log` output) touches test files, or
    a small (<=3 commit) migration+code+test triad along a single
    ancestry line touching overlapping paths. Everything else -- solo
    fixes, docs-only commits, unrelated churn -- is counted as skipped,
    never fabricated into a procedure.
  - Every candidate is born `provenance="prior_library"`,
    `verification_state="candidate"` (via `capture_local_procedure`'s own
    always-candidate posture) -- nothing here ever claims "verified" or
    invents a pass/fail outcome. Git history proves a diff was committed,
    never that a test passed; evidence_status on every step stays
    "recommended", the same ceiling `historical_bootstrap.py` documents
    for repository-shaped sources.

DEDUP: a light heuristic, deliberately not a second embedding pipeline --
an existing local row is treated as the same workflow (evidence merged,
not a duplicate row) when its own `git_history` scope block shares at
least one changed file path with the new candidate AND their goal text
shares a word. A false non-merge (two rows) is preferred over an unsafe
false merge, same posture `historical_bootstrap.py`'s directive-quoted
threshold takes for its own sources.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from app.local_agent.local_store import LocalProcedureStore, _cosine_similarity

PROVENANCE = "prior_library"
SOURCE_TYPE = "git_history"

# Never anything but "recommended" is written here -- see module docstring.
EVIDENCE_RECOMMENDED = "recommended"

_FIX_SUBJECT_RE = re.compile(
    r"\b(fix|fixes|fixed|bug|bugfix|hotfix|patch|resolve|resolves|resolved|correct|corrects)\b",
    re.IGNORECASE,
)
_TEST_PATH_RE = re.compile(
    r"(^|/)tests?(/|_)|_test\.[a-zA-Z0-9]+$|test_[^/]+\.[a-zA-Z0-9]+$"
    r"|\.test\.[jt]sx?$|\.spec\.[jt]sx?$",
)
_MIGRATION_PATH_RE = re.compile(
    r"(^|/)migrations?/|(^|/)\d+_[^/]+\.sql$|(^|/)db/.*\.sql$",
)

# Multi-byte control markers extremely unlikely to occur in real commit
# subjects/bodies -- used to delimit `git log` records reliably instead of
# guessing at a plain-text separator.
_REC_START = "\x02STEALTHLAB-COMMIT\x02"
_HEADER_END = "\x03STEALTHLAB-HEADER-END\x03"


@dataclass
class GitCommit:
    sha: str
    parents: list[str]
    author: str
    timestamp: str
    subject: str
    body: str
    changed_files: list[str] = field(default_factory=list)

    @property
    def touches_tests(self) -> bool:
        return any(_TEST_PATH_RE.search(f) for f in self.changed_files)

    @property
    def touches_migration(self) -> bool:
        return any(_MIGRATION_PATH_RE.search(f) for f in self.changed_files)

    @property
    def looks_like_fix(self) -> bool:
        return bool(_FIX_SUBJECT_RE.search(self.subject))

    @property
    def non_test_files(self) -> set[str]:
        return {f for f in self.changed_files if not _TEST_PATH_RE.search(f)}

    @property
    def test_files(self) -> set[str]:
        return {f for f in self.changed_files if _TEST_PATH_RE.search(f)}


@dataclass
class GitCandidate:
    """One conservatively-formed workflow candidate spanning >=2 real
    commits along a single ancestry line."""

    pattern: str  # "fix_then_test" | "migration_code_test_triad"
    goal: str
    commits: list[GitCommit]
    shared_files: list[str]

    def steps(self) -> list[dict]:
        return [
            {
                "order": i,
                "goal": c.subject.strip() or c.sha[:12],
                "properties": {
                    "commit": c.sha,
                    "author": c.author,
                    "timestamp": c.timestamp,
                    "changed_files": sorted(c.changed_files),
                    "touches_tests": c.touches_tests,
                    "touches_migration": c.touches_migration,
                    "evidence_status": EVIDENCE_RECOMMENDED,
                },
            }
            for i, c in enumerate(self.commits)
        ]

    def evidence_refs(self, repo_label: str) -> list[dict]:
        return [
            {
                "source_type": SOURCE_TYPE,
                "source_id": c.sha,
                "source_location": f"{repo_label}@{c.sha}",
                "timestamp": c.timestamp,
                "evidence_status": EVIDENCE_RECOMMENDED,
                "privacy": "local",
            }
            for c in self.commits
        ]


def is_git_repo(repo_root: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def walk_git_history(repo_root: str, *, max_commits: int = 500) -> list[GitCommit]:
    """Real `git log`, oldest-first, first-parent-inclusive (all parents
    kept so a genuine merge is visible, but pattern-formation below only
    ever follows a direct single-parent edge). Returns [] for anything
    that isn't actually a git repo -- never raises, never fabricates."""
    if not is_git_repo(repo_root):
        return []

    fmt = (
        f"{_REC_START}%n%H%n%P%n%an <%ae>%n%aI%n%s%n%b{_HEADER_END}"
    )
    try:
        result = subprocess.run(
            [
                "git", "-C", repo_root, "log", "--reverse", "--name-only",
                f"--max-count={max_commits}", f"--pretty=format:{fmt}",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []

    commits: list[GitCommit] = []
    records = result.stdout.split(_REC_START)
    for record in records:
        record = record.strip("\n")
        if not record.strip() or _HEADER_END not in record:
            continue
        header, _, files_blob = record.partition(_HEADER_END)
        lines = header.split("\n")
        if len(lines) < 5:
            continue
        sha = lines[0].strip()
        parents = [p for p in lines[1].strip().split(" ") if p]
        author = lines[2].strip()
        timestamp = lines[3].strip()
        subject = lines[4].strip()
        body = "\n".join(lines[5:]).strip()
        files = [f.strip() for f in files_blob.split("\n") if f.strip()]
        if not sha:
            continue
        commits.append(GitCommit(
            sha=sha, parents=parents, author=author, timestamp=timestamp,
            subject=subject, body=body, changed_files=files,
        ))
    return commits


def _shared_dir_or_file(a: set[str], b: set[str]) -> list[str]:
    """Real shared paths first; falling back to a shared top-level
    directory only when no exact path overlaps -- this is the file-level
    "same area of the codebase" signal that turns a merely-adjacent pair
    of commits into a genuine pattern instead of coincidental timing."""
    exact = sorted(a & b)
    if exact:
        return exact
    dirs_a = {f.split("/", 1)[0] for f in a if "/" in f}
    dirs_b = {f.split("/", 1)[0] for f in b if "/" in f}
    return sorted(dirs_a & dirs_b)


def form_candidates(commits: list[GitCommit]) -> list[GitCandidate]:
    """The conservative pattern-formation core (task point 2). Only two
    patterns ever produce a candidate; every other commit -- including
    every bare fix with no test evidence -- is left out entirely."""
    by_sha = {c.sha: c for c in commits}
    candidates: list[GitCandidate] = []

    for i in range(len(commits) - 1):
        a = commits[i]
        b = commits[i + 1]
        # Real parent/child edge required -- not just adjacency in the
        # walked list (a branch/merge would otherwise falsely pair two
        # unrelated lines of history).
        if a.sha not in b.parents:
            continue

        # Pattern A: fix commit (no test touch of its own) whose direct
        # child touches tests, over overlapping files/dirs.
        if a.looks_like_fix and not a.touches_tests and b.touches_tests:
            shared = _shared_dir_or_file(a.non_test_files, b.non_test_files | b.test_files)
            if shared:
                candidates.append(GitCandidate(
                    pattern="fix_then_test",
                    goal=f"fix + verify: {a.subject.strip()}"[:200],
                    commits=[a, b],
                    shared_files=shared,
                ))
                continue

        # Pattern B: migration + code + test triad across up to 3 commits
        # on a single ancestry line, sharing a real path/dir.
        if i + 2 < len(commits):
            c = commits[i + 2]
            if b.sha in c.parents:
                trio = [a, b, c]
                has_migration = any(t.touches_migration for t in trio)
                has_test = any(t.touches_tests for t in trio)
                # "code" = a changed file that is neither a test file nor
                # a migration file, on at least one commit in the trio.
                has_code = any(
                    any(not _TEST_PATH_RE.search(f) and not _MIGRATION_PATH_RE.search(f)
                        for f in t.changed_files)
                    for t in trio
                )
                if has_migration and has_test and has_code:
                    all_files = set().union(*(t.changed_files for t in trio))
                    shared = _shared_dir_or_file(
                        trio[0].non_test_files, trio[-1].non_test_files | trio[-1].test_files,
                    ) or sorted({f.split("/", 1)[0] for f in all_files if "/" in f})
                    if shared:
                        candidates.append(GitCandidate(
                            pattern="migration_code_test_triad",
                            goal=f"migration + code + test: {a.subject.strip()}"[:200],
                            commits=trio,
                            shared_files=shared,
                        ))
    return candidates


def _find_merge_target(
    store: LocalProcedureStore,
    candidate: GitCandidate,
    query_embedding: Optional[list[float]],
) -> Optional[dict]:
    """Light cross-source dedup heuristic (task point 4): prefer a false
    NON-merge over an unsafe false merge. A merge happens only when an
    existing row's own recorded git_history changed-files overlap with the
    candidate's AND the goal text shares at least one word -- deliberately
    cheap, not a second embedding pipeline."""
    matches = store.search_local_procedures(candidate.goal, query_embedding=query_embedding, limit=5)
    goal_words = set(re.findall(r"[a-z0-9]+", candidate.goal.lower()))
    cand_files = set(candidate.shared_files)
    for row in matches:
        if query_embedding is not None and row.get("embedding"):
            if _cosine_similarity(query_embedding, row["embedding"]) >= 0.90:
                return row
        scope = row.get("scope") or {}
        existing_files = set((scope.get("git_history") or {}).get("files") or [])
        if not existing_files or not cand_files:
            continue
        row_words = set(re.findall(r"[a-z0-9]+", (row.get("name") or "").lower()))
        if existing_files & cand_files and goal_words & row_words:
            return row
    return None


def converge_candidate(
    store: LocalProcedureStore,
    candidate: GitCandidate,
    *,
    repo_label: str,
    workspace_entity_id: Optional[str] = None,
    embedding: Optional[list[float]] = None,
) -> dict:
    target = _find_merge_target(store, candidate, embedding)
    evidence_refs = candidate.evidence_refs(repo_label)
    if target is not None:
        # Merge every commit's evidence ref in turn -- append-only, same
        # invalidate-and-append posture the rest of the substrate uses.
        for ref, commit in zip(evidence_refs, candidate.commits):
            store.merge_bootstrap_evidence(
                target["id"], evidence_ref=ref, source_episode_id=commit.sha,
            )
        return {"status": "merged", "id": target["id"], "procedure_id": target["procedure_id"]}

    result = store.capture_local_procedure(
        name=candidate.goal,
        goal=candidate.goal,
        steps=candidate.steps(),
        scope={
            "bootstrap_sources": evidence_refs,
            "git_history": {
                "repo": repo_label,
                "pattern": candidate.pattern,
                "files": sorted(candidate.shared_files),
            },
        },
        evidence_refs=evidence_refs,
        source_episode_ids=[c.sha for c in candidate.commits],
        provenance=PROVENANCE,
        scope_type="repository",
        scope_entity_id=workspace_entity_id or repo_label,
        embedding=embedding,
    )
    return {"status": "captured", **result}


def bootstrap_git_history(
    store: LocalProcedureStore,
    repo_root: str,
    *,
    max_commits: int = 500,
    embed=None,
    workspace_entity_id: Optional[str] = None,
) -> dict:
    """The whole git-history bootstrap pass: walk real history, form
    conservative candidates, converge each into `store`. Returns an honest
    summary -- `commits_scanned` counts every real commit walked,
    `candidates_formed` counts only the ones that cleared the conservative
    pattern bar, everything else is `skipped_bare_commits`."""
    import asyncio

    commits = walk_git_history(repo_root, max_commits=max_commits)
    if not commits:
        return {
            "is_git_repo": is_git_repo(repo_root), "commits_scanned": 0,
            "candidates_formed": 0, "captured": 0, "merged": 0, "ids": [],
        }

    candidates = form_candidates(commits)
    repo_label = os.path.basename(os.path.abspath(repo_root)) or repo_root

    summary = {
        "is_git_repo": True,
        "commits_scanned": len(commits),
        "candidates_formed": len(candidates),
        "skipped_bare_commits": len(commits) - len({c.sha for cand in candidates for c in cand.commits}),
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
        result = converge_candidate(
            store, candidate, repo_label=repo_label,
            workspace_entity_id=workspace_entity_id, embedding=embedding,
        )
        summary[result["status"]] += 1
        summary["ids"].append(result["id"])
    if loop is not None:
        loop.close()
    return summary

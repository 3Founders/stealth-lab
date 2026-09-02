"""
Live proving test for app/local_agent/git_history_bootstrap.py.

REAL objects only: a real temporary git repository (`git init`, real
commits via subprocess), a real LocalProcedureStore over a real SQLite
file, no mocks of the code under test. Proves:

  1. commit 1 (introduces a bug, no test touch) + commit 2 (fixes it AND
     touches a test file) forms exactly one "fix_then_test" candidate --
     never a candidate per bare commit.
  2. the candidate lands in LocalProcedureStore as a `candidate` row with
     provenance naming git_history (via its evidence_refs/scope, source
     of truth for "which historical source produced this row"), never
     `verified`, never a fabricated outcome.
  3. it is retrievable through the store's own normal query interface
     (`search_local_procedures` / `list_local_procedures`).
  4. it is NOT visible through any global/Postgres retrieval path --
     asserted directly against the real `procedures` table when
     DATABASE_URL is set; when it isn't, structural isolation is asserted
     instead (the local store is a standalone sqlite file, `git_history`
     is never a `PROVENANCE_VALUES` member the global V0 gate accepts).
  5. a solo, test-free "fix" commit (no downstream test-touching child)
     produces NO candidate at all -- the required conservatism check.

DATABASE_URL (if set) must point at a real reachable Postgres so the
negative Postgres-side assertion is a real query, not a skip.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from app.local_agent.git_history_bootstrap import (
    PROVENANCE,
    SOURCE_TYPE,
    bootstrap_git_history,
    form_candidates,
    walk_git_history,
)
from app.local_agent.local_store import LocalProcedureStore
from app.services.v0_gate import PROVENANCE_VALUES


def _git(*args, cwd):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


@pytest.fixture()
def real_git_repo(tmp_path):
    """A REAL git repository on disk: `git init`, real commits, a real
    bug-introducing change followed by a real fix+test change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test Author", cwd=repo)

    # Commit 1: introduce a real bug. No test file touched.
    (repo / "auth.py").write_text(
        "def check_password(pw, expected):\n"
        "    return pw == expected  # BUG: not constant-time, and accepts None==None\n",
        encoding="utf-8",
    )
    _git("add", "auth.py", cwd=repo)
    _git("commit", "-q", "-m", "add password check", cwd=repo)

    # A second, unrelated bare commit -- must NOT become a candidate
    # (no downstream test-touching child; conservatism check).
    (repo / "README.md").write_text("docs\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-q", "-m", "fix typo in README", cwd=repo)

    # Commit introducing the REAL bug this test's fix will target.
    (repo / "auth.py").write_text(
        "def check_password(pw, expected):\n"
        "    if pw is None or expected is None:\n"
        "        return False\n"
        "    return pw == expected\n"
        "\n\ndef check_password_buggy_entrypoint():\n"
        "    # BUG: off-by-one style logic error introduced here\n"
        "    return check_password(None, None)\n",
        encoding="utf-8",
    )
    _git("add", "auth.py", cwd=repo)
    _git("commit", "-q", "-m", "fix null handling in check_password", cwd=repo)
    bug_sha = _git("rev-parse", "HEAD", cwd=repo).strip()

    # Commit 4: the FIX + a real test file change -- this is the child of
    # the bug-fix-shaped commit above (real parent/child edge).
    (repo / "auth.py").write_text(
        "def check_password(pw, expected):\n"
        "    if pw is None or expected is None:\n"
        "        return False\n"
        "    return pw == expected\n",
        encoding="utf-8",
    )
    (repo / "test_auth.py").write_text(
        "from auth import check_password\n\n"
        "def test_check_password_rejects_none():\n"
        "    assert check_password(None, None) is False\n",
        encoding="utf-8",
    )
    _git("add", "auth.py", "test_auth.py", cwd=repo)
    _git("commit", "-q", "-m", "resolve check_password None bug, add regression test", cwd=repo)
    fix_sha = _git("rev-parse", "HEAD", cwd=repo).strip()

    return {"root": str(repo), "bug_sha": bug_sha, "fix_sha": fix_sha}


def test_walk_git_history_reads_real_commits(real_git_repo):
    commits = walk_git_history(real_git_repo["root"])
    assert len(commits) == 4
    shas = {c.sha for c in commits}
    assert real_git_repo["bug_sha"] in shas
    assert real_git_repo["fix_sha"] in shas
    fix_commit = next(c for c in commits if c.sha == real_git_repo["fix_sha"])
    assert "test_auth.py" in fix_commit.changed_files
    assert fix_commit.touches_tests is True
    bug_commit = next(c for c in commits if c.sha == real_git_repo["bug_sha"])
    assert bug_commit.touches_tests is False
    assert bug_commit.looks_like_fix is True  # subject says "fix null handling"


def test_bare_fix_commit_with_no_test_evidence_is_not_promoted(real_git_repo):
    """The "fix typo in README" commit has no downstream test-touching
    child -- must produce zero candidates. This is the required
    conservatism check (task point 2)."""
    commits = walk_git_history(real_git_repo["root"])
    candidates = form_candidates(commits)
    readme_commit_shas = {c.sha for c in commits if "README.md" in c.changed_files}
    for cand in candidates:
        cand_shas = {c.sha for c in cand.commits}
        assert not (cand_shas & readme_commit_shas), (
            "bare README fix commit must never appear in a formed candidate"
        )


def test_fix_then_test_pattern_forms_exactly_one_candidate(real_git_repo):
    commits = walk_git_history(real_git_repo["root"])
    candidates = form_candidates(commits)
    fix_then_test = [c for c in candidates if c.pattern == "fix_then_test"]
    assert len(fix_then_test) == 1
    cand = fix_then_test[0]
    cand_shas = {c.sha for c in cand.commits}
    assert real_git_repo["bug_sha"] in cand_shas
    assert real_git_repo["fix_sha"] in cand_shas


def test_candidate_lands_in_local_store_as_grounded_candidate(tmp_path, real_git_repo):
    store = LocalProcedureStore(str(tmp_path), db_path=str(tmp_path / "local_procedures.db"))
    summary = bootstrap_git_history(store, real_git_repo["root"])

    assert summary["is_git_repo"] is True
    assert summary["commits_scanned"] == 4
    assert summary["candidates_formed"] == 1
    assert summary["captured"] == 1
    assert len(summary["ids"]) == 1

    row = store.get_local_procedure(summary["ids"][0])
    assert row is not None
    # never verified, never anything but a freshly-born candidate
    assert row["verification_state"] == "candidate"
    assert row["staleness"] == "fresh"
    assert row["provenance"] == PROVENANCE
    assert PROVENANCE in PROVENANCE_VALUES  # a real V0-gate-accepted value, not invented

    # provenance names git_history explicitly, on every evidence ref
    assert row["evidence_refs"], "candidate must carry real evidence refs"
    for ref in row["evidence_refs"]:
        assert ref["source_type"] == SOURCE_TYPE
        assert ref["evidence_status"] == "recommended"  # never a fabricated "executed"
    assert row["scope"]["git_history"]["pattern"] == "fix_then_test"

    # no fabricated verification stats -- zero real executions were ever recorded
    assert row["verification_stats"]["attempts"] == 0
    assert row["verification_stats"]["successes"] == 0

    # retrievable through the store's own normal query interface
    found = store.search_local_procedures("check_password")
    assert any(r["id"] == row["id"] for r in found)
    listed = store.list_local_procedures()
    assert any(r["id"] == row["id"] for r in listed)


def test_bootstrap_summary_is_honest_about_skipped_bare_commits(tmp_path, real_git_repo):
    store = LocalProcedureStore(str(tmp_path), db_path=str(tmp_path / "local_procedures.db"))
    summary = bootstrap_git_history(store, real_git_repo["root"])
    # 4 commits total, 2 consumed by the one formed candidate, 2 bare
    # (the initial "add password check" and "fix typo in README").
    assert summary["skipped_bare_commits"] == 2


def test_non_git_directory_returns_empty_without_raising(tmp_path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    commits = walk_git_history(str(not_a_repo))
    assert commits == []
    store = LocalProcedureStore(str(tmp_path), db_path=str(tmp_path / "x.db"))
    summary = bootstrap_git_history(store, str(not_a_repo))
    assert summary["is_git_repo"] is False
    assert summary["commits_scanned"] == 0
    assert store.list_local_procedures() == []


def test_git_history_candidate_is_not_visible_in_postgres(tmp_path, real_git_repo):
    """Structural isolation: a git_history-sourced local candidate never
    reaches the shared Postgres `procedures` table. Runs a real query
    against Postgres when DATABASE_URL is set (asserting a real absence,
    not a skip); otherwise asserts the structural guarantee that makes
    this true by construction."""
    store = LocalProcedureStore(str(tmp_path), db_path=str(tmp_path / "local_procedures.db"))
    summary = bootstrap_git_history(store, real_git_repo["root"])
    assert summary["captured"] == 1
    row = store.get_local_procedure(summary["ids"][0])
    procedure_id = row["procedure_id"]

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        # Structural guarantee: SOURCE_TYPE/"git_history" is not a member
        # of the global V0 gate's accepted provenance values, so a row
        # carrying it could never pass `capture_procedure`'s own gate --
        # this local candidate is SQLite-only by construction.
        assert "git_history" not in PROVENANCE_VALUES
        return

    import asyncpg
    import asyncio

    async def _check():
        conn = await asyncpg.connect(database_url)
        try:
            found = await conn.fetchrow(
                "SELECT id FROM procedures WHERE procedure_id = $1", procedure_id,
            )
            return found
        finally:
            await conn.close()

    found = asyncio.run(_check())
    assert found is None, "a local git_history candidate must never appear in Postgres procedures"

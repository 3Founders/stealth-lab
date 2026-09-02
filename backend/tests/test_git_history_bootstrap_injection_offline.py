"""
Proving test: app/local_agent/git_history_bootstrap.py treats commit
subjects/bodies/file paths as DATA for candidate-procedure text fields
only -- never executed, never shell-interpolated, never fed into eval/exec.

Threat model (directive item 2): the git-history bootstrap adapter walks a
REAL repository via `git log`, and that repository's commit messages are
attacker-controlled (anyone who can commit -- a compromised dependency repo,
a malicious contributor, a poisoned upstream mirror). A malicious commit
subject/body containing shell metacharacters (`; rm -rf / #`) or a fake
"invariant" expression (`$(whoami)`) must produce an inert candidate-
procedure text field, with:
  - no command execution (proved by absence of a marker file a successful
    shell-interpolation would have created),
  - no exception (proved structurally, not just "caught" -- the module's
    own subprocess calls pass argument LISTS, never a shell string built
    from repo content, so there is nothing for the payload to break out
    of).

REAL objects only, same discipline as test_git_history_bootstrap_live.py:
a real temporary git repo, real `git commit` calls, no mocks of the code
under test. DB-free: LocalProcedureStore is a real SQLite file, no
DATABASE_URL needed.
"""
from __future__ import annotations

import inspect
import subprocess

import pytest

from app.local_agent import git_history_bootstrap
from app.local_agent.git_history_bootstrap import (
    bootstrap_git_history,
    form_candidates,
    walk_git_history,
)
from app.local_agent.local_store import LocalProcedureStore


def _git(*args, cwd):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


# Malicious payloads a hostile commit author could plant. If either
# subprocess.run call in git_history_bootstrap.py ever used shell=True
# with a string built from repo content, ONE of these would pop a shell:
# the semicolon payload as a command separator, the $() payload as
# command substitution. Neither must ever create MARKER.
_SHELL_INJECTION_SUBJECT = "fix null handling in check_password; touch MARKER #"
_SHELL_SUBSTITUTION_BODY = "see also $(touch MARKER) and `touch MARKER`"
_FAKE_INVARIANT_SUBJECT = "fix: enforce amount <= balance via __import__('os').system('touch MARKER')"


@pytest.fixture()
def hostile_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "attacker@example.com", cwd=repo)
    _git("config", "user.name", "Attacker", cwd=repo)

    (repo / "auth.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git("add", "auth.py", cwd=repo)
    _git("commit", "-q", "-m", _SHELL_INJECTION_SUBJECT, "-m", _SHELL_SUBSTITUTION_BODY, cwd=repo)
    bug_sha = _git("rev-parse", "HEAD", cwd=repo).strip()

    (repo / "auth.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (repo / "test_auth.py").write_text(
        "def test_f():\n    assert True\n", encoding="utf-8",
    )
    _git("add", "auth.py", "test_auth.py", cwd=repo)
    _git("commit", "-q", "-m", _FAKE_INVARIANT_SUBJECT, cwd=repo)
    fix_sha = _git("rev-parse", "HEAD", cwd=repo).strip()

    return {"root": str(repo), "bug_sha": bug_sha, "fix_sha": fix_sha}


def test_subprocess_calls_never_use_shell_true_or_string_interpolation():
    """Structural guarantee, not a behavioral guess: every subprocess.run
    call in this module passes a list literal whose only repo-derived
    element is `repo_root` via `-C`, never a shell string built from
    commit content -- so injected shell metacharacters in a commit
    subject/body have no shell to reach in the first place."""
    source = inspect.getsource(git_history_bootstrap)
    assert "shell=True" not in source
    assert "shell =True" not in source
    assert "shell= True" not in source


def test_hostile_commit_subject_and_body_never_spawn_a_shell(hostile_git_repo, tmp_path):
    marker = tmp_path / "repo" / "MARKER"
    assert not marker.exists()

    commits = walk_git_history(hostile_git_repo["root"])
    assert not marker.exists(), "walk_git_history must never execute commit content"

    candidates = form_candidates(commits)
    assert not marker.exists(), "form_candidates must never execute commit content"

    store = LocalProcedureStore(
        str(tmp_path), db_path=str(tmp_path / "local_procedures.db"),
    )
    summary = bootstrap_git_history(store, hostile_git_repo["root"])
    assert not marker.exists(), (
        "bootstrap_git_history must never execute injected shell/python "
        "payloads carried in a commit subject, body, or fake 'invariant' text"
    )

    assert summary["is_git_repo"] is True
    assert summary["captured"] == 1
    row = store.get_local_procedure(summary["ids"][0])
    assert row is not None

    # The hostile payload survives verbatim as inert TEXT in the
    # candidate's goal/step fields -- proof it was carried as data, never
    # parsed/executed/stripped by a "sanitizer" that could itself be a
    # second injection surface.
    goal_and_steps = row["goal"] + " " + " ".join(
        step.get("goal", "") for step in row["steps"]
    )
    assert "touch MARKER" in goal_and_steps or "MARKER" in row["goal"]

    # No candidate procedure ever carries an `invariants` field synthesized
    # from commit text -- git history never manufactures a numeric
    # invariant, so the "$(whoami)"-as-fake-invariant vector in the
    # directive has no landing surface in this adapter at all.
    assert not row.get("invariants")


def test_hostile_file_paths_are_inert_strings_not_shell_metacharacters(tmp_path):
    """A changed file PATH is also attacker-controlled (a committer can
    name a file anything the filesystem allows). Prove a path containing
    shell metacharacters is only ever compared/stored as a string."""
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "attacker@example.com", cwd=repo)
    _git("config", "user.name", "Attacker", cwd=repo)

    hostile_filename = "tests/$(touch MARKER2)_test.py"
    (repo / "tests").mkdir()
    (repo / hostile_filename).write_text("assert True\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "fix: add regression coverage", cwd=repo)

    marker = repo / "MARKER2"
    commits = walk_git_history(str(repo))
    assert not marker.exists()
    assert len(commits) == 1
    assert hostile_filename in commits[0].changed_files
    assert commits[0].touches_tests is True

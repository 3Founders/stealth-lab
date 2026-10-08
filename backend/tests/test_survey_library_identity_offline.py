"""
Cross-fork test (3-hard.md, fork B item 2): the repo identity the SURVEY scanner writes (fork C,
packaging/npm/lib/survey) reaches find_ways through the LIBRARY payload (fork B, `stealthlab-mcp library
payload`), and the server reads it the way plan §5.1 means:

  * a repository with a GitHub remote -> a strong `r:` identity; with the owner's consent its public name maps
    to the `owner/name` public benchmarks use, so this repo's own global Goals can be found;
  * no consent -> the hash only, never the name;
  * a shallow clone with no remote and no stored identity -> a weak `p:` identity that never matches other
    repositories' Goals (no query at all).

The scanner and the payload builder run for real (node); skipped when node is not installed.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import library_context as lc

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "packaging" / "npm" / "bin" / "stealthlab-mcp.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None or not BIN.exists(), reason="needs node and the npm client")

_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main",
                           "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env={**os.environ, **_GIT_ENV}).stdout


def _repo(tmp: Path, name: str, remote: str | None) -> Path:
    d = tmp / name
    (d / "src").mkdir(parents=True)
    (d / "pyproject.toml").write_text(f"[project]\nname = '{name}'\nversion = '0.1.0'\n", encoding="utf-8")
    (d / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "initial")
    if remote:
        _git(d, "remote", "add", "origin", remote)
    return d


def _node(*args: str, cwd: Path, env: dict | None = None) -> dict:
    out = subprocess.run(["node", str(BIN), *args], cwd=cwd, check=True, capture_output=True, text=True,
                         env={**os.environ, "STEALTHLAB_SHARE_REPO_NAME": "", **(env or {})})
    return json.loads(out.stdout)


def _survey_and_payload(repo: Path, env: dict | None = None) -> tuple[dict, dict]:
    subprocess.run(["node", str(BIN), "survey", str(repo), "--json", "--no-history"], cwd=repo, check=True,
                   capture_output=True, text=True)
    meta = json.loads((repo / ".stealth" / "meta.json").read_text(encoding="utf-8"))
    payload = _node("library", "payload", "--root", str(repo), cwd=repo, env=env)
    return meta["repo_identity"], payload


class _NoQueryPool:
    async def fetch(self, *a, **k):
        raise AssertionError("a weak identity must never query other repositories' Goals")


class _RecordingPool:
    def __init__(self):
        self.args = None

    async def fetch(self, sql, *args):
        self.args = args
        return [{"goal_id": "00000000-0000-0000-0000-000000000001"}]


def test_github_remote_strong_identity_maps_to_owner_name_only_with_consent(tmp_path):
    repo = _repo(tmp_path, "widget", "git@github.com:Acme/Widget.git")
    ident, payload = _survey_and_payload(repo)
    assert ident["strength"] == "strong" and ident["repo_id"].startswith("r:")
    # no consent: the hash travels, the name never does
    assert payload["repo_identity"] == {"repo_id": ident["repo_id"]}
    parsed, problem = lc.parse_repo_identity(payload["repo_identity"])
    assert problem is None and not parsed.weak and parsed.public_name is None
    assert asyncio.run(lc.same_repo_goal_ids(_NoQueryPool(), parsed)) == []

    # consent: the scanner's normalised remote is accepted and maps to the benchmark spelling owner/name
    shared = _node("library", "payload", "--root", str(repo), cwd=repo, env={"STEALTHLAB_SHARE_REPO_NAME": "1"})
    parsed, problem = lc.parse_repo_identity(shared["repo_identity"])
    assert problem is None, problem
    assert parsed.public_name == "github.com/acme/widget"
    assert parsed.benchmark_repo == "acme/widget"
    pool = _RecordingPool()
    assert asyncio.run(lc.same_repo_goal_ids(pool, parsed)) == ["00000000-0000-0000-0000-000000000001"]
    assert pool.args[0] == "acme/widget"


def test_non_github_remote_never_guesses_a_benchmark_repo(tmp_path):
    repo = _repo(tmp_path, "proj", "https://gitlab.com/grp/sub/proj.git")
    _, _ = _survey_and_payload(repo)
    shared = _node("library", "payload", "--root", str(repo), cwd=repo, env={"STEALTHLAB_SHARE_REPO_NAME": "1"})
    parsed, problem = lc.parse_repo_identity(shared["repo_identity"])
    assert problem is None and not parsed.weak
    assert parsed.benchmark_repo is None
    assert asyncio.run(lc.same_repo_goal_ids(_NoQueryPool(), parsed)) == []


def test_shallow_clone_without_identity_is_weak_and_never_matches(tmp_path):
    origin = _repo(tmp_path, "origin_repo", None)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(shallow)], check=True,
                   capture_output=True, env={**os.environ, **_GIT_ENV})
    _git(shallow, "remote", "remove", "origin")
    ident, payload = _survey_and_payload(shallow)
    assert ident["strength"] == "weak" and ident["repo_id"].startswith("p:")
    assert payload["repo_identity"]["strength"] == "weak"
    parsed, problem = lc.parse_repo_identity(payload["repo_identity"])
    assert problem is None and parsed.weak
    assert asyncio.run(lc.same_repo_goal_ids(_NoQueryPool(), parsed)) == []
    ctx = lc.build(payload["repo_identity"], payload.get("library_rows", ""))
    assert not ctx.pinned


def test_two_unrelated_weak_repos_with_the_same_manifest_name_are_not_one_identity(tmp_path):
    a = _repo(tmp_path / "a", "same", None)
    b = _repo(tmp_path / "b", "same", None)
    ids = []
    for origin in (a, b):
        shallow = origin.parent / "clone"
        subprocess.run(["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(shallow)], check=True,
                       capture_output=True, env={**os.environ, **_GIT_ENV})
        _git(shallow, "remote", "remove", "origin")
        ident, _ = _survey_and_payload(shallow)
        ids.append(ident)
    # same manifest name and path -> the same weak id is possible, which is exactly why weak never matches
    assert all(i["strength"] == "weak" for i in ids)
    for i in ids:
        parsed, _ = lc.parse_repo_identity({"repo_id": i["repo_id"], "strength": "weak"})
        assert asyncio.run(lc.same_repo_goal_ids(_NoQueryPool(), parsed)) == []

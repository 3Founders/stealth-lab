"""
Offline tests for app/services/ingestion_sources/ -- the left half of the
Phase 2 ingestion compiler (enumerate a source, pull its bytes, stamp a
stable content hash). No network, no DB.
"""
from __future__ import annotations

import base64
import json

import httpx

import pytest

from app.services.ingestion_sources import (
    SOURCE_ADAPTERS,
    GitHubSkillSource,
    LocalDirSkillSource,
    SourceArtifact,
    compute_content_hash,
)
from app.services.ingestion_sources.skill_md import _parse_repo_url

SKILL_A = """---
name: explore-repo
description: Build a mental model of an unfamiliar repository.
---

1. List the top-level directories.
2. Read the package manifest.
"""

SKILL_B = """---
name: debug-flaky-test
description: Diagnose a test that passes sometimes and fails others.
---

1. Run the test in isolation repeatedly.
2. Look for shared mutable state.
"""


# --------------------------------------------------------------------------
# LocalDirSkillSource
# --------------------------------------------------------------------------
def _make_tree(tmp_path):
    (tmp_path / "skills" / "explore").mkdir(parents=True)
    (tmp_path / "skills" / "explore" / "SKILL.md").write_text(SKILL_A, encoding="utf-8")
    (tmp_path / "skills" / "debug").mkdir(parents=True)
    (tmp_path / "skills" / "debug" / "flaky.skill.md").write_text(SKILL_B, encoding="utf-8")
    (tmp_path / "README.md").write_text("not a skill", encoding="utf-8")
    return tmp_path


def test_localdir_discover_finds_only_skill_files(tmp_path):
    root = _make_tree(tmp_path)
    src = LocalDirSkillSource(root)
    refs = list(src.discover())
    paths = sorted(r.path for r in refs)
    assert paths == ["skills/debug/flaky.skill.md", "skills/explore/SKILL.md"]
    assert all(r.repository == root.name for r in refs)
    assert all(r.uri.startswith("file://") for r in refs)


def test_localdir_fetch_returns_content_and_hex_hash(tmp_path):
    root = _make_tree(tmp_path)
    src = LocalDirSkillSource(root)
    ref = next(r for r in src.discover() if r.path.endswith("explore/SKILL.md"))
    art = src.fetch(ref)
    assert isinstance(art, SourceArtifact)
    assert art.source_type == "skill_md"
    assert art.content == SKILL_A
    assert len(art.content_hash) == 64
    int(art.content_hash, 16)  # valid hex or raises


def test_localdir_hash_is_stable_and_content_sensitive(tmp_path):
    root = _make_tree(tmp_path)
    src = LocalDirSkillSource(root)
    ref = next(r for r in src.discover() if r.path.endswith("explore/SKILL.md"))
    h1 = src.fetch(ref).content_hash
    h2 = src.fetch(ref).content_hash
    assert h1 == h2

    (root / "skills" / "explore" / "SKILL.md").write_text(SKILL_A + "\n3. Extra step.\n", encoding="utf-8")
    h3 = src.fetch(ref).content_hash
    assert h3 != h1


def test_localdir_fingerprint_is_the_content_hash(tmp_path):
    root = _make_tree(tmp_path)
    src = LocalDirSkillSource(root)
    ref = next(src.discover())
    art = src.fetch(ref)
    assert src.fingerprint(art) == art.content_hash


# --------------------------------------------------------------------------
# GitHubSkillSource
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/skills",
        "https://github.com/acme/skills.git",
        "git@github.com:acme/skills.git",
    ],
)
def test_parse_repo_url_forms(url):
    assert _parse_repo_url(url) == ("acme", "skills")


def test_parse_repo_url_rejects_non_github():
    with pytest.raises(ValueError):
        _parse_repo_url("https://gitlab.com/acme/skills")


class FakeHttp:
    """Canned GitHub trees API + raw file bodies."""

    def __init__(self):
        self.calls: list[str] = []
        self.tree = {
            "sha": "abc123def",
            "tree": [
                {"type": "blob", "path": "skills/explore/SKILL.md"},
                {"type": "blob", "path": "skills/debug/flaky.skill.md"},
                {"type": "blob", "path": "README.md"},
                {"type": "tree", "path": "skills"},
            ],
        }
        self.raw = {
            "skills/explore/SKILL.md": SKILL_A,
            "skills/debug/flaky.skill.md": SKILL_B,
        }

    def __call__(self, url: str) -> tuple[int, str]:
        self.calls.append(url)
        if "api.github.com" in url:
            return 200, json.dumps(self.tree)
        for path, body in self.raw.items():
            if url.endswith(path):
                return 200, body
        return 404, ""


def test_github_discover_filters_and_carries_tree_sha():
    http = FakeHttp()
    src = GitHubSkillSource("https://github.com/acme/skills", http_get=http)
    refs = list(src.discover())
    assert sorted(r.path for r in refs) == [
        "skills/debug/flaky.skill.md",
        "skills/explore/SKILL.md",
    ]
    assert all(r.commit == "abc123def" for r in refs)
    assert all(r.repository == "acme/skills" for r in refs)
    assert http.calls[0] == (
        "https://api.github.com/repos/acme/skills/git/trees/HEAD?recursive=1"
    )


def test_github_fetch_hits_raw_url_and_sets_repository():
    http = FakeHttp()
    src = GitHubSkillSource("https://github.com/acme/skills", http_get=http)
    ref = next(r for r in src.discover() if r.path.endswith("explore/SKILL.md"))
    art = src.fetch(ref)
    assert (
        "https://raw.githubusercontent.com/acme/skills/abc123def/skills/explore/SKILL.md"
        in http.calls
    )
    assert art.repository == "acme/skills"
    assert art.commit == "abc123def"
    assert art.content == SKILL_A
    assert art.content_hash == compute_content_hash(SKILL_A)


def test_github_fetch_recovers_from_raw_tls_failure(monkeypatch):
    from app.services.ingestion_sources import skill_md

    calls = []
    checked = []
    sleeps = []
    api_url = "https://api.github.com/repos/acme/skills/contents/skills/explore/SKILL.md?ref=abc123def"

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs.get("verify", True) is True
        assert kwargs["follow_redirects"] is False
        if "git/trees" in url:
            return httpx.Response(200, json=FakeHttp().tree)
        if "raw.githubusercontent.com" in url:
            raise httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER]")
        assert url == api_url
        return httpx.Response(200, json={
            "type": "file", "encoding": "base64",
            "content": base64.encodebytes(SKILL_A.encode()).decode(),
        })

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(skill_md.time, "sleep", sleeps.append)
    monkeypatch.setattr("app.services.screening.assert_safe_locator", checked.append)
    src = GitHubSkillSource("https://github.com/acme/skills")
    ref = next(src.discover())
    artifact = src.fetch(ref)
    assert artifact.content == SKILL_A
    assert artifact.content_hash == compute_content_hash(SKILL_A)
    assert artifact.commit == ref.commit == "abc123def"
    assert artifact.uri == ref.uri
    assert len([url for url in calls if "raw.githubusercontent.com" in url]) == 3
    assert sleeps == [2.0, 4.0]
    assert api_url in checked


@pytest.mark.parametrize("failure", [ValueError("unsafe locator"), None])
def test_github_fetch_does_not_mask_non_transport_failure(failure):
    http = FakeHttp()

    def get(url):
        if "api.github.com" in url:
            return http(url)
        if failure is not None:
            raise failure
        return 404, "not found"

    src = GitHubSkillSource("https://github.com/acme/skills", http_get=get)
    ref = next(src.discover())
    with pytest.raises((ValueError, RuntimeError)):
        src.fetch(ref)
    assert len(http.calls) == 1


@pytest.mark.parametrize("payload", [
    {"type": "dir", "encoding": "base64", "content": "YQ=="},
    {"type": "file", "encoding": "none", "content": ""},
    {"type": "file", "encoding": "base64", "content": "***"},
    {"type": "file", "encoding": "base64", "content": "/w=="},
    [],
])
def test_github_api_recovery_rejects_invalid_content(payload):
    def get(url):
        if "git/trees" in url:
            return 200, json.dumps(FakeHttp().tree)
        if "raw.githubusercontent.com" in url:
            raise httpx.ConnectError("raw unavailable")
        return 200, json.dumps(payload)

    src = GitHubSkillSource("https://github.com/acme/skills", http_get=get)
    with pytest.raises((ValueError, RuntimeError)):
        src.fetch(next(src.discover()))


@pytest.mark.parametrize("status", [403, 404, 429, 503])
def test_github_api_recovery_surfaces_http_failure(status):
    def get(url):
        if "git/trees" in url:
            return 200, json.dumps(FakeHttp().tree)
        if "raw.githubusercontent.com" in url:
            raise httpx.ConnectError("raw unavailable")
        return status, "not a skill"

    src = GitHubSkillSource("https://github.com/acme/skills", http_get=get)
    with pytest.raises(RuntimeError, match=str(status)):
        src.fetch(next(src.discover()))


def test_github_ctor_does_no_network():
    # No http_get calls just from constructing.
    http = FakeHttp()
    GitHubSkillSource("https://github.com/acme/skills", http_get=http)
    assert http.calls == []


# --------------------------------------------------------------------------
# dispatch table
# --------------------------------------------------------------------------
def test_source_adapters_dispatch():
    assert SOURCE_ADAPTERS["skill_md_dir"] is LocalDirSkillSource
    assert SOURCE_ADAPTERS["skill_md_repo"] is GitHubSkillSource

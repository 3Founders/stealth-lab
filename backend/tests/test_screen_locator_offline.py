"""
G3 tail / T13 -- the SSRF fetch-locator guard
(`app.services.screening.screen_locator` / `assert_safe_locator`) and the
`LocalDirSkillSource` symlink-escape guard. Pure / filesystem only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.screening import (
    UnsafeLocatorError,
    assert_safe_locator,
    screen_locator,
)


# ---- locators that MUST be blocked ----------------------------------------
@pytest.mark.parametrize("locator, needle", [
    ("file:///etc/passwd", "scheme"),
    ("ftp://example.com/x", "scheme"),
    ("gopher://example.com/x", "scheme"),
    ("dict://127.0.0.1:11211/", "scheme"),
    ("http://169.254.169.254/latest/meta-data/", "not a public address"),
    ("http://169.254.170.2/v2/credentials", "not a public address"),
    ("http://127.0.0.1:8080/admin", "not a public address"),
    ("http://localhost/x", "non-public"),
    ("http://10.0.0.5/x", "not a public address"),
    ("http://192.168.1.1/x", "not a public address"),
    ("http://172.16.9.9/x", "not a public address"),
    ("http://[::1]/x", "not a public address"),
    ("http://0.0.0.0/x", "not a public address"),
    ("http://metadata.google.internal/computeMetadata/v1/", "metadata"),
    ("https://metadata/x", "metadata"),
    ("not-even-a-url", "http(s)"),
    ("https://", "host"),
])
def test_unsafe_locators_are_rejected(locator, needle):
    result = screen_locator(locator, resolve=True)
    assert result["allowed"] is False
    assert result["classification"] == "REJECT"
    assert needle in result["reason"]
    with pytest.raises(UnsafeLocatorError):
        assert_safe_locator(locator)


# ---- locators that MUST pass --------------------------------------------
@pytest.mark.parametrize("locator", [
    "https://github.com/owner/repo",
    "https://api.github.com/repos/owner/repo/git/trees/HEAD?recursive=1",
    "https://raw.githubusercontent.com/owner/repo/HEAD/SKILL.md",
    "https://codeload.github.com/owner/repo/tar.gz/HEAD",
])
def test_allowlisted_hosts_pass_without_dns(locator):
    result = screen_locator(locator, resolve=False)
    assert result["allowed"] is True
    assert result["classification"] == "ALLOW"
    assert assert_safe_locator(locator, resolve=False)["host"]


def test_public_hostname_passes_with_resolution():
    # example.com is IANA-reserved but resolves to public addresses.
    result = screen_locator("https://example.com/path", resolve=True)
    assert result["allowed"] is True
    assert result["resolved_ips"]


def test_dns_failure_is_a_deny_not_a_crash():
    result = screen_locator("https://no-such-host.invalid/x", resolve=True)
    assert result["allowed"] is False
    assert "DNS" in result["reason"] or "resolve" in result["reason"].lower()


# ---- T13: symlink escape in LocalDirSkillSource -------------------------
@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privilege on Windows")
def test_local_dir_skill_source_skips_symlink_escape(tmp_path):
    from app.services.ingestion_sources.skill_md import LocalDirSkillSource

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SECRET.md").write_text("top secret", encoding="utf-8")

    root = tmp_path / "skills"
    root.mkdir()
    (root / "SKILL.md").write_text("# real skill\n\nsteps: do a thing", encoding="utf-8")
    # a symlink inside the root named like a skill file, pointing outside
    (root / "evil.skill.md").symlink_to(outside / "SECRET.md")

    src = LocalDirSkillSource(root)
    refs = list(src.discover())
    paths = {r.path for r in refs}
    assert "SKILL.md" in paths
    assert "evil.skill.md" not in paths, "symlink escaping the root must not be discovered"

    # even if a ref is forged for it, fetch refuses
    from app.services.ingestion_sources.base import SourceRef

    with pytest.raises(ValueError):
        src.fetch(SourceRef(uri=(outside / "SECRET.md").as_uri(), repository="skills",
                            path="../outside/SECRET.md", commit=None))

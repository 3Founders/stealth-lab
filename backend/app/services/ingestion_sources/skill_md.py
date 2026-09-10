"""
SKILL.md source adapters -- a local directory and a GitHub repository.
The only two adapters implemented for Phase 2 (prompts.md section 15's
`ingest skill-dir ./skills` / `ingest skill-repo <github-url>`).

Both produce the same `SourceArtifact(source_type="skill_md")`; the
compiler downstream cannot tell which one an artifact came from except by
its provenance fields. That is the point of the split (see this package's
__init__ docstring).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Iterator

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)

# A SKILL.md is either literally named `SKILL.md` or `<something>.skill.md`.
_SKILL_FILENAME_RE = re.compile(r"(^SKILL\.md$)|(\.skill\.md$)", re.IGNORECASE)

# httpx GET returning (status_code, text). Injected in tests; the default
# lazily imports httpx (a transitive dep, same lazy-import discipline
# app/debate/panel.py uses for it) so a missing install surfaces only when
# a real network fetch is actually attempted, never at import time.
HttpGet = Callable[[str], "tuple[int, str]"]


def _default_http_get(url: str) -> tuple[int, str]:
    import httpx

    from app.services.screening import assert_safe_locator

    # SSRF guard (G3 tail): screen the locator, then follow at most one
    # redirect hop -- each hop re-screened -- so a benign-looking URL
    # cannot 302 the server into the cloud-metadata endpoint or an
    # internal host.
    assert_safe_locator(url)
    current = url
    for _ in range(4):
        resp = httpx.get(current, timeout=30, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
            current = str(httpx.URL(resp.url).join(resp.headers["location"]))
            assert_safe_locator(current)
            continue
        return resp.status_code, resp.text
    return resp.status_code, resp.text


def _is_skill_file(name: str) -> bool:
    return bool(_SKILL_FILENAME_RE.search(name))


def _within_root(path: Path, root_resolved: Path) -> bool:
    """True iff `path`, with every symlink resolved, stays under
    `root_resolved`. Used to reject directory-traversal / symlink escapes
    (T13)."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root_resolved or root_resolved in resolved.parents


class LocalDirSkillSource:
    """Every `SKILL.md` / `*.skill.md` under a directory tree."""

    source_type = "skill_md"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def discover(self) -> Iterator[SourceRef]:
        root_resolved = self._root.resolve()
        for p in sorted(self._root.rglob("*")):
            if not p.is_file() or not _is_skill_file(p.name):
                continue
            # T13: a symlink (or a `..` path) that resolves outside the
            # ingestion root is a directory-traversal escape -- skip it,
            # never read it.
            if not _within_root(p, root_resolved):
                continue
            yield SourceRef(
                uri=p.resolve().as_uri(),
                repository=self._root.name or str(self._root),
                path=str(p.relative_to(self._root).as_posix()),
                commit=None,
            )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        path = self._root / ref.path if ref.path else Path(_uri_to_path(ref.uri))
        if not _within_root(path, self._root.resolve()):
            raise ValueError(f"refusing to read {path} -- resolves outside the ingestion root")
        content = path.read_text(encoding="utf-8")
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=ref.repository,
            path=ref.path,
            commit=None,
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash


def _uri_to_path(uri: str) -> str:
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    p = unquote(parsed.path)
    # Windows file URIs come back as `/C:/...`
    if re.match(r"^/[A-Za-z]:/", p):
        p = p[1:]
    return p


_REPO_URL_RE = re.compile(
    r"""
    (?:
        (?:https?://github\.com/) |
        (?:git@github\.com:)
    )
    (?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$
    """,
    re.VERBOSE,
)


def _parse_repo_url(repo_url: str) -> tuple[str, str]:
    m = _REPO_URL_RE.search(repo_url.strip())
    if not m:
        raise ValueError(f"not a recognisable GitHub repo URL: {repo_url!r}")
    return m.group("owner"), m.group("repo")


class GitHubSkillSource:
    """Every `SKILL.md` / `*.skill.md` in a GitHub repository tree.

    Network happens ONLY inside `discover()` and `fetch()` -- never in
    `__init__` -- so constructing one of these is free and test-safe.
    """

    source_type = "skill_md"

    def __init__(
        self,
        repo_url: str,
        ref: str = "HEAD",
        *,
        http_get: HttpGet | None = None,
    ) -> None:
        self._owner, self._repo = _parse_repo_url(repo_url)
        self._ref = ref
        self._http_get = http_get or _default_http_get
        self._resolved_commit: str | None = None

    @property
    def slug(self) -> str:
        return f"{self._owner}/{self._repo}"

    def discover(self) -> Iterator[SourceRef]:
        url = (
            f"https://api.github.com/repos/{self._owner}/{self._repo}"
            f"/git/trees/{self._ref}?recursive=1"
        )
        status, body = self._http_get(url)
        if status != 200:
            raise RuntimeError(f"GitHub trees API {url} returned {status}")
        tree = json.loads(body)
        self._resolved_commit = tree.get("sha") or self._ref
        for entry in tree.get("tree", []):
            if entry.get("type") != "blob":
                continue
            path = entry.get("path", "")
            if not _is_skill_file(path.rsplit("/", 1)[-1]):
                continue
            yield SourceRef(
                uri=(
                    f"https://github.com/{self._owner}/{self._repo}"
                    f"/blob/{self._resolved_commit}/{path}"
                ),
                repository=self.slug,
                path=path,
                commit=self._resolved_commit,
            )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        commit = ref.commit or self._resolved_commit or self._ref
        raw_url = (
            f"https://raw.githubusercontent.com/{self._owner}/{self._repo}"
            f"/{commit}/{ref.path}"
        )
        status, content = self._http_get(raw_url)
        if status != 200:
            raise RuntimeError(f"raw fetch {raw_url} returned {status}")
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=self.slug,
            path=ref.path,
            commit=commit,
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash


# Dispatch table (prompts.md section 13). Keys name a source TYPE + how it
# is reached, not a provider -- later adapters (`github_workflow`,
# `documentation`, `research_paper`, ...) are added here.
SOURCE_ADAPTERS: dict[str, type] = {
    "skill_md_dir": LocalDirSkillSource,
    "skill_md_repo": GitHubSkillSource,
}

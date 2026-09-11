"""Immutable-revision GitHub adapter for bounded SKILL.md packages.

This extends the existing SourceAdapter seam. It uses GitHub's commit and tree
interfaces as a logical snapshot, then fetches only SKILL.md and directly relevant
package resources. No repository checkout or archive extraction is required.
"""
from __future__ import annotations

import hashlib
import json
import os
import base64
import subprocess
import tempfile
from pathlib import Path
import posixpath
import re
from pathlib import PurePosixPath
from typing import Callable, Iterator
from urllib.parse import quote, urlsplit

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    SourceResource,
    compute_content_hash,
)
from app.services.ingestion_sources.manifest import CorpusSourceSpec
from app.services.ingestion_sources.skill_md import _parse_repo_url


HttpGetBytes = Callable[[str], tuple[int, bytes]]

MAX_TREE_ENTRIES = 100_000
MAX_RESOURCE_FILES = 500
MAX_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 20 * 1024 * 1024

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SKILL_NAME = "skill.md"
_BUNDLED_DIRS = {"scripts", "references", "assets", "examples", "templates"}
_CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
_EXCLUDED_PARTS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    "dist", "build", ".mypy_cache", ".ruff_cache",
}
_MARKDOWN_LINK_RE = re.compile(r"\]\(([^)]+)\)")
_BACKTICK_PATH_RE = re.compile(
    r"`((?:\.\.?/)?[^`\s]+\.(?:py|sh|bash|md|json|ya?ml|toml|ini|cfg))`",
    re.IGNORECASE,
)


def _default_http_get(url: str) -> tuple[int, bytes]:
    import httpx

    from app.services.screening import assert_safe_locator

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "stealthlab-skill-ingestion",
    }
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    # SSRF guard (G3 tail): screen every locator + redirect hop.
    assert_safe_locator(url)
    current = url
    for _ in range(4):
        response = httpx.get(current, timeout=60, follow_redirects=False, headers=headers)
        if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
            current = str(httpx.URL(response.url).join(response.headers["location"]))
            assert_safe_locator(current)
            continue
        return response.status_code, response.content
    return response.status_code, response.content


def _safe_repo_path(path: str) -> str | None:
    path = path.replace("\\", "/")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        return None
    if any(part.lower() in _EXCLUDED_PARTS for part in candidate.parts):
        return None
    return candidate.as_posix()


def _resource_kind(path: str, *, skill_dir: str) -> str:
    rel_parts = PurePosixPath(path).relative_to(PurePosixPath(skill_dir)).parts \
        if path == skill_dir or path.startswith(skill_dir + "/") else PurePosixPath(path).parts
    lowered = [part.lower() for part in rel_parts]
    for directory in _BUNDLED_DIRS:
        if directory in lowered:
            return "reference" if directory == "references" else directory.rstrip("s")
    if PurePosixPath(path).suffix.lower() in _CONFIG_SUFFIXES:
        return "config"
    return "other"


def _bundle_hash(skill_hash: str, resources: list[SourceResource]) -> str:
    material = [f"SKILL.md\0{skill_hash}"]
    material.extend(f"{resource.path}\0{resource.sha256}" for resource in resources)
    return hashlib.sha256("\n".join(material).encode("utf-8")).hexdigest()


class GitHubSkillCorpusSource:
    """One SourceArtifact per recursively discovered SKILL.md at an exact commit."""

    source_type = "skill_package"

    def __init__(
        self,
        spec: CorpusSourceSpec,
        *,
        http_get: HttpGetBytes | None = None,
        max_resource_files: int = MAX_RESOURCE_FILES,
        max_resource_bytes: int = MAX_RESOURCE_BYTES,
        max_bundle_bytes: int = MAX_BUNDLE_BYTES,
        include_paths: set[str] | None = None,
    ) -> None:
        self.spec = spec
        self._owner, self._repo = _parse_repo_url(spec.repo)
        self._http_get = http_get or _default_http_get
        self._use_git = http_get is None
        self._max_resource_files = max_resource_files
        self._max_resource_bytes = max_resource_bytes
        self._max_bundle_bytes = max_bundle_bytes
        self._include_paths = {
            path.replace("\\", "/").strip("/") for path in (include_paths or set())
        }
        self._commit: str | None = None
        self._entries: dict[str, dict] = {}
        self._license: dict = {}
        self._byte_cache: dict[str, bytes] = {}
        self._checkout: tempfile.TemporaryDirectory[str] | None = None

    def _git(self, *args: str) -> str:
        assert self._checkout is not None
        result = subprocess.run(
            ["git", "-C", self._checkout.name, *args], capture_output=True,
            text=True, encoding="utf-8", timeout=300,
        )
        if result.returncode:
            raise RuntimeError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
        return result.stdout

    def _ensure_git_snapshot(self) -> None:
        self._checkout = tempfile.TemporaryDirectory(prefix="stealth-skill-source-")
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:limit=2097152",
             "--no-checkout", "--quiet",
             self.spec.repo, self._checkout.name],
            capture_output=True, text=True, encoding="utf-8", timeout=300,
        )
        if clone.returncode:
            raise RuntimeError(f"git clone failed for {self.spec.repo}: {clone.stderr.strip()}")
        ref = "HEAD" if self.spec.ref == "HEAD" else self.spec.ref
        commit = self._git("rev-parse", f"{ref}^{{commit}}").strip().lower()
        if not _COMMIT_RE.fullmatch(commit):
            raise RuntimeError(f"git did not resolve {self.slug}@{self.spec.ref} to a commit SHA")
        lines = self._git("ls-tree", "-r", "-l", commit).splitlines()
        if len(lines) > MAX_TREE_ENTRIES:
            raise RuntimeError(f"Git tree for {self.slug}@{commit} exceeds {MAX_TREE_ENTRIES} entries")
        for line in lines:
            header, path_raw = line.split("\t", 1)
            mode, obj_type, sha, size_raw = header.split(maxsplit=3)
            path = _safe_repo_path(path_raw)
            if path and obj_type == "blob" and mode != "120000":
                self._entries[path] = {
                    "mode": mode, "type": obj_type, "sha": sha,
                    "size": int(size_raw) if size_raw.isdigit() else 0,
                }
        self._commit = commit
        for candidate in ("LICENSE", "LICENSE.md", "LICENSE.txt"):
            if candidate in self._entries:
                self._license = {"path": candidate, "spdx_id": None, "name": None, "url": None}
                break

    @property
    def slug(self) -> str:
        return f"{self._owner}/{self._repo}"

    def _json_get(self, url: str) -> dict:
        status, body = self._http_get(url)
        if status != 200:
            raise RuntimeError(f"GitHub API {url} returned {status}")
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"GitHub API {url} returned a non-object")
        return value

    def _ensure_snapshot(self) -> None:
        if self._commit is not None:
            return
        if self._use_git:
            self._ensure_git_snapshot()
            return
        ref = quote(self.spec.ref, safe="")
        commit_doc = self._json_get(
            f"https://api.github.com/repos/{self._owner}/{self._repo}/commits/{ref}"
        )
        commit = str(commit_doc.get("sha") or "").lower()
        if not _COMMIT_RE.fullmatch(commit):
            raise RuntimeError(f"GitHub did not resolve {self.slug}@{self.spec.ref} to a commit SHA")
        tree = self._json_get(
            f"https://api.github.com/repos/{self._owner}/{self._repo}/git/trees/{commit}?recursive=1"
        )
        if tree.get("truncated"):
            raise RuntimeError(f"GitHub tree for {self.slug}@{commit} is truncated")
        entries = tree.get("tree") or []
        if len(entries) > MAX_TREE_ENTRIES:
            raise RuntimeError(f"GitHub tree for {self.slug}@{commit} exceeds {MAX_TREE_ENTRIES} entries")
        for entry in entries:
            path = _safe_repo_path(str(entry.get("path") or ""))
            if path and entry.get("type") == "blob" and entry.get("mode") != "120000":
                self._entries[path] = dict(entry)

        status, license_body = self._http_get(
            f"https://api.github.com/repos/{self._owner}/{self._repo}/license"
        )
        if status == 200:
            raw_license = json.loads(license_body.decode("utf-8"))
            license_info = raw_license.get("license") or {}
            self._license = {
                "spdx_id": license_info.get("spdx_id"),
                "name": license_info.get("name"),
                "url": raw_license.get("html_url"),
                "path": raw_license.get("path"),
            }
        self._commit = commit

    def discover(self) -> Iterator[SourceRef]:
        self._ensure_snapshot()
        assert self._commit is not None
        subtree = (self.spec.path or "").strip("/")
        paths = [
            path for path in self._entries
            if PurePosixPath(path).name.lower() == _SKILL_NAME
            and (not subtree or path == subtree + "/SKILL.md" or path.startswith(subtree + "/"))
            and (not self._include_paths or path in self._include_paths)
        ]
        for path in sorted(paths):
            yield SourceRef(
                uri=f"https://github.com/{self.slug}/blob/{self._commit}/{path}",
                repository=self.slug,
                path=path,
                commit=self._commit,
                source_id=self.spec.id,
            )

    def snapshot_metadata(self) -> dict:
        """Reproducible repository-level provenance, even for zero-skill repos."""
        self._ensure_snapshot()
        assert self._commit is not None
        tree_material = "\n".join(
            f"{path}\0{entry.get('sha', '')}\0{entry.get('size', '')}"
            for path, entry in sorted(self._entries.items())
        )
        return {
            "source_id": self.spec.id,
            "repo_url": self.spec.repo,
            "resolved_commit": self._commit,
            "license_metadata": dict(self._license),
            "source_path": self.spec.path,
            "content_hash": hashlib.sha256(tree_material.encode("utf-8")).hexdigest(),
            "expected_format": self.spec.expected_format,
        }

    def _fetch_path(self, path: str, *, limit: int) -> bytes:
        if path in self._byte_cache:
            return self._byte_cache[path]
        entry = self._entries[path]
        advertised_size = int(entry.get("size") or 0)
        if advertised_size > limit:
            raise RuntimeError(f"resource {path!r} exceeds {limit} bytes")
        assert self._commit is not None
        if self._use_git:
            result = subprocess.run(
                ["git", "-C", self._checkout.name, "show", f"{self._commit}:{path}"],
                capture_output=True, timeout=120,
            )
            if result.returncode:
                raise RuntimeError(f"git show failed for {path!r}: {result.stderr.decode(errors='replace').strip()}")
            body = result.stdout
            if len(body) > limit:
                raise RuntimeError(f"resource {path!r} body exceeds {limit} bytes")
            self._byte_cache[path] = body
            return body
        blob_sha = entry.get("sha")
        if blob_sha:
            url = f"https://api.github.com/repos/{self._owner}/{self._repo}/git/blobs/{blob_sha}"
            status, encoded = self._http_get(url)
            if status != 200:
                raise RuntimeError(f"GitHub blob fetch {url} returned {status}")
            document = json.loads(encoded.decode("utf-8"))
            if document.get("encoding") != "base64":
                raise RuntimeError(f"GitHub blob {blob_sha} did not use base64 encoding")
            body = base64.b64decode(document.get("content") or "", validate=False)
        else:
            url = f"https://raw.githubusercontent.com/{self.slug}/{self._commit}/{quote(path, safe='/')}"
            status, body = self._http_get(url)
            if status != 200:
                raise RuntimeError(f"raw fetch {url} returned {status}")
        if len(body) > limit:
            raise RuntimeError(f"resource {path!r} body exceeds {limit} bytes")
        self._byte_cache[path] = body
        return body

    def _referenced_paths(self, content: str, *, skill_dir: str) -> set[str]:
        raw_refs = _MARKDOWN_LINK_RE.findall(content) + _BACKTICK_PATH_RE.findall(content)
        resolved: set[str] = set()
        for raw in raw_refs:
            raw_path = urlsplit(raw.strip().split(maxsplit=1)[0]).path
            if not raw_path or raw_path.startswith("/"):
                continue
            path = _safe_repo_path(posixpath.normpath(posixpath.join(skill_dir, raw_path)))
            if path and path in self._entries:
                resolved.add(path)
        return resolved

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        self._ensure_snapshot()
        if ref.commit != self._commit:
            raise ValueError("SourceRef commit does not match this adapter's immutable snapshot")
        if not ref.path or ref.path not in self._entries:
            raise ValueError(f"unknown SKILL.md path {ref.path!r}")
        skill_bytes = self._fetch_path(ref.path, limit=self._max_resource_bytes)
        try:
            content = skill_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"SKILL.md {ref.path!r} is not UTF-8 text") from exc

        skill_dir = str(PurePosixPath(ref.path).parent)
        candidates: set[str] = set()
        for path in self._entries:
            if not path.startswith(skill_dir + "/") or path == ref.path:
                continue
            relative = PurePosixPath(path).relative_to(PurePosixPath(skill_dir))
            if relative.parts and (
                relative.parts[0].lower() in _BUNDLED_DIRS
                or PurePosixPath(path).suffix.lower() in _CONFIG_SUFFIXES
            ):
                candidates.add(path)
        referenced = self._referenced_paths(content, skill_dir=skill_dir)
        related_skills = {
            path for path in referenced if PurePosixPath(path).name.lower() == _SKILL_NAME
        }
        candidates.update(referenced - related_skills)
        if len(candidates) > self._max_resource_files:
            raise RuntimeError(
                f"skill package {ref.path!r} exceeds {self._max_resource_files} resource files"
            )

        resources: list[SourceResource] = []
        total = 0
        seen_hashes: set[str] = set()
        for path in sorted(candidates):
            data = self._fetch_path(path, limit=self._max_resource_bytes)
            total += len(data)
            if total > self._max_bundle_bytes:
                raise RuntimeError(f"skill package {ref.path!r} exceeds bundle byte limit")
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            resources.append(SourceResource(
                path=path,
                kind=_resource_kind(path, skill_dir=skill_dir),
                sha256=digest,
                size=len(data),
                content=data,
            ))

        skill_hash = compute_content_hash(content)
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=skill_hash,
            repository=self.slug,
            path=ref.path,
            commit=self._commit,
            source_id=self.spec.id,
            license_metadata=dict(self._license),
            resources=tuple(resources),
            bundle_hash=_bundle_hash(skill_hash, resources),
            related_skill_paths=tuple(sorted(related_skills)),
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.bundle_hash or artifact.content_hash

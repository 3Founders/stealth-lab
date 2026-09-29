"""Fetch a repository at one pinned commit as in-memory text, for the code cascade. Blocking: call it through `run_blocking`.

ONE TARBALL, NOT ONE REQUEST PER FILE
    The structural stages need to READ most files of a repository (a file's importance is its structure and who imports it), so
    a request per file is thousands of calls against a rate-limited API. A single `tarball/<sha>` request returns the whole
    tree at the exact commit; files are filtered by path as the archive streams by, so what is held in memory is only the source
    that survives S1, plus the LICENSE files and `go.mod`.

PINNED AND REFUSING
    The commit sha is resolved first and every later request names it, so the license verdict, the tree and the bytes describe
    ONE state of the repository. Anything that cannot be read completely is refused rather than half-ingested: a tarball over the
    size cap, an archived or missing repository. Every URL, including each redirect hop, goes through the SSRF guard.
"""
from __future__ import annotations

import io
import json
import logging
import tarfile
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from app.services.code_cascade import inventory

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
MAX_TARBALL_BYTES = 80_000_000
MAX_FILES_KEPT = 8_000
_KEEP_ALWAYS = frozenset({"go.mod"})

HttpJson = Callable[[str], tuple[int, Any]]
Download = Callable[[str, int], bytes]


class FetchRefused(RuntimeError):
    """The repository cannot be ingested as asked; the message is the reason."""


@dataclass(frozen=True)
class RepoSnapshot:
    repository: str
    commit: str
    license_spdx: Optional[str]                 # GitHub/Licensee's reading, pinned to `commit`
    files: dict[str, str]                        # path -> text, S1-path-surviving source + go.mod
    tree: list[dict]                             # every blob path in the archive: {"path", "type": "blob", "size"}
    license_index: dict[str, str]                # LICENSE blob path -> the SPDX id its own text identifies ("" if none)
    default_branch: Optional[str] = None
    stars: Optional[int] = None
    archived: bool = False
    fork: bool = False
    skipped: dict[str, int] = field(default_factory=dict)


def _headers() -> dict[str, str]:
    from app.services.ingestion_sources.skillmd_dataset import _github_headers

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "stealthlab-code-cascade"}
    headers.update(_github_headers())        # adds Authorization when a token is configured (env or backend/.env)
    return headers


def default_http_json(url: str) -> tuple[int, Any]:
    import httpx

    from app.services.screening import assert_safe_locator

    assert_safe_locator(url)
    response = httpx.get(url, timeout=60, follow_redirects=False, headers=_headers())
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def default_download(url: str, max_bytes: int) -> bytes:
    """Stream a URL into memory, following redirects by hand so every hop is SSRF-checked, refusing past `max_bytes`."""
    import httpx

    from app.services.screening import assert_safe_locator

    current = url
    for _ in range(5):
        assert_safe_locator(current)
        with httpx.stream("GET", current, timeout=120, follow_redirects=False, headers=_headers()) as response:
            if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                current = str(httpx.URL(current).join(response.headers["location"]))
                continue
            if response.status_code != 200:
                raise FetchRefused(f"download {url} answered HTTP {response.status_code}")
            declared = int(response.headers.get("content-length") or 0)
            if declared > max_bytes:
                raise FetchRefused(f"archive is {declared} bytes, over the {max_bytes} cap")
            buffer = io.BytesIO()
            for chunk in response.iter_bytes():
                buffer.write(chunk)
                if buffer.tell() > max_bytes:
                    raise FetchRefused(f"archive exceeds the {max_bytes} byte cap")
            return buffer.getvalue()
    raise FetchRefused(f"too many redirects fetching {url}")


def read_archive(data: bytes) -> tuple[dict[str, str], list[dict], dict[str, str], dict[str, int]]:
    """(files, tree, license_texts, skipped) from a tar.gz: top-level directory stripped, only surviving source decoded."""
    from app.services.repo_license_policy import license_paths

    files: dict[str, str] = {}
    license_texts: dict[str, str] = {}
    tree: list[dict] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        members = [m for m in archive.getmembers() if m.isfile()]
        for member in members:
            parts = member.name.split("/", 1)
            path = parts[1] if len(parts) == 2 else parts[0]
            if path:
                tree.append({"path": path, "type": "blob", "size": member.size})
        wanted_licenses = set(license_paths(tree))
        for member in members:
            parts = member.name.split("/", 1)
            path = parts[1] if len(parts) == 2 else parts[0]
            if not path:
                continue
            is_license = path in wanted_licenses
            if not (is_license or path in _KEEP_ALWAYS or inventory.path_verdict(path, member.size).kept):
                skip(inventory.path_verdict(path, member.size).reason or "not_wanted")
                continue
            if len(files) >= MAX_FILES_KEPT and not is_license:
                skip("file_cap")
                continue
            handle = archive.extractfile(member)
            raw = handle.read() if handle is not None else b""
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                skip("not_utf8")
                continue
            (license_texts if is_license else files)[path] = text
    return files, tree, license_texts, skipped


def fetch_snapshot(repository: str, ref: Optional[str] = None, *, http_json: Optional[HttpJson] = None,
                   download: Optional[Download] = None) -> RepoSnapshot:
    from app.services.repo_license_policy import identify_spdx_from_text

    get = http_json or default_http_json
    grab = download or default_download
    api = f"{GITHUB_API}/repos/{repository}"

    status, meta = get(api)
    if status != 200 or not isinstance(meta, dict):
        raise FetchRefused(f"{repository}: GitHub answered HTTP {status}")
    if meta.get("archived"):
        raise FetchRefused(f"{repository} is archived")
    branch = meta.get("default_branch")
    status, commit_doc = get(f"{api}/commits/{ref or branch or 'HEAD'}")
    sha = commit_doc.get("sha") if isinstance(commit_doc, dict) else None
    if status != 200 or not sha:
        raise FetchRefused(f"{repository}: cannot resolve {ref or branch or 'HEAD'} to a commit (HTTP {status})")

    spdx: Optional[str] = None
    status, lic = get(f"{api}/license?ref={sha}")          # pinned: an unpinned id describes another set of files
    if status == 200 and isinstance(lic, dict) and isinstance(lic.get("license"), dict):
        value = lic["license"].get("spdx_id")
        spdx = value if isinstance(value, str) and value.strip() else None

    files, tree, license_texts, skipped = read_archive(grab(f"{api}/tarball/{sha}", MAX_TARBALL_BYTES))
    index = {path: (identify_spdx_from_text(text) or "") for path, text in license_texts.items()}
    if spdx:
        for path in list(index):
            if not index[path] and "/" not in path:            # the root file IS the file GitHub's detector read
                index[path] = spdx
    return RepoSnapshot(
        repository=repository, commit=sha, license_spdx=spdx, files=files, tree=tree, license_index=index,
        default_branch=branch, stars=meta.get("stargazers_count"), archived=bool(meta.get("archived")),
        fork=bool(meta.get("fork")), skipped=skipped)

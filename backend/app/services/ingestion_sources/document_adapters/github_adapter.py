"""GitHub adapters: one file at an exact commit (`GitHubFileAdapter`), and
repo-wide doc discovery (`GitHubRepoDocsAdapter`). Both reuse the
existing GitHub plumbing rather than re-implementing SSRF-guarded HTTP
or repo-URL parsing: `_default_http_get` /
`assert_safe_locator` (via `github_corpus`) and `_parse_repo_url`
(`skill_md`).

`GitHubFileAdapter` does not re-parse formats itself -- it fetches raw
bytes with GitHub provenance attached, then DISPATCHES to the matching
per-format `DocumentSourceAdapter` (Markdown/SkillMarkdown/Html/Pdf/Docx)
for `normalize()`. This is the "GitHub file/repository document source"
the prompt asks for, composed from adapters that already exist rather
than a parallel implementation.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable, Iterator
from urllib.parse import quote

from app.services.ingestion_sources.canonical import CanonicalDocument
from app.services.ingestion_sources.document_adapter import (
    AdapterNotApplicable,
    DocumentLocator,
    DocumentSourceAdapter,
    RawDocument,
)
from app.services.ingestion_sources.document_adapters.docx_adapter import DocxAdapter
from app.services.ingestion_sources.document_adapters.html_adapter import HtmlAdapter
from app.services.ingestion_sources.document_adapters.markdown_adapter import (
    MarkdownAdapter,
    looks_like_skill_md,
)
from app.services.ingestion_sources.document_adapters.pdf_adapter import PdfAdapter
from app.services.ingestion_sources.document_adapters.skill_markdown_adapter import (
    SkillMarkdownAdapter,
)
from app.services.ingestion_sources.github_corpus import _default_http_get, _safe_repo_path
from app.services.ingestion_sources.skill_md import _parse_repo_url

HttpGetBytes = Callable[[str], "tuple[int, bytes]"]

_DOC_SUFFIXES = (".md", ".markdown", ".mdx", ".html", ".htm", ".pdf", ".docx")
_MAX_TREE_ENTRIES = 100_000


def _source_id(uri: str) -> str:
    return hashlib.sha256(f"github_file\0{uri}".encode("utf-8")).hexdigest()[:32]


def _format_adapter_for(path: str):
    lowered = path.lower()
    if lowered.endswith((".md", ".markdown", ".mdx")):
        # A generic locator built purely from a path can't sniff content
        # up front; can_handle needs the bytes, so this pre-selection is
        # refined again once fetched, in `normalize()` below.
        return None  # resolved after fetch, once content is known
    if lowered.endswith((".html", ".htm")):
        return HtmlAdapter()
    if lowered.endswith(".pdf"):
        return PdfAdapter()
    if lowered.endswith(".docx"):
        return DocxAdapter()
    return None


class GitHubFileAdapter(DocumentSourceAdapter):
    """One document at one resolved commit SHA. `locator.repository` is
    `"owner/repo"`, `locator.path` the in-repo path, `locator.commit` a
    ref (branch/tag/SHA) or `None` for the default branch."""

    source_type = "document_github_file"

    def __init__(self, *, http_get: HttpGetBytes | None = None) -> None:
        self._http_get = http_get or _default_http_get

    def can_handle(self, locator: DocumentLocator) -> bool:
        if not locator.repository or not locator.path:
            return False
        return locator.path.lower().endswith(_DOC_SUFFIXES)

    def _resolve_commit(self, owner: str, repo: str, ref: str) -> str:
        import json

        status, body = self._http_get(
            f"https://api.github.com/repos/{owner}/{repo}/commits/{quote(ref, safe='')}"
        )
        if status != 200:
            raise RuntimeError(f"GitHub commit lookup for {owner}/{repo}@{ref} returned {status}")
        sha = json.loads(body.decode("utf-8")).get("sha")
        if not sha:
            raise RuntimeError(f"GitHub did not resolve {owner}/{repo}@{ref} to a commit SHA")
        return str(sha).lower()

    def fetch(self, locator: DocumentLocator) -> RawDocument:
        if not self.can_handle(locator):
            raise AdapterNotApplicable(f"GitHubFileAdapter cannot handle {locator!r}")
        owner, repo = _parse_repo_url(f"https://github.com/{locator.repository}")
        safe_path = _safe_repo_path(locator.path)
        if not safe_path:
            raise AdapterNotApplicable(f"unsafe repo path: {locator.path!r}")
        commit = locator.commit or self._resolve_commit(owner, repo, "HEAD")
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{quote(safe_path, safe='/')}"
        status, body = self._http_get(raw_url)
        if status != 200:
            raise RuntimeError(f"GitHub raw fetch {raw_url} returned {status}")
        uri = f"https://github.com/{owner}/{repo}/blob/{commit}/{safe_path}"
        return RawDocument(
            source_type=self.source_type, uri=uri, content=body,
            repository=f"{owner}/{repo}", path=safe_path, commit=commit,
            fetched_at=datetime.now(timezone.utc), metadata=dict(locator.metadata),
        )

    def normalize(self, raw: RawDocument) -> CanonicalDocument:
        format_adapter = _format_adapter_for(raw.path or "")
        if format_adapter is None:
            # Markdown-family: decide skill-vs-generic from the real bytes.
            text_preview = raw.content.decode("utf-8", errors="replace")
            format_adapter = (
                SkillMarkdownAdapter()
                if looks_like_skill_md(raw.path, text_preview)
                else MarkdownAdapter()
            )
        doc = format_adapter.normalize(raw)
        # Re-stamp identity/type as the GitHub adapter's own -- provenance
        # (repository/path/commit) already flows through from `raw`.
        from dataclasses import replace

        return replace(
            doc,
            source_id=_source_id(raw.uri),
            source_type=self.source_type,
        )


class GitHubRepoDocsAdapter:
    """Repo-wide doc discovery: walks a repo tree at one resolved commit
    and yields a `DocumentLocator` per matching doc file, for the caller
    to hand to `GitHubFileAdapter`. Not a `DocumentSourceAdapter` itself
    (there is no single `fetch`/`normalize` for "a whole repository") --
    same shape as the existing `SourceAdapter.discover()` idiom in
    `ingestion_sources/base.py`.
    """

    source_type = "document_github_repo_docs"

    def __init__(self, *, http_get: HttpGetBytes | None = None) -> None:
        self._http_get = http_get or _default_http_get
        self._file_adapter = GitHubFileAdapter(http_get=self._http_get)

    def _json_get(self, url: str) -> dict:
        import json

        status, body = self._http_get(url)
        if status != 200:
            raise RuntimeError(f"GitHub API {url} returned {status}")
        return json.loads(body.decode("utf-8"))

    def discover(self, repository: str, *, ref: str = "HEAD", path_prefix: str = "") -> Iterator[DocumentLocator]:
        owner, repo = _parse_repo_url(f"https://github.com/{repository}")
        commit_doc = self._json_get(f"https://api.github.com/repos/{owner}/{repo}/commits/{quote(ref, safe='')}")
        commit = str(commit_doc.get("sha") or "").lower()
        if not commit:
            raise RuntimeError(f"GitHub did not resolve {repository}@{ref} to a commit SHA")
        tree = self._json_get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{commit}?recursive=1"
        )
        if tree.get("truncated"):
            raise RuntimeError(f"GitHub tree for {repository}@{commit} is truncated")
        entries = tree.get("tree") or []
        if len(entries) > _MAX_TREE_ENTRIES:
            raise RuntimeError(f"GitHub tree for {repository}@{commit} exceeds {_MAX_TREE_ENTRIES} entries")
        prefix = path_prefix.strip("/")
        for entry in entries:
            raw_path = str(entry.get("path") or "")
            path = _safe_repo_path(raw_path)
            if not path or entry.get("type") != "blob":
                continue
            if prefix and not (path == prefix or path.startswith(prefix + "/")):
                continue
            if not path.lower().endswith(_DOC_SUFFIXES):
                continue
            yield DocumentLocator(
                uri=f"https://github.com/{owner}/{repo}/blob/{commit}/{path}",
                repository=f"{owner}/{repo}", path=path, commit=commit,
            )

    def fetch_and_normalize_all(
        self, repository: str, *, ref: str = "HEAD", path_prefix: str = ""
    ) -> Iterator[CanonicalDocument]:
        for locator in self.discover(repository, ref=ref, path_prefix=path_prefix):
            yield self._file_adapter.fetch_and_normalize(locator)

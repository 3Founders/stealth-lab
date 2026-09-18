"""Generic Markdown adapter -- any `.md`/`.markdown` file that is NOT a
SKILL.md package (see `skill_markdown_adapter.py`, which must be checked
first by callers/`select_adapter`)."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone

from app.services.ingestion_sources.canonical import CanonicalDocument
from app.services.ingestion_sources.document_adapter import (
    AdapterNotApplicable,
    DocumentLocator,
    DocumentSourceAdapter,
    RawDocument,
)
from app.services.ingestion_sources.document_adapters._markdown_common import (
    elements_from_markdown,
)

_MD_SUFFIXES = (".md", ".markdown", ".mdx")
_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def looks_like_skill_md(filename: str | None, content: str) -> bool:
    """Shared heuristic: a SKILL.md package file is named exactly
    `SKILL.md` (case-insensitive) OR its frontmatter declares
    `name:`/`description:` in the shape `parse_skill_md` expects. Kept
    here (not duplicated) so `MarkdownAdapter.can_handle` and
    `SkillMarkdownAdapter.can_handle` never disagree."""
    if filename and os.path.basename(filename).lower() == "skill.md":
        return True
    head = content[:2000]
    if head.startswith("---"):
        end = head.find("\n---", 3)
        if end != -1:
            frontmatter = head[3:end]
            return "name:" in frontmatter and "description:" in frontmatter
    return False


def _source_id(uri: str) -> str:
    return hashlib.sha256(f"markdown\0{uri}".encode("utf-8")).hexdigest()[:32]


class MarkdownAdapter(DocumentSourceAdapter):
    source_type = "document_markdown"

    def can_handle(self, locator: DocumentLocator) -> bool:
        name = locator.filename or locator.local_path or locator.uri or ""
        if locator.content_type_hint == "markdown":
            return True
        if not name.lower().endswith(_MD_SUFFIXES):
            return False
        # Defer to SkillMarkdownAdapter for real SKILL.md packages.
        content = locator.raw_bytes
        if content is not None:
            try:
                return not looks_like_skill_md(name, content.decode("utf-8", errors="replace"))
            except Exception:
                return True
        return not os.path.basename(name).lower() == "skill.md"

    def fetch(self, locator: DocumentLocator) -> RawDocument:
        if locator.raw_bytes is not None:
            data = locator.raw_bytes
        elif locator.local_path:
            with open(locator.local_path, "rb") as fh:
                data = fh.read()
        else:
            raise AdapterNotApplicable("MarkdownAdapter.fetch requires local_path or raw_bytes")
        uri = locator.uri or locator.local_path or locator.filename or "unknown.md"
        return RawDocument(
            source_type=self.source_type, uri=uri, content=data, content_type="text/markdown",
            repository=locator.repository, path=locator.path, commit=locator.commit,
            fetched_at=datetime.now(timezone.utc), metadata=dict(locator.metadata),
        )

    def normalize(self, raw: RawDocument) -> CanonicalDocument:
        warnings = []
        try:
            text = raw.content.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.content.decode("utf-8", errors="replace")
            warnings.append(("quarantine:decode_error", "content was not valid UTF-8; lossy-decoded"))

        sections, code_blocks, tables, links = elements_from_markdown(text)
        title_match = _H1_RE.search(text)
        title = title_match.group(1) if title_match else (raw.path or raw.uri)

        doc = CanonicalDocument(
            source_id=_source_id(raw.uri),
            source_type=self.source_type,
            source_uri=raw.uri,
            title=title,
            canonical_markdown=text,
            sections=sections,
            code_blocks=code_blocks,
            tables=tables,
            links=links,
            repository=raw.repository,
            path=raw.path,
            version=raw.commit,
            metadata=dict(raw.metadata),
        )
        for code, message in warnings:
            doc = doc.with_warning(code, message)
        return doc

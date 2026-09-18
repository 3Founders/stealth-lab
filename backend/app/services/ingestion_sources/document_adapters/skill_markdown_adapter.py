"""SKILL.md adapter -- a SPECIALIZED package format, not a generic
Markdown document (Prompt 2, "SKILL.MD" section). This wraps the
EXISTING `app.services.skill_ingestion.parse_skill_md` parser rather
than replacing it: frontmatter/prerequisites/steps/references/expected
outcomes/failure modes keep coming from that one deterministic parser,
so nothing here regresses `test_skill_ingestion_offline.py` /
`test_skill_ingestion_e2e.py`. This module's only job is to wrap that
parse into the shared `CanonicalDocument` shape so a SKILL.md can flow
through the same `document_ingestion` pipeline as any other document
when a caller wants that (e.g. discovery via `GitHubRepoDocsAdapter`);
the existing `skill_ingestion.compile_skill_artifact` pipeline is
untouched and remains the primary path for skill-package corpora.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
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
from app.services.ingestion_sources.document_adapters.markdown_adapter import (
    looks_like_skill_md,
)
from app.services.skill_ingestion import SkillMdParseError, parse_skill_md


def _source_id(uri: str) -> str:
    return hashlib.sha256(f"skill_markdown\0{uri}".encode("utf-8")).hexdigest()[:32]


class SkillMarkdownAdapter(DocumentSourceAdapter):
    source_type = "document_skill_markdown"

    def can_handle(self, locator: DocumentLocator) -> bool:
        name = locator.filename or locator.local_path or locator.uri or ""
        if locator.content_type_hint == "skill_markdown":
            return True
        if not name.lower().endswith((".md", ".markdown")):
            return False
        content = locator.raw_bytes
        if content is None:
            return name.split("/")[-1].lower() == "skill.md"
        try:
            return looks_like_skill_md(name, content.decode("utf-8", errors="replace"))
        except Exception:
            return False

    def fetch(self, locator: DocumentLocator) -> RawDocument:
        if locator.raw_bytes is not None:
            data = locator.raw_bytes
        elif locator.local_path:
            with open(locator.local_path, "rb") as fh:
                data = fh.read()
        else:
            raise AdapterNotApplicable("SkillMarkdownAdapter.fetch requires local_path or raw_bytes")
        uri = locator.uri or locator.local_path or locator.filename or "SKILL.md"
        return RawDocument(
            source_type=self.source_type, uri=uri, content=data, content_type="text/markdown",
            repository=locator.repository, path=locator.path, commit=locator.commit,
            fetched_at=datetime.now(timezone.utc), metadata=dict(locator.metadata),
        )

    def normalize(self, raw: RawDocument) -> CanonicalDocument:
        text = raw.content.decode("utf-8", errors="replace")
        warnings: list[tuple[str, str]] = []
        try:
            parsed = parse_skill_md(text)
            structured = asdict(parsed)
        except SkillMdParseError as exc:
            # Lossless fallback: still a valid CanonicalDocument, just
            # without structured fields -- never silently drop the body.
            structured = {}
            warnings.append(("quarantine:skill_parse_failed", str(exc)))

        sections, code_blocks, tables, links = elements_from_markdown(text)
        title = structured.get("name") or raw.path or raw.uri

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
            license=structured.get("license"),
            metadata=dict(raw.metadata),
            structured=structured,
        )
        for code, message in warnings:
            doc = doc.with_warning(code, message)
        return doc

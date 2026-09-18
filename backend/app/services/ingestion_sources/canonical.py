"""
The canonical document IR that every non-trace SourceAdapter normalizes
into (Prompt 2: "make the document/source ingestion path accept
heterogeneous sources through a common adapter interface").

WHY A SEPARATE TYPE FROM `SourceArtifact` (ingestion_sources/base.py)
    `SourceArtifact` is the *fetched-bytes* unit for the existing
    corpus-discovery adapters (`discover()`/`fetch()`/`fingerprint()`) --
    one string of content plus repo/path/commit. It has no place to hold
    a PDF's per-page table, an HTML doc's DOM heading path, or a DOCX's
    paragraph identity. `CanonicalDocument` is the *understood-structure*
    unit downstream of that: still lossless, still provenance-carrying,
    but shaped so the existing artifact/block pipeline
    (`app.services.artifact_blocks.normalize_markdown`) can consume its
    `canonical_markdown` exactly like any other document, and so
    `document_ingestion.ingest_canonical_document` can write ONE
    `ingested_artifacts` row from it without a second provenance table.

FIDELITY, NOT INTERPRETATION
    Nothing in this module infers a Goal/Claim/Procedure/Implementation.
    It only preserves structure (headings, code, tables, links) and
    provenance (page/line/commit/DOM path) so the existing semantic
    extraction layer can do that later, off `canonical_markdown` and the
    parallel element lists below.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.services.ingestion_sources.base import compute_content_hash

__all__ = [
    "compute_content_hash",
    "SourceLocation",
    "SectionElement",
    "CodeBlockElement",
    "TableElement",
    "LinkElement",
    "ExtractionWarning",
    "CanonicalDocument",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SourceLocation:
    """Where in the ORIGINAL (non-canonical) source an element came from.

    Exactly one of these is normally set per adapter family:
      PDF paragraph      -> page
      GitHub file line   -> repo_path + line_start/line_end
      HTML section       -> dom_path (heading path, e.g. "h1>h2#setup")
      DOCX section       -> paragraph_index / table_index
    `raw` is a free-form human-readable rendering (e.g. "page 4",
    "L120-L134") for adapters whose provenance doesn't fit a typed field.
    """

    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    dom_path: str | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    raw: str | None = None


@dataclass(frozen=True)
class SectionElement:
    heading: str | None
    level: int
    position: int
    text: str
    location: SourceLocation = field(default_factory=SourceLocation)


@dataclass(frozen=True)
class CodeBlockElement:
    text: str
    position: int
    language: str | None = None
    location: SourceLocation = field(default_factory=SourceLocation)


@dataclass(frozen=True)
class TableElement:
    position: int
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    caption: str | None = None
    location: SourceLocation = field(default_factory=SourceLocation)


@dataclass(frozen=True)
class LinkElement:
    text: str
    url: str
    position: int
    location: SourceLocation = field(default_factory=SourceLocation)


@dataclass(frozen=True)
class ExtractionWarning:
    """A disclosed loss or ambiguity, never a silently dropped detail."""

    code: str
    message: str
    location: SourceLocation | None = None


@dataclass(frozen=True)
class CanonicalDocument:
    """The normalized output every DocumentSourceAdapter produces.

    `content_hash` is computed from `canonical_markdown` at construction
    time when not supplied, using the same `compute_content_hash` the
    existing skill-corpus adapters use -- one hashing scheme across both
    ingestion families.
    """

    source_id: str
    source_type: str
    source_uri: str
    title: str
    canonical_markdown: str

    sections: tuple[SectionElement, ...] = ()
    code_blocks: tuple[CodeBlockElement, ...] = ()
    tables: tuple[TableElement, ...] = ()
    links: tuple[LinkElement, ...] = ()

    metadata: dict[str, Any] = field(default_factory=dict)
    author: str | None = None
    license: str | None = None
    version: str | None = None  # commit SHA / doc version / ETag, when known
    repository: str | None = None
    path: str | None = None

    content_hash: str = ""
    extraction_warnings: tuple[ExtractionWarning, ...] = ()

    owner: str | None = None
    visibility: str = "public"
    scope: str | None = None

    # SKILL.md's specialized structured metadata (frontmatter/prerequisites/
    # steps/scripts/references/expected outcomes/failure modes) lives here,
    # never flattened into `metadata` -- see SkillMarkdownAdapter.
    structured: dict[str, Any] = field(default_factory=dict)

    adapter_version: str = "1"
    fetched_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not self.content_hash:
            object.__setattr__(
                self, "content_hash", compute_content_hash(self.canonical_markdown)
            )

    def quarantined(self) -> bool:
        """True when this document has warnings severe enough that the
        orchestration layer should route it through admission review
        rather than straight ingestion (mirrors skill_ingestion's
        'quarantine on review' idiom, without duplicating its gate)."""
        return any(w.code.startswith("quarantine:") for w in self.extraction_warnings)

    def with_warning(self, code: str, message: str, *, location: SourceLocation | None = None) -> "CanonicalDocument":
        return replace(
            self,
            extraction_warnings=self.extraction_warnings + (ExtractionWarning(code, message, location),),
        )

"""
The `DocumentSourceAdapter` contract for heterogeneous, non-trace,
non-SKILL.md-package document sources (Prompt 2).

RELATIONSHIP TO `ingestion_sources.base.SourceAdapter`
    That Protocol is corpus discovery: `discover()` enumerates many
    `SourceRef`s from one configured source (a GitHub repo scanned for
    every SKILL.md), `fetch()` pulls one, and identity is a flat
    `SourceArtifact.content: str`. This module's `DocumentSourceAdapter`
    is per-locator normalization: given ONE `DocumentLocator` (a path, a
    URL, a repo+ref), decide whether this adapter understands its format
    (`can_handle`), pull its raw bytes (`fetch`), and turn it into a
    `CanonicalDocument` (`normalize`). A GitHub *discovery* adapter can
    still sit on top of this -- see `document_adapters/github_adapter.py`
    `GitHubRepoDocsAdapter`, which walks a repo and hands each matching
    path to the right per-format `DocumentSourceAdapter`.

    Kept as a separate class (not folded into `SourceAdapter`) exactly
    because the shapes differ: one Protocol per concern, not one bloated
    interface trying to be both.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.ingestion_sources.canonical import CanonicalDocument

__all__ = ["DocumentLocator", "RawDocument", "DocumentSourceAdapter", "AdapterNotApplicable"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdapterNotApplicable(ValueError):
    """Raised by `fetch`/`normalize` when called on a locator the adapter
    does not actually handle -- callers should always gate with
    `can_handle` first; this is a programmer-error guard, not a normal
    control-flow path."""


@dataclass(frozen=True)
class DocumentLocator:
    """What the caller knows about a source BEFORE it is fetched -- enough
    for `can_handle` to decide, and for `fetch` to go get it.

    Exactly one of `local_path` / `uri` / `raw_bytes` is normally set.
    `content_type_hint` (a MIME type or a caller-asserted format like
    "markdown") lets a caller short-circuit sniffing when it already
    knows the format (e.g. an API document adapter that always receives
    JSON regardless of the endpoint's own Content-Type quirks).
    """

    local_path: str | None = None
    uri: str | None = None
    raw_bytes: bytes | None = None
    filename: str | None = None
    content_type_hint: str | None = None
    repository: str | None = None
    path: str | None = None
    commit: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawDocument:
    """The pulled bytes plus enough provenance to normalize and to fill an
    `ingested_artifacts` row without a second lookup."""

    source_type: str
    uri: str
    content: bytes
    content_type: str | None = None
    repository: str | None = None
    path: str | None = None
    commit: str | None = None
    fetched_at: datetime = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentSourceAdapter(ABC):
    """One adapter per source FORMAT (or format + transport, e.g. a
    GitHub file). Each does only: understand-the-format, fetch, preserve
    provenance, and produce a `CanonicalDocument`. Semantic extraction
    (Goals/Claims/Procedures/Implementations) is deliberately NOT this
    layer's job -- see `canonical.py`'s module docstring.
    """

    source_type: str

    @abstractmethod
    def can_handle(self, locator: DocumentLocator) -> bool:
        """Cheap, sync, side-effect-free: filename/extension/content-type
        sniffing only. Never fetches network/disk to decide."""

    @abstractmethod
    def fetch(self, locator: DocumentLocator) -> RawDocument:
        """Pull the raw bytes for one locator. Raises
        `AdapterNotApplicable` if `locator` is not one this adapter
        handles (defensive -- callers should already have checked
        `can_handle`)."""

    @abstractmethod
    def normalize(self, raw: RawDocument) -> CanonicalDocument:
        """Turn fetched bytes into a `CanonicalDocument`. Must never raise
        on malformed-but-readable input -- return a partial document with
        `extraction_warnings` instead (see `canonical.CanonicalDocument`).
        Only raise for input the adapter cannot decode/open at all."""

    def fetch_and_normalize(self, locator: DocumentLocator) -> CanonicalDocument:
        """Convenience composition; adapters should not need to override
        this."""
        if not self.can_handle(locator):
            raise AdapterNotApplicable(f"{type(self).__name__} cannot handle {locator!r}")
        return self.normalize(self.fetch(locator))

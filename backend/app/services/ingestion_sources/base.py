"""
The source-adapter contract: `SourceRef` (a pointer), `SourceArtifact`
(the pulled bytes plus provenance), and the `SourceAdapter` Protocol every
adapter satisfies.

SYNC ON PURPOSE. `discover()` is a directory walk or a single JSON API
call; `fetch()` is one file read or one HTTP GET. Neither fans out, neither
belongs on the event loop's critical path, and the compiler that consumes
these runs as a batch job, not inside a request handler. Making the
Protocol async would force every call site and every test to carry an
event loop for no concurrency benefit. If a future adapter genuinely needs
to parallelise hundreds of fetches, it can do that internally behind the
same sync `fetch()` -- the contract does not have to change for it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Protocol, runtime_checkable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_content_hash(content: str) -> str:
    """SHA-256 of the UTF-8 bytes. This is the identity the ingestion
    compiler uses for staleness/version detection (plan section 2b step
    3): same hash -> same artifact -> no-op; new hash for a known URI ->
    a new procedure version."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceRef:
    """A lightweight pointer produced by `discover()` and handed back to
    `fetch()`. Carries only what is needed to locate and later attribute
    the artifact -- never its content."""

    uri: str
    repository: str | None = None
    path: str | None = None
    commit: str | None = None


@dataclass(frozen=True)
class SourceArtifact:
    """One fetched unit of public procedural knowledge, with enough
    provenance for `capture_procedure(domain_payload=...)` and the
    `ingested_artifacts` row to be filled without a second lookup."""

    source_type: str
    uri: str
    content: str
    content_hash: str
    repository: str | None = None
    path: str | None = None
    commit: str | None = None
    discovered_at: datetime = field(default_factory=_utcnow)


@runtime_checkable
class SourceAdapter(Protocol):
    """Turns a public source into `SourceArtifact`s. No transport, no
    provider, no format assumptions live here -- an adapter for GitHub
    workflows, a docs site, or a paper index satisfies this same shape."""

    source_type: str

    def discover(self) -> Iterator[SourceRef]:
        """Enumerate the artifacts this source currently exposes."""
        ...

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        """Pull the bytes for one `SourceRef` and stamp its content hash."""
        ...

    def fingerprint(self, artifact: SourceArtifact) -> str:
        """The value the compiler compares to decide "have I seen exactly
        this before". Default implementation is just
        `artifact.content_hash`; an adapter overrides this only if it has
        a cheaper or more precise identity (e.g. a commit SHA it trusts
        more than a re-hash)."""
        ...

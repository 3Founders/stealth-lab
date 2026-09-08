"""
Source adapters for the Global Procedural Library ingestion compiler
(prompts.md Phase 2, sections 2 and 13; .scratch/phase2_ingestion_plan.md
section 2a).

WHY THIS SEAM EXISTS. The compiler's job is `public source -> source
artifact -> procedure extraction`. Everything to the right of "source
artifact" (parse, capability abstraction, dedup, capture_procedure, task
nodes) is source-type-agnostic and already lives in
app/services/skill_ingestion.py. Everything to the LEFT of it -- how you
enumerate what exists and pull its bytes -- is entirely source-specific
(a directory walk, a GitHub API call, later a docs crawler or a paper
index). This package is that left half, and only that: it turns a source
into `SourceArtifact` objects with a stable `content_hash`, and stops.

It is deliberately NOT built around GitHub. `SourceAdapter` names no
transport and no provider; `SOURCE_ADAPTERS` is a plain dispatch dict that
a `github_workflow` / `documentation` / `research_paper` adapter can be
added to later without touching a line of the compiler.
"""
from __future__ import annotations

from app.services.ingestion_sources.base import (
    SourceAdapter,
    SourceArtifact,
    SourceRef,
    SourceResource,
    compute_content_hash,
)
from app.services.ingestion_sources.github_corpus import GitHubSkillCorpusSource
from app.services.ingestion_sources.manifest import (
    CorpusSourceSpec,
    SkillSourceManifest,
    load_source_manifest,
)
from app.services.ingestion_sources.skill_md import (
    SOURCE_ADAPTERS,
    GitHubSkillSource,
    LocalDirSkillSource,
)

__all__ = [
    "SourceAdapter",
    "SourceArtifact",
    "SourceRef",
    "SourceResource",
    "compute_content_hash",
    "GitHubSkillCorpusSource",
    "CorpusSourceSpec",
    "SkillSourceManifest",
    "load_source_manifest",
    "SOURCE_ADAPTERS",
    "GitHubSkillSource",
    "LocalDirSkillSource",
]

"""Version-controlled source manifest for structured skill corpora."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml


SourceType = Literal["github", "github_subtree"]


@dataclass(frozen=True)
class CorpusSourceSpec:
    id: str
    priority: int
    type: SourceType
    repo: str
    expected_format: str
    path: str | None = None
    ref: str = "HEAD"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("source id must not be blank")
        if self.priority < 1:
            raise ValueError(f"source {self.id!r} priority must be positive")
        if self.type not in ("github", "github_subtree"):
            raise ValueError(f"source {self.id!r} has unsupported type {self.type!r}")
        if not self.repo.startswith("https://github.com/"):
            raise ValueError(f"source {self.id!r} must use an https://github.com/ URL")
        if self.type == "github_subtree" and not self.path:
            raise ValueError(f"source {self.id!r} is github_subtree but has no path")
        if self.path:
            normalized = PurePosixPath(self.path.replace("\\", "/"))
            if normalized.is_absolute() or ".." in normalized.parts:
                raise ValueError(f"source {self.id!r} path escapes repository: {self.path!r}")


@dataclass(frozen=True)
class SkillSourceManifest:
    sources: tuple[CorpusSourceSpec, ...]

    def by_id(self, source_id: str) -> CorpusSourceSpec:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(source_id)


def load_source_manifest(path: str | Path) -> SkillSourceManifest:
    """Load and validate one manifest, preserving priority order."""
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise ValueError("skill source manifest must contain a 'sources' list")
    sources = tuple(CorpusSourceSpec(**item) for item in raw["sources"])
    ids = [source.id for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("skill source ids must be unique")
    priorities = [source.priority for source in sources]
    if len(priorities) != len(set(priorities)):
        raise ValueError("skill source priorities must be unique")
    return SkillSourceManifest(tuple(sorted(sources, key=lambda source: source.priority)))

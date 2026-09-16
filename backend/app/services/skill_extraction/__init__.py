"""
LLM-only, Pydantic-enforced extraction of Procedures/Goals/Implementations
from one document/artifact -- replaces skill_ingestion.py's old
deterministic parse_skill_md() entirely (founder directive, 2026-09-15).

Two grounding variants exist side by side (see schema.py's module
docstring for the full rationale): `grounded` requires a verbatim
source_quote per step, checked against the raw document; `ungrounded`
is schema-validated only. `compile_skill_artifact` (skill_ingestion.py)
imports and calls exactly ONE concrete `extract_document` by name --
switching or deleting a variant later is a one-import-line change plus
its own distinct EXTRACTOR_VERSION_* tag, never a scattered edit.

Claims are NOT part of this module -- they stay on their own,
already-LLM-driven, block-anchored path
(app/services/claim_extraction.py), which has real callers beyond
skill_ingestion.py.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol

from app.services.skill_extraction.schema import (
    ExtractedDocument,
    ExtractedGoal,
    ExtractedImplementation,
    ExtractedProcedure,
    ExtractedProcedureStep,
    SkillExtractionTransientFailure,
)

__all__ = [
    "ExtractedDocument",
    "ExtractedGoal",
    "ExtractedImplementation",
    "ExtractedProcedure",
    "ExtractedProcedureStep",
    "SkillExtractionTransientFailure",
    "DocumentExtractor",
]


class DocumentExtractor(Protocol):
    async def extract_document(
        self, client: Any, content: str, *, resource_paths: Optional[list[str]] = None,
        model: str = ..., temperature: float = ...,
    ) -> Optional[ExtractedDocument]: ...

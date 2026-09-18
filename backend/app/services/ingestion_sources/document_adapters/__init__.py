"""Concrete `DocumentSourceAdapter` implementations (Prompt 2 MVP set).

Each module is independent -- no shared base class beyond
`document_adapter.DocumentSourceAdapter` -- per the prompt's "do not
over-generalize source-specific behavior into the base interface."
"""
from __future__ import annotations

from app.services.ingestion_sources.document_adapters.api_adapter import ApiDocumentAdapter
from app.services.ingestion_sources.document_adapters.docx_adapter import DocxAdapter
from app.services.ingestion_sources.document_adapters.github_adapter import (
    GitHubFileAdapter,
    GitHubRepoDocsAdapter,
)
from app.services.ingestion_sources.document_adapters.html_adapter import HtmlAdapter
from app.services.ingestion_sources.document_adapters.markdown_adapter import MarkdownAdapter
from app.services.ingestion_sources.document_adapters.pdf_adapter import PdfAdapter
from app.services.ingestion_sources.document_adapters.skill_markdown_adapter import (
    SkillMarkdownAdapter,
)

DEFAULT_DOCUMENT_ADAPTERS: tuple[object, ...] = (
    SkillMarkdownAdapter(),  # checked before MarkdownAdapter -- more specific format
    MarkdownAdapter(),
    HtmlAdapter(),
    PdfAdapter(),
    DocxAdapter(),
    ApiDocumentAdapter(),
)

__all__ = [
    "ApiDocumentAdapter",
    "DocxAdapter",
    "GitHubFileAdapter",
    "GitHubRepoDocsAdapter",
    "HtmlAdapter",
    "MarkdownAdapter",
    "PdfAdapter",
    "SkillMarkdownAdapter",
    "DEFAULT_DOCUMENT_ADAPTERS",
]


def select_adapter(locator, adapters: tuple[object, ...] = DEFAULT_DOCUMENT_ADAPTERS):
    """First adapter (in order) whose `can_handle(locator)` is True, or
    `None`. Order matters: more specific formats (SkillMarkdownAdapter)
    must be listed before their general fallback (MarkdownAdapter)."""
    for adapter in adapters:
        if adapter.can_handle(locator):
            return adapter
    return None

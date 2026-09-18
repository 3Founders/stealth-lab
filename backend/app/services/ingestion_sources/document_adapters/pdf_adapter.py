"""PDF adapter (pdfplumber -- already a backend dependency). Page number
is the provenance unit for PDFs (Prompt 2: "PDF paragraph -> page
number"); tables are preserved as structured rows/columns rather than
flattened into text.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.services.ingestion_sources.canonical import (
    CanonicalDocument,
    SectionElement,
    SourceLocation,
    TableElement,
)
from app.services.ingestion_sources.document_adapter import (
    AdapterNotApplicable,
    DocumentLocator,
    DocumentSourceAdapter,
    RawDocument,
)

_MAX_PAGES = 2000


def _source_id(uri: str) -> str:
    return hashlib.sha256(f"pdf\0{uri}".encode("utf-8")).hexdigest()[:32]


class PdfAdapter(DocumentSourceAdapter):
    source_type = "document_pdf"

    def can_handle(self, locator: DocumentLocator) -> bool:
        if locator.content_type_hint in ("pdf", "application/pdf"):
            return True
        name = (locator.filename or locator.local_path or locator.uri or "").lower()
        return name.endswith(".pdf")

    def fetch(self, locator: DocumentLocator) -> RawDocument:
        if locator.raw_bytes is not None:
            data = locator.raw_bytes
        elif locator.local_path:
            with open(locator.local_path, "rb") as fh:
                data = fh.read()
        else:
            raise AdapterNotApplicable("PdfAdapter.fetch requires local_path or raw_bytes")
        uri = locator.uri or locator.local_path or locator.filename or "unknown.pdf"
        return RawDocument(
            source_type=self.source_type, uri=uri, content=data, content_type="application/pdf",
            repository=locator.repository, path=locator.path, commit=locator.commit,
            fetched_at=datetime.now(timezone.utc), metadata=dict(locator.metadata),
        )

    def normalize(self, raw: RawDocument) -> CanonicalDocument:
        import io

        import pdfplumber

        warnings: list[tuple[str, str]] = []
        sections: list[SectionElement] = []
        tables: list[TableElement] = []
        markdown_parts: list[str] = []
        position = 0

        try:
            pdf = pdfplumber.open(io.BytesIO(raw.content))
        except Exception as exc:
            # Cannot open at all -- still return a valid, empty, quarantined document.
            doc = CanonicalDocument(
                source_id=_source_id(raw.uri), source_type=self.source_type,
                source_uri=raw.uri, title=raw.path or raw.uri, canonical_markdown="",
                repository=raw.repository, path=raw.path, version=raw.commit,
                metadata=dict(raw.metadata),
            )
            return doc.with_warning("quarantine:pdf_open_failed", f"{type(exc).__name__}: {exc}")

        with pdf:
            page_count = len(pdf.pages)
            if page_count > _MAX_PAGES:
                warnings.append(("warning:truncated", f"PDF has {page_count} pages; truncated to {_MAX_PAGES}"))
            for page_index, page in enumerate(pdf.pages[:_MAX_PAGES]):
                page_number = page_index + 1
                try:
                    page_text = page.extract_text() or ""
                except Exception as exc:
                    warnings.append((f"warning:page_text_failed", f"page {page_number}: {type(exc).__name__}: {exc}"))
                    page_text = ""
                if page_text.strip():
                    position += 1
                    sections.append(
                        SectionElement(
                            heading=None, level=0, position=position, text=page_text,
                            location=SourceLocation(page=page_number),
                        )
                    )
                    markdown_parts.append(f"<!-- page:{page_number} -->\n{page_text}")

                try:
                    page_tables = page.extract_tables() or []
                except Exception as exc:
                    warnings.append((f"warning:page_table_failed", f"page {page_number}: {type(exc).__name__}: {exc}"))
                    page_tables = []
                for table_rows in page_tables:
                    if not table_rows:
                        continue
                    position += 1
                    normalized_rows = [
                        tuple(cell or "" for cell in row) for row in table_rows
                    ]
                    headers, body = normalized_rows[0], tuple(normalized_rows[1:])
                    tables.append(
                        TableElement(
                            position=position, headers=headers, rows=body,
                            location=SourceLocation(page=page_number),
                        )
                    )
                    md_rows = ["| " + " | ".join(headers) + " |",
                               "|" + "|".join(" --- " for _ in headers) + "|"]
                    md_rows.extend("| " + " | ".join(row) + " |" for row in body)
                    markdown_parts.append("\n".join(md_rows))

        if not markdown_parts:
            warnings.append(("quarantine:no_extractable_text", "no text or tables could be extracted (possibly a scanned/image PDF)"))

        canonical_markdown = "\n\n".join(markdown_parts) + ("\n" if markdown_parts else "")
        title = raw.path or raw.uri

        doc = CanonicalDocument(
            source_id=_source_id(raw.uri),
            source_type=self.source_type,
            source_uri=raw.uri,
            title=title,
            canonical_markdown=canonical_markdown,
            sections=tuple(sections),
            tables=tuple(tables),
            repository=raw.repository,
            path=raw.path,
            version=raw.commit,
            metadata={**dict(raw.metadata), "page_count": page_count},
        )
        for code, message in warnings:
            doc = doc.with_warning(code, message)
        return doc

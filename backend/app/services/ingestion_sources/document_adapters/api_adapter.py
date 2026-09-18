"""Generic API-response adapter. Provenance is the endpoint URI plus
whatever request metadata the caller already knows (method, query
params, headers actually sent) -- the caller made the request; this
adapter only normalizes the response it got back (Prompt 2: "API
response -> endpoint + request metadata + response hash", where the
response hash is `CanonicalDocument.content_hash`).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.services.ingestion_sources.canonical import CanonicalDocument, CodeBlockElement, SourceLocation
from app.services.ingestion_sources.document_adapter import (
    AdapterNotApplicable,
    DocumentLocator,
    DocumentSourceAdapter,
    RawDocument,
)


def _source_id(uri: str) -> str:
    return hashlib.sha256(f"api\0{uri}".encode("utf-8")).hexdigest()[:32]


class ApiDocumentAdapter(DocumentSourceAdapter):
    """Normalizes an ALREADY-FETCHED API response. This adapter never
    makes the HTTP call itself -- `fetch()` here just wraps bytes the
    caller supplies via `locator.raw_bytes` plus `locator.metadata`
    (expected keys: `method`, `request_params`, `status_code`,
    `content_type`), matching every other adapter's `fetch(locator)`
    signature without duplicating an HTTP client."""

    source_type = "document_api"

    def can_handle(self, locator: DocumentLocator) -> bool:
        return locator.content_type_hint in ("api", "application/json", "json") or bool(
            locator.metadata.get("is_api_response")
        )

    def fetch(self, locator: DocumentLocator) -> RawDocument:
        if locator.raw_bytes is None:
            raise AdapterNotApplicable("ApiDocumentAdapter.fetch requires raw_bytes (the response body)")
        uri = locator.uri or locator.filename or "unknown-endpoint"
        return RawDocument(
            source_type=self.source_type, uri=uri, content=locator.raw_bytes,
            content_type=locator.metadata.get("content_type", "application/json"),
            repository=locator.repository, path=locator.path, commit=locator.commit,
            fetched_at=datetime.now(timezone.utc), metadata=dict(locator.metadata),
        )

    def normalize(self, raw: RawDocument) -> CanonicalDocument:
        warnings: list[tuple[str, str]] = []
        text = raw.content.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
            pretty = json.dumps(parsed, indent=2, sort_keys=True)
            language = "json"
        except (json.JSONDecodeError, ValueError):
            pretty = text
            language = None
            warnings.append(("warning:non_json_body", "response body was not valid JSON; stored as raw text"))

        response_hash = hashlib.sha256(raw.content).hexdigest()
        canonical_markdown = f"```{language or ''}\n{pretty}\n```\n"
        code_blocks = (
            CodeBlockElement(
                text=pretty, position=0, language=language,
                location=SourceLocation(raw=raw.uri),
            ),
        )

        doc = CanonicalDocument(
            source_id=_source_id(raw.uri),
            source_type=self.source_type,
            source_uri=raw.uri,
            title=raw.uri,
            canonical_markdown=canonical_markdown,
            code_blocks=code_blocks,
            repository=raw.repository,
            path=raw.path,
            version=raw.commit,
            metadata={
                **{k: v for k, v in raw.metadata.items() if k != "is_api_response"},
                "endpoint": raw.uri,
                "response_hash": response_hash,
            },
        )
        for code, message in warnings:
            doc = doc.with_warning(code, message)
        return doc

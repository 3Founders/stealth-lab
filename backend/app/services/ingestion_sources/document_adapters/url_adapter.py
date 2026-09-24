"""Web URL adapter: fetch an http(s) document (blog postmortem, case study, web PDF) and hand the
bytes to the matching format adapter's normalize() -- a transport, like GitHubFileAdapter.

SSRF-safe: every hop (the URL and each redirect target) must pass screening.assert_safe_locator
(http(s) only, no cloud-metadata host, no private/loopback/link-local address after DNS);
redirects are followed manually, at most _MAX_REDIRECTS. Bodies are capped at _MAX_BYTES.
Web pages carry no SPDX license, so the license gate cannot vet them: enqueueing a URL is the
operator's statement that the page may be ingested.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from app.services.ingestion_sources.canonical import CanonicalDocument
from app.services.ingestion_sources.document_adapter import (
    AdapterNotApplicable,
    DocumentLocator,
    DocumentSourceAdapter,
    RawDocument,
)
from app.services.ingestion_sources.document_adapters.docx_adapter import DocxAdapter
from app.services.ingestion_sources.document_adapters.html_adapter import HtmlAdapter
from app.services.ingestion_sources.document_adapters.markdown_adapter import MarkdownAdapter
from app.services.ingestion_sources.document_adapters.pdf_adapter import PdfAdapter

_MAX_BYTES = 20 * 1024 * 1024
_MAX_REDIRECTS = 3
_UA = "StealthLab-ingestion/1.0 (+document fetch)"


def _format_adapter(content_type: str, url: str):
    ct, path = (content_type or "").split(";")[0].strip().lower(), urlsplit(url).path.lower()
    if ct == "application/pdf" or path.endswith(".pdf"):
        return PdfAdapter()
    if "wordprocessingml" in ct or path.endswith(".docx"):
        return DocxAdapter()
    if ct in ("text/markdown", "text/x-markdown", "text/plain") or path.endswith((".md", ".markdown")):
        return MarkdownAdapter()
    return HtmlAdapter()


def _default_fetch(url: str) -> tuple[int, dict, bytes, str]:
    """(status, headers, body, final_url) with per-hop SSRF checks and a size cap."""
    import httpx

    from app.services.screening import assert_safe_locator

    for _ in range(_MAX_REDIRECTS + 1):
        assert_safe_locator(url)
        with httpx.Client(timeout=30.0, follow_redirects=False, headers={"User-Agent": _UA}) as client:
            with client.stream("GET", url) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_BYTES:
                        raise AdapterNotApplicable(f"{url}: body exceeds {_MAX_BYTES} bytes")
                return resp.status_code, dict(resp.headers), bytes(body), url
    raise AdapterNotApplicable(f"too many redirects fetching {url}")


class UrlFetchAdapter(DocumentSourceAdapter):
    source_type = "document_web"

    def __init__(self, fetch=None) -> None:
        self._fetch = fetch or _default_fetch

    def can_handle(self, locator: DocumentLocator) -> bool:
        return bool(locator.uri and locator.uri.lower().startswith(("http://", "https://"))
                    and not locator.local_path and locator.raw_bytes is None
                    and not (locator.repository and locator.path))

    def fetch(self, locator: DocumentLocator) -> RawDocument:
        if not self.can_handle(locator):
            raise AdapterNotApplicable(f"UrlFetchAdapter cannot handle {locator!r}")
        status, headers, body, final_url = self._fetch(locator.uri)
        if status != 200:
            raise RuntimeError(f"fetch {locator.uri} returned HTTP {status}")
        ctype = {k.lower(): v for k, v in headers.items()}.get("content-type", "")
        return RawDocument(
            source_type=self.source_type, uri=locator.uri, content=body, content_type=ctype,
            fetched_at=datetime.now(timezone.utc),
            metadata={**dict(locator.metadata), "final_url": final_url, "content_type": ctype},
        )

    def normalize(self, raw: RawDocument) -> CanonicalDocument:
        doc = _format_adapter(raw.content_type or "", raw.metadata.get("final_url") or raw.uri).normalize(raw)
        return replace(doc, source_id=hashlib.sha256(f"web\0{raw.uri}".encode()).hexdigest()[:32],
                       source_type=self.source_type)

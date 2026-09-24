"""S2 (ingestion_problems.md): production can ingest web pages/PDFs by URL. No network."""
from __future__ import annotations

import asyncio

import pytest

from app.services.ingestion_sources.document_adapter import DocumentLocator
from app.services.ingestion_sources.document_adapters.url_adapter import UrlFetchAdapter, _default_fetch

HTML = b"<html><head><title>Outage</title></head><body><h1>Postmortem</h1><p>1. Roll back the deploy.</p></body></html>"


def _fetch(body, ctype):
    return lambda url: (200, {"Content-Type": ctype}, body, url)


def test_url_adapter_handles_only_bare_http_urls():
    a = UrlFetchAdapter(fetch=_fetch(HTML, "text/html"))
    assert a.can_handle(DocumentLocator(uri="https://blog.example.com/post/"))
    assert not a.can_handle(DocumentLocator(uri="file:///etc/passwd"))
    assert not a.can_handle(DocumentLocator(uri="https://x/y.md", repository="o/r", path="y.md"))   # GitHub wins
    assert not a.can_handle(DocumentLocator(uri="https://x/", raw_bytes=b"x"))


def test_url_adapter_normalizes_html_through_the_html_adapter():
    doc = UrlFetchAdapter(fetch=_fetch(HTML, "text/html; charset=utf-8")).fetch_and_normalize(
        DocumentLocator(uri="https://blog.example.com/post/"))
    assert doc.source_type == "document_web" and "Roll back the deploy" in doc.canonical_markdown


def test_url_adapter_refuses_internal_addresses_before_any_request():
    for url in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/", "http://10.0.0.5/x"):
        with pytest.raises(Exception):
            _default_fetch(url)


def test_url_documents_queue_with_the_url_as_identity():
    from app.ingestion import enqueue as e

    class Pool:
        calls = []

        async def fetchrow(self, sql, *a):
            self.calls.append(a)
            return {"id": 1}

    res = asyncio.run(e.enqueue_documents(Pool(), [{"uri": "https://blog.example.com/post/"}]))
    assert res["created"] == 1 and Pool.calls[0][0] == "ingest_document"

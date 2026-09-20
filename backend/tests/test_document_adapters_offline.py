"""
Offline tests for the document-ingestion SourceAdapter layer (Prompt 2):
`app/services/ingestion_sources/canonical.py`,
`app/services/ingestion_sources/document_adapter.py`,
`app/services/ingestion_sources/document_adapters/*`, and the
orchestration in `app/services/document_ingestion.py`. No network, no
real DB -- DB writes are proven against a hand-rolled fake pool, same
idiom as `test_artifact_blocks_offline.py`.

Four halves:
  1. Per-format adapter fidelity: Markdown, SKILL.md (structured fields
     preserved, existing parser reused), HTML (headings/code/tables/
     links survive), PDF (page provenance), DOCX (headings/tables).
  2. Malformed input -> partial CanonicalDocument + quarantine warning,
     never a silent drop / never a crash.
  3. `document_ingestion.ingest_canonical_document`: idempotent re-ingest,
     a changed content_hash producing a new version, and blocks landing
     in the existing `artifact_blocks` writer.
  4. Non-regression: `SkillMarkdownAdapter` uses the SAME
     `parse_skill_md` the existing SKILL.md pipeline uses, so its output
     for a real fixture matches `parse_skill_md` called directly.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json

import pytest

pytest.importorskip("docx", reason="python-docx is not installed in this environment")

from app.services.artifact_blocks import normalize_markdown
from app.services.ingestion_sources.canonical import CanonicalDocument
from app.services.ingestion_sources.document_adapter import DocumentLocator
from app.services.ingestion_sources.document_adapters import (
    ApiDocumentAdapter,
    DocxAdapter,
    HtmlAdapter,
    MarkdownAdapter,
    PdfAdapter,
    SkillMarkdownAdapter,
    select_adapter,
)
from app.services.ingestion_sources.document_adapters.github_adapter import (
    GitHubFileAdapter,
    GitHubRepoDocsAdapter,
)
from app.services.skill_ingestion import parse_skill_md


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

MARKDOWN_DOC = """# Getting Started

See the [reference docs](https://example.com/ref) for details.

## Install

```bash
pip install stealthlab
```

## Options

| Flag | Meaning |
| --- | --- |
| -v | verbose |
| -q | quiet |
"""

SKILL_MD_DOC = """---
name: rotate-secrets
description: Rotate a leaked credential safely.
---

## Prerequisites
- admin access to the secret store

## Steps
1. Revoke the old credential.
2. Issue a new one.
3. Update every consumer.

## Failure Modes
- a consumer is missed and breaks silently

## Expected Outcome
The old credential no longer works and nothing broke.
"""

HTML_DOC = """<html><head><title>Runbook</title></head><body>
<h1>Deploy</h1>
<p>Read the <a href="https://example.com/checklist">checklist</a> first.</p>
<h2>Commands</h2>
<pre><code class="language-bash">make deploy</code></pre>
<h2>Rollback matrix</h2>
<table><tr><th>Env</th><th>Command</th></tr>
<tr><td>prod</td><td>make rollback-prod</td></tr>
<tr><td>staging</td><td>make rollback-staging</td></tr></table>
</body></html>"""


def _minimal_pdf(pages_text: list[str]) -> bytes:
    """Hand-built minimal single/multi-page PDF -- no writer dependency
    needed since only `pdfplumber` (a read-only, already-vendored dep)
    is used for the adapter itself."""
    content_objs = []
    n = len(pages_text)
    page_nums = list(range(3, 3 + n))
    stream_nums = list(range(3 + n, 3 + 2 * n))
    font_num = 3 + 2 * n
    objs: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_nums)}] /Count {n} >>",
    }
    for i, text in enumerate(pages_text):
        objs[page_nums[i]] = (
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_num} 0 R >> >> "
            f"/MediaBox [0 0 200 200] /Contents {stream_nums[i]} 0 R >>"
        )
        stream = f"BT /F1 12 Tf 10 150 Td ({text}) Tj ET"
        objs[stream_nums[i]] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    objs[font_num] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n{objs[num]}\nendobj\n".encode())
    xref_pos = out.tell()
    total = max(objs) + 1
    out.write(f"xref\n0 {total}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for num in range(1, total):
        out.write(f"{offsets[num]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())
    return out.getvalue()


def _minimal_docx() -> bytes:
    import docx

    document = docx.Document()
    document.add_heading("Ops Report", level=1)
    document.add_paragraph("Weekly summary.")
    document.add_heading("Incidents", level=2)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Severity"
    table.cell(0, 1).text = "Count"
    table.cell(1, 0).text = "SEV1"
    table.cell(1, 1).text = "0"
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------
# 1. Per-format fidelity
# ---------------------------------------------------------------------


def test_markdown_adapter_preserves_headings_code_table_and_links():
    locator = DocumentLocator(raw_bytes=MARKDOWN_DOC.encode(), filename="guide.md", uri="file://guide.md")
    adapter = MarkdownAdapter()
    assert adapter.can_handle(locator)
    doc = adapter.fetch_and_normalize(locator)

    assert isinstance(doc, CanonicalDocument)
    assert doc.title == "Getting Started"
    assert [s.heading for s in doc.sections] == ["Getting Started", "Install", "Options"]
    assert len(doc.code_blocks) == 1
    assert doc.code_blocks[0].language == "bash"
    assert "pip install stealthlab" in doc.code_blocks[0].text
    assert doc.tables[0].headers == ("Flag", "Meaning")
    assert doc.tables[0].rows == (("-v", "verbose"), ("-q", "quiet"))
    assert doc.links[0].url == "https://example.com/ref"
    assert doc.content_hash  # computed, non-empty
    assert not doc.extraction_warnings


def test_markdown_normalize_markdown_output_matches_source_via_blocks():
    """Losslessness check: every code block / table survives structurally
    when the SAME normalizer the artifact pipeline uses re-splits the
    adapter's own `canonical_markdown`."""
    locator = DocumentLocator(raw_bytes=MARKDOWN_DOC.encode(), filename="guide.md", uri="file://guide.md")
    doc = MarkdownAdapter().fetch_and_normalize(locator)
    blocks = normalize_markdown(doc.canonical_markdown)
    assert any(b.block_type == "code_block" for b in blocks)
    assert any(b.block_type == "table" for b in blocks)
    assert any(b.block_type == "heading" and b.text == "Install" for b in blocks)


def test_skill_markdown_adapter_is_not_matched_by_markdown_adapter():
    locator = DocumentLocator(raw_bytes=SKILL_MD_DOC.encode(), filename="SKILL.md", uri="file://SKILL.md")
    assert SkillMarkdownAdapter().can_handle(locator)
    assert not MarkdownAdapter().can_handle(locator)
    assert select_adapter(locator).source_type == "document_skill_markdown"


def test_skill_markdown_adapter_preserves_structured_fields():
    locator = DocumentLocator(raw_bytes=SKILL_MD_DOC.encode(), filename="SKILL.md", uri="file://SKILL.md")
    doc = SkillMarkdownAdapter().fetch_and_normalize(locator)
    assert doc.title == "rotate-secrets"
    assert doc.structured["prerequisites"] == ["admin access to the secret store"]
    assert doc.structured["steps"] == [
        "Revoke the old credential.", "Issue a new one.", "Update every consumer.",
    ]
    assert doc.structured["failure_modes"] == ["a consumer is missed and breaks silently"]
    assert doc.structured["expected_outcome"] == "The old credential no longer works and nothing broke."
    # canonical_markdown is the untouched original text -- lossless.
    assert doc.canonical_markdown == SKILL_MD_DOC


def test_skill_markdown_adapter_matches_parse_skill_md_directly():
    """Non-regression: the adapter must not re-implement or drift from
    the existing SKILL.md parser."""
    locator = DocumentLocator(raw_bytes=SKILL_MD_DOC.encode(), filename="SKILL.md", uri="file://SKILL.md")
    doc = SkillMarkdownAdapter().fetch_and_normalize(locator)
    direct = parse_skill_md(SKILL_MD_DOC)
    assert doc.structured["steps"] == direct.steps
    assert doc.structured["prerequisites"] == direct.prerequisites
    assert doc.structured["failure_modes"] == direct.failure_modes


def test_html_adapter_preserves_headings_code_tables_links():
    locator = DocumentLocator(raw_bytes=HTML_DOC.encode(), filename="runbook.html", uri="https://example.com/runbook.html")
    doc = HtmlAdapter().fetch_and_normalize(locator)
    assert doc.title == "Runbook"
    assert [s.heading for s in doc.sections] == ["Deploy", "Commands", "Rollback matrix"]
    assert doc.sections[1].location.dom_path == "h1:Deploy>h2:Commands"
    assert doc.code_blocks[0].language == "bash"
    assert doc.code_blocks[0].text == "make deploy"
    assert doc.tables[0].headers == ("Env", "Command")
    assert doc.tables[0].rows == (("prod", "make rollback-prod"), ("staging", "make rollback-staging"))
    assert doc.links[0].url == "https://example.com/checklist"
    assert not doc.extraction_warnings


def test_pdf_adapter_preserves_page_provenance():
    pdf_bytes = _minimal_pdf(["First page body", "Second page body"])
    locator = DocumentLocator(raw_bytes=pdf_bytes, filename="doc.pdf", uri="file://doc.pdf")
    assert PdfAdapter().can_handle(locator)
    doc = PdfAdapter().fetch_and_normalize(locator)
    assert [s.location.page for s in doc.sections] == [1, 2]
    assert "First page body" in doc.sections[0].text
    assert "page:1" in doc.canonical_markdown
    assert "page:2" in doc.canonical_markdown
    assert not doc.extraction_warnings


def test_docx_adapter_preserves_headings_and_tables():
    locator = DocumentLocator(raw_bytes=_minimal_docx(), filename="ops.docx", uri="file://ops.docx")
    assert DocxAdapter().can_handle(locator)
    doc = DocxAdapter().fetch_and_normalize(locator)
    assert doc.title == "Ops Report"
    assert [s.heading for s in doc.sections] == ["Ops Report", "Incidents"]
    assert doc.tables[0].headers == ("Severity", "Count")
    assert doc.tables[0].rows == (("SEV1", "0"),)


def test_api_document_adapter_preserves_endpoint_and_response_hash():
    locator = DocumentLocator(
        raw_bytes=b'{"status": "ok", "id": 42}',
        uri="https://api.example.com/v1/widgets/42",
        content_type_hint="api",
    )
    doc = ApiDocumentAdapter().fetch_and_normalize(locator)
    assert doc.metadata["endpoint"] == "https://api.example.com/v1/widgets/42"
    assert doc.metadata["response_hash"]
    assert '"status": "ok"' in doc.canonical_markdown


# ---------------------------------------------------------------------
# 2. Malformed sources -> partial document + warnings, never a crash
# ---------------------------------------------------------------------


def test_malformed_pdf_is_quarantined_not_dropped():
    locator = DocumentLocator(raw_bytes=b"not actually a pdf", filename="bad.pdf", uri="file://bad.pdf")
    doc = PdfAdapter().fetch_and_normalize(locator)
    assert doc.quarantined()
    assert any(w.code.startswith("quarantine:") for w in doc.extraction_warnings)
    assert doc.canonical_markdown == ""  # honestly empty, not fabricated


def test_malformed_docx_is_quarantined_not_dropped():
    locator = DocumentLocator(raw_bytes=b"not actually a docx", filename="bad.docx", uri="file://bad.docx")
    doc = DocxAdapter().fetch_and_normalize(locator)
    assert doc.quarantined()


def test_non_utf8_markdown_is_lossy_decoded_with_a_warning():
    locator = DocumentLocator(raw_bytes=b"\xff\xfe# title\nbroken bytes here", filename="x.md", uri="file://x.md")
    doc = MarkdownAdapter().fetch_and_normalize(locator)
    assert any(w.code == "quarantine:decode_error" for w in doc.extraction_warnings)
    assert doc.canonical_markdown  # still produced a document, not dropped


def test_non_json_api_body_is_preserved_as_text_with_a_warning():
    locator = DocumentLocator(raw_bytes=b"<html>not json</html>", uri="https://api.example.com/x", content_type_hint="api")
    doc = ApiDocumentAdapter().fetch_and_normalize(locator)
    assert any(w.code == "warning:non_json_body" for w in doc.extraction_warnings)
    assert "not json" in doc.canonical_markdown


# ---------------------------------------------------------------------
# 3. document_ingestion orchestration: idempotency + versioning + blocks
# ---------------------------------------------------------------------


class _FakeConn:
    def __init__(self, db: "_FakeDb"):
        self._db = db

    class _TxnCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def transaction(self):
        return _FakeConn._TxnCM(self)

    async def execute(self, sql, *args):
        return await self._db._execute(sql, args)

    async def fetchrow(self, sql, *args):
        return await self._db._fetchrow(sql, args)


class _FakeDb:
    """Enough of an asyncpg.Pool to drive `document_ingestion` end to
    end: `ingested_artifacts` + `sources` as in-memory tables, routed by
    SQL substring -- same fake-by-substring idiom as
    `test_skill_ingestion_offline.py`'s own pool fakes."""

    def __init__(self):
        self.ingested_artifacts: list[dict] = []
        self.sources: list[dict] = []
        self.block_inserts: list[tuple] = []
        self._next_id = 1

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}-{self._next_id}"

    class _AcquireCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return _FakeDb._AcquireCM(_FakeConn(self))

    async def fetch(self, sql, *args):
        if "SELECT id FROM ingested_artifacts" in sql:
            source_type, uri, content_hash, extractor_version = args
            return [
                row for row in self.ingested_artifacts
                if row["source_type"] == source_type and row["uri"] == uri
                and row["content_hash"] == content_hash
                and row["extractor_version"] == extractor_version
                and not row.get("t_invalid")
            ]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def execute(self, sql, *args):
        return await self._execute(sql, args)

    async def fetchrow(self, sql, *args):
        return await self._fetchrow(sql, args)

    async def _execute(self, sql, args):
        if "set_config" in sql:
            return "SELECT 1"
        if "UPDATE ingested_artifacts SET last_seen" in sql:
            (row_id,) = args
            for row in self.ingested_artifacts:
                if row["id"] == row_id:
                    row["last_seen_bumped"] = row.get("last_seen_bumped", 0) + 1
            return "UPDATE 1"
        if "INSERT INTO artifact_blocks" in sql:
            self.block_inserts.append(args)
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {sql}")

    async def _fetchrow(self, sql, args):
        if "INSERT INTO ingested_artifacts" in sql:
            source_type, uri, repository, path, commit, content_hash, extractor_version = args[:7]
            row_id = self._new_id("artifact")
            self.ingested_artifacts.append({
                "id": row_id, "source_type": source_type, "uri": uri,
                "repository": repository, "path": path, "commit": commit,
                "content_hash": content_hash, "extractor_version": extractor_version,
            })
            return {"id": row_id}
        if "INSERT INTO sources" in sql:
            row_id = self._new_id("source")
            self.sources.append({"id": row_id})
            return {"id": row_id, "inserted": True}
        raise AssertionError(f"unexpected fetchrow: {sql}")


def _make_doc(content_hash_content: str, *, uri: str = "file://guide.md") -> CanonicalDocument:
    locator = DocumentLocator(raw_bytes=content_hash_content.encode(), filename="guide.md", uri=uri)
    return MarkdownAdapter().fetch_and_normalize(locator)


def test_ingest_writes_artifact_row_source_row_and_blocks():
    from app.services.document_ingestion import ingest_canonical_document

    db = _FakeDb()
    doc = _make_doc(MARKDOWN_DOC)
    outcome = _run(ingest_canonical_document(db, doc, created_by="tester"))

    assert outcome.status == "ingested"
    assert outcome.artifact_id is not None
    assert outcome.source_ref is not None
    assert len(db.ingested_artifacts) == 1
    assert db.ingested_artifacts[0]["content_hash"] == doc.content_hash
    assert len(db.sources) == 1
    assert db.block_inserts  # blocks were persisted
    assert len(outcome.block_ids) == len(normalize_markdown(doc.canonical_markdown))


def test_ingest_is_idempotent_for_byte_identical_content():
    from app.services.document_ingestion import ingest_canonical_document

    db = _FakeDb()
    doc = _make_doc(MARKDOWN_DOC)
    first = _run(ingest_canonical_document(db, doc, created_by="tester"))
    second = _run(ingest_canonical_document(db, doc, created_by="tester"))

    assert first.status == "ingested"
    assert second.status == "unchanged"
    assert second.artifact_id == first.artifact_id
    assert len(db.ingested_artifacts) == 1  # no duplicate row
    assert db.ingested_artifacts[0]["last_seen_bumped"] == 1


def test_changed_content_creates_a_new_version_not_an_overwrite():
    from app.services.document_ingestion import ingest_canonical_document

    db = _FakeDb()
    v1 = _make_doc(MARKDOWN_DOC)
    v2 = _make_doc(MARKDOWN_DOC + "\n## Changelog\n\nv2 adds this section.\n")
    assert v1.content_hash != v2.content_hash

    out1 = _run(ingest_canonical_document(db, v1, created_by="tester"))
    out2 = _run(ingest_canonical_document(db, v2, created_by="tester"))

    assert out1.status == "ingested"
    assert out2.status == "ingested"
    assert out1.artifact_id != out2.artifact_id
    assert len(db.ingested_artifacts) == 2  # both versions retained


# ---------------------------------------------------------------------
# GitHub file / repo-docs adapters: commit + path provenance
# ---------------------------------------------------------------------


class _FakeGitHubDocs:
    """Same fake-by-URL-shape idiom as
    `test_skill_corpus_wave1_offline.py`'s `FakeGitHub`, scoped to the
    three endpoints `GitHubFileAdapter`/`GitHubRepoDocsAdapter` call:
    commit resolution, recursive tree listing, and raw file fetch."""

    commit = "c" * 40

    def __init__(self):
        self.files = {
            "README.md": b"# Hello\n\nSome docs.\n",
            "docs/guide.md": b"# Guide\n\nStep by step.\n",
            "src/main.py": b"print('not a doc')\n",
        }

    def __call__(self, url: str) -> tuple[int, bytes]:
        if "/commits/" in url:
            return 200, json.dumps({"sha": self.commit}).encode()
        if "/git/trees/" in url:
            tree = [{"type": "blob", "path": path} for path in self.files]
            return 200, json.dumps({"sha": self.commit, "truncated": False, "tree": tree}).encode()
        marker = f"/{self.commit}/"
        if marker in url:
            path = url.split(marker, 1)[1]
            if path in self.files:
                return 200, self.files[path]
        return 404, b""


def test_github_file_adapter_fetches_at_resolved_commit_with_provenance():
    fake = _FakeGitHubDocs()
    adapter = GitHubFileAdapter(http_get=fake)
    locator = DocumentLocator(repository="acme/widgets", path="docs/guide.md")
    assert adapter.can_handle(locator)

    raw = adapter.fetch(locator)
    assert raw.repository == "acme/widgets"
    assert raw.path == "docs/guide.md"
    assert raw.commit == _FakeGitHubDocs.commit
    assert raw.uri == f"https://github.com/acme/widgets/blob/{_FakeGitHubDocs.commit}/docs/guide.md"

    doc = adapter.normalize(raw)
    assert doc.source_type == "document_github_file"
    assert doc.repository == "acme/widgets"
    assert doc.path == "docs/guide.md"
    assert doc.version == _FakeGitHubDocs.commit
    assert doc.title == "Guide"


def test_github_file_adapter_rejects_path_traversal():
    fake = _FakeGitHubDocs()
    adapter = GitHubFileAdapter(http_get=fake)
    locator = DocumentLocator(repository="acme/widgets", path="../../etc/passwd.md")
    with pytest.raises(Exception):
        adapter.fetch(locator)


def test_github_repo_docs_adapter_discovers_only_doc_files_at_one_commit():
    fake = _FakeGitHubDocs()
    adapter = GitHubRepoDocsAdapter(http_get=fake)
    locators = list(adapter.discover("acme/widgets"))
    paths = sorted(loc.path for loc in locators)
    assert paths == ["README.md", "docs/guide.md"]  # src/main.py excluded, not a doc suffix
    assert all(loc.commit == _FakeGitHubDocs.commit for loc in locators)
    assert all(loc.repository == "acme/widgets" for loc in locators)


def test_github_repo_docs_adapter_fetches_and_normalizes_every_discovered_doc():
    fake = _FakeGitHubDocs()
    adapter = GitHubRepoDocsAdapter(http_get=fake)
    docs = list(adapter.fetch_and_normalize_all("acme/widgets"))
    titles = sorted(d.title for d in docs)
    assert titles == ["Guide", "Hello"]
    assert all(d.version == _FakeGitHubDocs.commit for d in docs)


def test_quarantined_document_is_still_ingested_with_status_flagged():
    from app.services.document_ingestion import ingest_canonical_document

    db = _FakeDb()
    locator = DocumentLocator(raw_bytes=b"not actually a pdf", filename="bad.pdf", uri="file://bad.pdf")
    doc = PdfAdapter().fetch_and_normalize(locator)
    outcome = _run(ingest_canonical_document(db, doc, created_by="tester"))

    assert outcome.status == "quarantined"
    assert outcome.artifact_id is not None  # content is never silently dropped
    assert any("quarantine:" in w for w in outcome.warnings)

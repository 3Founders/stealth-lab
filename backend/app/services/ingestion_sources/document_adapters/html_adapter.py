"""HTML adapter: preserves headings/code/tables/links losslessly and
renders a canonical Markdown projection. Uses the stdlib `html.parser`
only -- no new third-party HTML dependency for what is a bounded,
well-specified subset (headings, paragraphs, `pre>code`, `table`, `a`).
"""
from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser

from app.services.ingestion_sources.canonical import (
    CanonicalDocument,
    CodeBlockElement,
    LinkElement,
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

_HEADING_TAGS = {f"h{i}": i for i in range(1, 7)}
_WS_RE = re.compile(r"[ \t\f\v]+")


def _source_id(uri: str) -> str:
    return hashlib.sha256(f"html\0{uri}".encode("utf-8")).hexdigest()[:32]


@dataclass
class _TableState:
    rows: list[list[str]] = field(default_factory=list)
    current_row: list[str] | None = None
    current_cell: list[str] | None = None
    has_header: bool = False


class _DocumentHTMLParser(HTMLParser):
    """Single-pass walk producing markdown fragments plus typed elements,
    in document order. Deliberately linear/stateful rather than building
    a full DOM tree -- the subset of HTML this adapter promises to
    understand (headings/paragraphs/pre-code/tables/links) never needs
    one."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.markdown_parts: list[str] = []
        self.sections: list[SectionElement] = []
        self.code_blocks: list[CodeBlockElement] = []
        self.tables: list[TableElement] = []
        self.links: list[LinkElement] = []
        self.warnings: list[tuple[str, str]] = []
        self.title: str | None = None

        self._position = 0
        self._heading_path: list[str] = []  # e.g. ["h1:Intro", "h2:Setup"]
        self._in_title = False
        self._in_heading: int | None = None
        self._heading_buf: list[str] = []
        self._in_pre = False
        self._in_code = False
        self._code_buf: list[str] = []
        self._code_lang: str | None = None
        self._table_stack: list[_TableState] = []
        self._link_href: str | None = None
        self._link_buf: list[str] = []
        self._para_buf: list[str] = []

    def _next_position(self) -> int:
        self._position += 1
        return self._position

    def _dom_path(self) -> str:
        return ">".join(self._heading_path) if self._heading_path else ""

    def _flush_paragraph(self) -> None:
        text = _WS_RE.sub(" ", "".join(self._para_buf)).strip()
        self._para_buf = []
        if text:
            self.markdown_parts.append(text + "\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = dict(attrs)
        if tag == "title":
            self._in_title = True
            return
        if tag in _HEADING_TAGS:
            self._flush_paragraph()
            self._in_heading = _HEADING_TAGS[tag]
            self._heading_buf = []
            return
        if tag == "pre":
            self._flush_paragraph()
            self._in_pre = True
            self._code_buf = []
            self._code_lang = None
            return
        if tag == "code" and self._in_pre:
            self._in_code = True
            cls = attrs_d.get("class") or ""
            m = re.search(r"language-([\w+.\-]+)", cls)
            if m:
                self._code_lang = m.group(1)
            return
        if tag == "table":
            self._flush_paragraph()
            self._table_stack.append(_TableState())
            return
        if tag == "tr" and self._table_stack:
            self._table_stack[-1].current_row = []
            return
        if tag in ("td", "th") and self._table_stack:
            state = self._table_stack[-1]
            state.current_cell = []
            if tag == "th" and not state.rows:
                state.has_header = True
            return
        if tag == "a":
            self._link_href = attrs_d.get("href")
            self._link_buf = []
            return
        if tag in ("p", "br", "li", "div") and not self._in_pre:
            self._flush_paragraph()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in _HEADING_TAGS:
            text = _WS_RE.sub(" ", "".join(self._heading_buf)).strip()
            level = self._in_heading or _HEADING_TAGS[tag]
            while len(self._heading_path) >= level:
                self._heading_path.pop()
            self._heading_path.append(f"h{level}:{text}")
            pos = self._next_position()
            self.sections.append(
                SectionElement(
                    heading=text, level=level, position=pos, text=text,
                    location=SourceLocation(dom_path=self._dom_path()),
                )
            )
            self.markdown_parts.append(f"{'#' * level} {text}\n")
            self._in_heading = None
            return
        if tag == "code" and self._in_code:
            self._in_code = False
            return
        if tag == "pre" and self._in_pre:
            self._in_pre = False
            text = "".join(self._code_buf)
            pos = self._next_position()
            self.code_blocks.append(
                CodeBlockElement(
                    text=text, position=pos, language=self._code_lang,
                    location=SourceLocation(dom_path=self._dom_path()),
                )
            )
            fence_lang = self._code_lang or ""
            self.markdown_parts.append(f"```{fence_lang}\n{text}\n```\n")
            return
        if tag in ("td", "th") and self._table_stack and self._table_stack[-1].current_cell is not None:
            state = self._table_stack[-1]
            cell_text = _WS_RE.sub(" ", "".join(state.current_cell)).strip()
            state.current_row.append(cell_text)
            state.current_cell = None
            return
        if tag == "tr" and self._table_stack and self._table_stack[-1].current_row is not None:
            state = self._table_stack[-1]
            state.rows.append(state.current_row)
            state.current_row = None
            return
        if tag == "table" and self._table_stack:
            state = self._table_stack.pop()
            if not state.rows:
                return
            pos = self._next_position()
            if state.has_header and len(state.rows) >= 1:
                headers = tuple(state.rows[0])
                rows = tuple(tuple(r) for r in state.rows[1:])
            else:
                headers = ()
                rows = tuple(tuple(r) for r in state.rows)
            self.tables.append(
                TableElement(
                    position=pos, headers=headers, rows=rows,
                    location=SourceLocation(dom_path=self._dom_path()),
                )
            )
            md_rows = []
            if headers:
                md_rows.append("| " + " | ".join(headers) + " |")
                md_rows.append("|" + "|".join(" --- " for _ in headers) + "|")
            for row in (rows if headers else state.rows):
                md_rows.append("| " + " | ".join(row) + " |")
            self.markdown_parts.append("\n".join(md_rows) + "\n")
            return
        if tag == "a" and self._link_href is not None:
            text = _WS_RE.sub(" ", "".join(self._link_buf)).strip()
            pos = self._next_position()
            self.links.append(
                LinkElement(
                    text=text or self._link_href, url=self._link_href, position=pos,
                    location=SourceLocation(dom_path=self._dom_path()),
                )
            )
            self._para_buf.append(f"[{text or self._link_href}]({self._link_href})")
            self._link_href = None
            return
        if tag in ("p", "li"):
            self._flush_paragraph()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data
            return
        if self._in_heading is not None:
            self._heading_buf.append(data)
            return
        if self._in_code:
            self._code_buf.append(data)
            return
        if self._in_pre:
            return
        if self._table_stack and self._table_stack[-1].current_cell is not None:
            self._table_stack[-1].current_cell.append(data)
            return
        if self._link_href is not None:
            self._link_buf.append(data)
            return
        self._para_buf.append(data)


class HtmlAdapter(DocumentSourceAdapter):
    source_type = "document_html"

    def can_handle(self, locator: DocumentLocator) -> bool:
        if locator.content_type_hint in ("html", "text/html"):
            return True
        name = (locator.filename or locator.local_path or locator.uri or "").lower()
        return name.endswith((".html", ".htm"))

    def fetch(self, locator: DocumentLocator) -> RawDocument:
        if locator.raw_bytes is not None:
            data = locator.raw_bytes
        elif locator.local_path:
            with open(locator.local_path, "rb") as fh:
                data = fh.read()
        else:
            raise AdapterNotApplicable("HtmlAdapter.fetch requires local_path or raw_bytes")
        uri = locator.uri or locator.local_path or locator.filename or "unknown.html"
        return RawDocument(
            source_type=self.source_type, uri=uri, content=data, content_type="text/html",
            repository=locator.repository, path=locator.path, commit=locator.commit,
            fetched_at=datetime.now(timezone.utc), metadata=dict(locator.metadata),
        )

    def normalize(self, raw: RawDocument) -> CanonicalDocument:
        warnings: list[tuple[str, str]] = []
        try:
            text = raw.content.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.content.decode("utf-8", errors="replace")
            warnings.append(("quarantine:decode_error", "content was not valid UTF-8; lossy-decoded"))

        parser = _DocumentHTMLParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:  # malformed markup: still emit a partial document
            warnings.append(("quarantine:html_parse_error", f"{type(exc).__name__}: {exc}"))
        parser._flush_paragraph()

        canonical_markdown = "\n".join(html_lib.unescape(p) for p in parser.markdown_parts).strip() + "\n"
        title = (parser.title or "").strip() or (parser.sections[0].heading if parser.sections else raw.path or raw.uri)

        doc = CanonicalDocument(
            source_id=_source_id(raw.uri),
            source_type=self.source_type,
            source_uri=raw.uri,
            title=title,
            canonical_markdown=canonical_markdown,
            sections=tuple(parser.sections),
            code_blocks=tuple(parser.code_blocks),
            tables=tuple(parser.tables),
            links=tuple(parser.links),
            repository=raw.repository,
            path=raw.path,
            version=raw.commit,
            metadata=dict(raw.metadata),
        )
        for code, message in warnings + parser.warnings:
            doc = doc.with_warning(code, message)
        return doc

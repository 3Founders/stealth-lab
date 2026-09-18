"""Shared markdown structural extraction used by `MarkdownAdapter` and
`SkillMarkdownAdapter` -- built ON TOP OF the existing
`app.services.artifact_blocks.normalize_markdown` splitter rather than
re-parsing markdown a second time. This module only adds the two things
that splitter deliberately does not do: turning heading/code/table
`Block`s into typed `SectionElement`/`CodeBlockElement`/`TableElement`
objects, and pulling out `[text](url)` links (which `normalize_markdown`
leaves inside paragraph text).
"""
from __future__ import annotations

import re

from app.services.artifact_blocks import Block, normalize_markdown
from app.services.ingestion_sources.canonical import (
    CodeBlockElement,
    LinkElement,
    SectionElement,
    SourceLocation,
    TableElement,
)

_FENCE_LANG_RE = re.compile(r"^\s{0,3}(?:`{3,}|~{3,})\s*([\w+.\-]*)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.*)\|\s*$")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def _line_of(content: str, offset: int) -> int:
    """1-based line number for a character offset -- used as the
    line-range provenance for a generic (non-GitHub) markdown source."""
    return content.count("\n", 0, offset) + 1


def _split_table_row(row: str) -> tuple[str, ...]:
    m = _TABLE_ROW_RE.match(row)
    body = m.group(1) if m else row.strip("|")
    return tuple(cell.strip() for cell in body.split("|"))


def _table_from_block(block: Block, content: str) -> TableElement | None:
    lines = [ln for ln in block.text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    header = _split_table_row(lines[0])
    sep_cells = _split_table_row(lines[1])
    if not all(_TABLE_SEP_CELL_RE.match(c) for c in sep_cells if c):
        # Not a real header/separator table -- still preserve as a table,
        # just without a confirmed header row.
        rows = tuple(_split_table_row(ln) for ln in lines)
        return TableElement(
            position=block.block_index, headers=(), rows=rows,
            location=SourceLocation(line_start=_line_of(content, block.source_start),
                                     line_end=_line_of(content, block.source_end)),
        )
    rows = tuple(_split_table_row(ln) for ln in lines[2:])
    return TableElement(
        position=block.block_index, headers=header, rows=rows,
        location=SourceLocation(line_start=_line_of(content, block.source_start),
                                 line_end=_line_of(content, block.source_end)),
    )


def elements_from_markdown(
    content: str,
) -> tuple[tuple[SectionElement, ...], tuple[CodeBlockElement, ...], tuple[TableElement, ...], tuple[LinkElement, ...]]:
    """Derive sections/code_blocks/tables/links from markdown text using
    the shared structural splitter. Line numbers are 1-based, inclusive."""
    blocks = normalize_markdown(content)
    sections: list[SectionElement] = []
    code_blocks: list[CodeBlockElement] = []
    tables: list[TableElement] = []
    links: list[LinkElement] = []

    for block in blocks:
        loc = SourceLocation(
            line_start=_line_of(content, block.source_start),
            line_end=_line_of(content, block.source_end),
        )
        if block.block_type == "heading":
            sections.append(
                SectionElement(
                    heading=block.text, level=block.depth,
                    position=block.block_index, text=block.text, location=loc,
                )
            )
        elif block.block_type == "code_block":
            lang_match = _FENCE_LANG_RE.match(block.text.splitlines()[0]) if block.text else None
            language = (lang_match.group(1) or None) if lang_match else None
            inner_lines = block.text.splitlines()[1:-1] if block.text.count("\n") >= 1 else []
            code_blocks.append(
                CodeBlockElement(
                    text="\n".join(inner_lines), position=block.block_index,
                    language=language, location=loc,
                )
            )
        elif block.block_type == "table":
            table = _table_from_block(block, content)
            if table is not None:
                tables.append(table)
        elif block.block_type in ("paragraph", "ordered_list_item", "unordered_list_item"):
            for match in _LINK_RE.finditer(block.text):
                links.append(
                    LinkElement(
                        text=match.group(1), url=match.group(2),
                        position=block.block_index, location=loc,
                    )
                )

    return tuple(sections), tuple(code_blocks), tuple(tables), tuple(links)

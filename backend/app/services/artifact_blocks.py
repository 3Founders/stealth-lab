"""
Immutable, addressable Artifact blocks (V4-hardening Part II-A §3-§4 /
Gate G2 / audit B16).

WHY THIS EXISTS
    `ingested_artifacts` (migrations 32/39) stores an artifact's
    provenance but not its normalized structure, and
    `skill_ingestion.py::parse_skill_md` splits a document into sections
    while keeping NO offsets. So a Claim or Observation derived from an
    artifact cannot be cited back to the exact characters that justify it.

    V4-hardening §4: "Every normalized block MUST retain source location
    information ... This is what makes later claims auditable back to the
    source." This module cuts a document into ordered blocks, each
    carrying `source_start` / `source_end` back into the raw content, and
    persists them into `artifact_blocks` (migration 69).

HONEST LIMIT
    `normalize_markdown` / `normalize_text` are DETERMINISTIC STRUCTURAL
    splitters, not semantic parsers. They classify SHAPE -- heading,
    paragraph, list item, code block, table, frontmatter -- and nothing
    else. They do not interpret meaning, do not resolve links, do not
    classify a section as "steps" vs "limitations" (that stays
    `skill_ingestion.py`'s job), and never fabricate a block that is not
    literally present in the source. Given the same input string they
    return byte-identical output every time.

    Offsets are CHARACTER offsets into the exact `str` passed in (Python
    string indices), half-open, such that
    ``content[block.source_start:block.source_end]`` round-trips to the
    block's raw span with the trailing newline excluded. They are only
    meaningful against the exact bytes whose sha256 is the
    `artifact_content_hash` the blocks are persisted under.

ANCHOR FORMAT
    A heading block gets ``anchor = f"h{depth}-{slug}"`` where ``slug`` is
    the lowercased heading text with every run of non-alphanumerics
    collapsed to ``-`` and stripped from the ends -- e.g. ``## Foo Bar``
    -> ``h2-foo-bar``. Non-heading blocks have ``anchor = None``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Optional

from app.services.access import TenantScope, tenant_transaction
from app.utils.ids import uuid7

NORMALIZER_VERSION = "block_normalizer@v1"

# schema.md / migration 69 vocabulary. Widenable there and here together.
BLOCK_TYPES: tuple[str, ...] = (
    "heading",
    "paragraph",
    "ordered_list_item",
    "unordered_list_item",
    "code_block",
    "table",
    "frontmatter",
    "other",
)


@dataclass(frozen=True)
class Block:
    """One normalized structural block of an artifact.

    ``parent_index`` is the in-list index (== ``block_index``) of the
    block this one nests under -- the most recent heading that precedes
    it, or ``None`` at top level. ``persist_artifact_blocks`` resolves it
    to a real ``parent_block_id`` at write time.
    """

    block_index: int
    block_type: str
    depth: int
    text: str
    source_start: int
    source_end: int
    anchor: Optional[str]
    parent_index: Optional[int]


def redact_blocks_for_persistence(blocks: list[Block]) -> tuple[list[Block], list[str]]:
    """Redact detected secrets from derived block text, never from source.

    ``source_start``/``source_end`` still cite the exact immutable Artifact;
    the persisted text is deliberately a safe projection and must not be
    used to reconstruct raw source bytes.
    """
    from app.services.screening import redact_document_text

    matched: list[str] = []
    safe: list[Block] = []
    for block in blocks:
        text, findings = redact_document_text(block.text)
        matched.extend(findings)
        safe.append(replace(block, text=text))
    return safe, sorted(set(matched))


# --------------------------------------------------------------------------
# Pure, deterministic normalizers
# --------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_OPEN_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*$")
_LIST_ITEM_RE = re.compile(r"^(\s{0,3})(\d+[.)]|[-*+])\s+(.*)$")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, *, maxlen: int = 80) -> str:
    s = _NON_SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:maxlen].rstrip("-") or "section"


def _line_spans(content: str) -> list[tuple[int, int]]:
    """(start, end) char spans for each physical line, EXCLUDING the
    trailing newline. A trailing newline at end-of-string yields no empty
    final line."""
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(content)
    while i < n:
        j = content.find("\n", i)
        if j == -1:
            spans.append((i, n))
            break
        spans.append((i, j))
        i = j + 1
    return spans


def normalize_markdown(content: str) -> list[Block]:
    """Deterministic structural split of a markdown document.

    Recognizes, in priority order per line: a leading YAML frontmatter
    fence (``---`` ... ``---``) as one ``frontmatter`` block; fenced code
    (``` / ~~~) as one ``code_block`` (interior never split); ATX
    headings (``#``..``######``) with ``depth`` = hash count and a slug
    ``anchor``; numbered list items (``ordered_list_item``); ``-``/``*``/
    ``+`` list items (``unordered_list_item``); contiguous ``|...`` rows
    as one ``table``; every other contiguous non-blank run as a
    ``paragraph``. Blank lines are not blocks.

    Every block's ``source_start`` / ``source_end`` are char offsets into
    ``content`` with ``content[start:end]`` equal to the block's raw span
    (trailing newline excluded). ``parent_index`` is the most recent
    heading block that precedes the block, or ``None``.
    """
    if not content:
        return []

    lines = _line_spans(content)
    n = len(lines)
    blocks: list[Block] = []
    # heading stack of (depth, block_index) for heading->heading nesting
    heading_stack: list[tuple[int, int]] = []
    most_recent_heading: Optional[int] = None

    def _text(k: int) -> str:
        s, e = lines[k]
        return content[s:e]

    def _add(
        block_type: str,
        depth: int,
        text: str,
        start: int,
        end: int,
        anchor: Optional[str],
        parent_index: Optional[int],
    ) -> int:
        idx = len(blocks)
        blocks.append(
            Block(
                block_index=idx,
                block_type=block_type,
                depth=depth,
                text=text,
                source_start=start,
                source_end=end,
                anchor=anchor,
                parent_index=parent_index,
            )
        )
        return idx

    i = 0

    # ---- leading YAML frontmatter -------------------------------------
    if n >= 2 and _text(0) == "---":
        close = None
        for k in range(1, n):
            t = _text(k).strip()
            if t == "---" or t == "...":
                close = k
                break
        if close is not None:
            _add(
                "frontmatter",
                0,
                content[lines[0][0] : lines[close][1]],
                lines[0][0],
                lines[close][1],
                None,
                None,
            )
            i = close + 1

    # ---- body -------------------------------------------------------------
    while i < n:
        raw = _text(i)
        stripped = raw.strip()

        if not stripped:
            i += 1
            continue

        # fenced code block -- interior is never split
        fm = _FENCE_OPEN_RE.match(raw)
        if fm:
            marker = fm.group(1)
            fence_char = marker[0]
            fence_len = len(marker)
            start_off = lines[i][0]
            j = i + 1
            while j < n:
                cm = _FENCE_CLOSE_RE.match(_text(j))
                if (
                    cm
                    and cm.group(1)[0] == fence_char
                    and len(cm.group(1)) >= fence_len
                ):
                    j += 1
                    break
                j += 1
            end_off = lines[j - 1][1]
            _add(
                "code_block",
                0,
                content[start_off:end_off],
                start_off,
                end_off,
                None,
                most_recent_heading,
            )
            i = j
            continue

        # ATX heading
        hm = _HEADING_RE.match(raw)
        if hm:
            depth = len(hm.group(1))
            htext = hm.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= depth:
                heading_stack.pop()
            parent = heading_stack[-1][1] if heading_stack else None
            idx = _add(
                "heading",
                depth,
                htext,
                lines[i][0],
                lines[i][1],
                f"h{depth}-{_slug(htext)}",
                parent,
            )
            heading_stack.append((depth, idx))
            most_recent_heading = idx
            i += 1
            continue

        # markdown table -- contiguous '|...' rows
        if stripped.startswith("|"):
            start_off = lines[i][0]
            j = i
            while j < n and _text(j).strip().startswith("|"):
                j += 1
            end_off = lines[j - 1][1]
            _add(
                "table",
                0,
                content[start_off:end_off],
                start_off,
                end_off,
                None,
                most_recent_heading,
            )
            i = j
            continue

        # list item (+ non-marker continuation lines)
        lm = _LIST_ITEM_RE.match(raw)
        if lm:
            ordered = bool(re.match(r"\d", lm.group(2)))
            block_type = "ordered_list_item" if ordered else "unordered_list_item"
            start_off = lines[i][0]
            j = i + 1
            while j < n:
                r2 = _text(j)
                s2 = r2.strip()
                if not s2:
                    break
                if _LIST_ITEM_RE.match(r2) or _HEADING_RE.match(r2):
                    break
                if _FENCE_OPEN_RE.match(r2) or s2.startswith("|"):
                    break
                j += 1
            end_off = lines[j - 1][1]
            _add(
                block_type,
                0,
                content[start_off:end_off],
                start_off,
                end_off,
                None,
                most_recent_heading,
            )
            i = j
            continue

        # paragraph -- contiguous non-blank run that is none of the above
        start_off = lines[i][0]
        j = i + 1
        while j < n:
            r2 = _text(j)
            s2 = r2.strip()
            if not s2:
                break
            if (
                _HEADING_RE.match(r2)
                or _FENCE_OPEN_RE.match(r2)
                or _LIST_ITEM_RE.match(r2)
                or s2.startswith("|")
            ):
                break
            j += 1
        end_off = lines[j - 1][1]
        _add(
            "paragraph",
            0,
            content[start_off:end_off],
            start_off,
            end_off,
            None,
            most_recent_heading,
        )
        i = j

    return blocks


def normalize_text(content: str) -> list[Block]:
    """Trivial fallback for non-markdown documents: paragraph-split on
    blank lines, offsets preserved, every block ``block_type='paragraph'``
    with ``depth=0`` and no parent. Deterministic."""
    if not content:
        return []
    lines = _line_spans(content)
    n = len(lines)
    blocks: list[Block] = []
    i = 0
    while i < n:
        s, e = lines[i]
        if not content[s:e].strip():
            i += 1
            continue
        start_off = lines[i][0]
        j = i
        while j < n:
            ls, le = lines[j]
            if not content[ls:le].strip():
                break
            j += 1
        end_off = lines[j - 1][1]
        blocks.append(
            Block(
                block_index=len(blocks),
                block_type="paragraph",
                depth=0,
                text=content[start_off:end_off],
                source_start=start_off,
                source_end=end_off,
                anchor=None,
                parent_index=None,
            )
        )
        i = j
    return blocks


# --------------------------------------------------------------------------
# Writer + read paths
# --------------------------------------------------------------------------

_INSERT_SQL = """
INSERT INTO artifact_blocks (
    id, artifact_id, artifact_content_hash, block_index, parent_block_id,
    block_type, depth, text, source_start, source_end, anchor,
    created_by, visibility, owner_id, scope_type, scope_entity_id,
    ingestion_context_id
) VALUES (
    $1::uuid, $2::uuid, $3, $4, $5::uuid,
    $6, $7, $8, $9, $10, $11,
    $12, $13::visibility_level, $14, $15, $16,
    $17::uuid
)
ON CONFLICT (artifact_id, artifact_content_hash, block_index) DO NOTHING
"""

_SELECT_COLUMNS = """
    id, artifact_id, artifact_content_hash, block_index, parent_block_id,
    block_type, depth, text, source_start, source_end, anchor,
    created_by, visibility, owner_id, scope_type, scope_entity_id,
    ingestion_context_id, t_valid, t_invalid, t_created
"""

_GET_BLOCKS_SQL = f"""
SELECT {_SELECT_COLUMNS}
FROM artifact_blocks
WHERE artifact_id = $1::uuid AND t_invalid IS NULL
ORDER BY block_index ASC
"""

_GET_SPAN_SQL = f"""
SELECT {_SELECT_COLUMNS}
FROM artifact_blocks
WHERE id = $1::uuid AND t_invalid IS NULL
"""


async def persist_artifact_blocks(
    pool: Any,
    *,
    artifact_id: str,
    artifact_content_hash: str,
    blocks: list[Block],
    created_by: Optional[str],
    ingestion_context_id: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
) -> list[str]:
    """Write one ``INSERT INTO artifact_blocks`` per block inside ONE
    ``tenant_transaction`` (tenant bound as the first statement). Resolves
    each block's ``parent_index`` to the ``id`` of the parent block
    already inserted in this call. ``ON CONFLICT ... DO NOTHING`` makes a
    re-ingest of byte-identical content a no-op. Returns the list of new
    block ids in ``block_index`` order. ``blocks == []`` -> no statements,
    returns ``[]``.
    """
    if not blocks:
        return []

    ordered = sorted(blocks, key=lambda b: b.block_index)
    max_index = ordered[-1].block_index
    ids_by_index: list[Optional[str]] = [None] * (max_index + 1)
    written: list[str] = []

    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        for b in ordered:
            block_id = str(uuid7())
            parent_id: Optional[str] = None
            if b.parent_index is not None and 0 <= b.parent_index <= max_index:
                parent_id = ids_by_index[b.parent_index]
            await conn.execute(
                _INSERT_SQL,
                block_id,
                str(artifact_id),
                artifact_content_hash,
                b.block_index,
                parent_id,
                b.block_type,
                b.depth,
                b.text,
                b.source_start,
                b.source_end,
                b.anchor,
                created_by,
                visibility,
                owner_id,
                scope_type,
                scope_entity_id,
                ingestion_context_id,
            )
            ids_by_index[b.block_index] = block_id
            written.append(block_id)

    return written


async def get_artifact_blocks(pool: Any, artifact_id: str) -> list[dict]:
    """Every live block of an artifact, ordered by ``block_index``."""
    rows = await pool.fetch(_GET_BLOCKS_SQL, str(artifact_id))
    return [dict(r) for r in rows]


async def get_block_span(pool: Any, block_id: str) -> Optional[dict]:
    """One live block by id -- the "cite this claim back to source" read.
    ``None`` when the block does not exist or is tombstoned."""
    row = await pool.fetchrow(_GET_SPAN_SQL, str(block_id))
    return dict(row) if row is not None else None

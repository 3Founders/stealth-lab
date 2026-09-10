"""
DB-free coverage for app/services/artifact_blocks.py
(V4-hardening Part II-A §3-§4 / Gate G2 / audit B16).

Three halves:

1.  `normalize_markdown` / `normalize_text` -- PURE functions: block
    count / types / depth, exact offset round-trip
    (`content[b.source_start:b.source_end]` is the block's raw span),
    heading nesting via `parent_index`, code-block interior not split,
    the `h{depth}-{slug}` anchor format.

2.  `persist_artifact_blocks` -- a hand-rolled FakeConn/FakeTxnPool
    (same idiom as `test_claim_evidence_offline.py`; each offline file
    rolls its own, not shared) proving one INSERT per block, the column
    list, `ON CONFLICT (artifact_id, artifact_content_hash, block_index)
    DO NOTHING`, `parent_index` -> real parent id resolution, `uuid7`
    ids, tenant bound before the writes, and `[]` -> zero INSERTs.

3.  `get_artifact_blocks` / `get_block_span` -- exact bounded SELECT
    shape (`t_invalid IS NULL`, `ORDER BY block_index`).
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.services.artifact_blocks import (
    BLOCK_TYPES,
    NORMALIZER_VERSION,
    Block,
    get_artifact_blocks,
    get_block_span,
    normalize_markdown,
    normalize_text,
    persist_artifact_blocks,
    redact_blocks_for_persistence,
)

ARTIFACT_ID = "00000000-0000-4000-8000-0000000000a1"
CONTENT_HASH = "sha256:deadbeef"


def test_secret_shaped_block_text_is_redacted_but_source_span_is_preserved():
    raw = "api_key=sk-abcdefghijklmnopqrstuvwxyz123456\n"
    block = Block(
        block_index=0, block_type="paragraph", depth=0, text=raw,
        source_start=0, source_end=len(raw), anchor=None, parent_index=None,
    )
    safe, patterns = redact_blocks_for_persistence([block])
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in safe[0].text
    assert "REDACTED" in safe[0].text
    assert patterns
    assert safe[0].source_start == block.source_start
    assert safe[0].source_end == block.source_end


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _run(coro):
    return asyncio.run(coro)


MARKDOWN_FIXTURE = """\
---
name: demo-skill
description: a demo
---

# Demo Skill

Intro paragraph one.
Still the same paragraph.

## Foo Bar

Body under foo bar.

1. first step
2. second step
3. third step

```python
def x():
    # a comment that must not become its own block
    return 1
```
"""


# ---------------------------------------------------------------------
# normalize_markdown / normalize_text  (pure)
# ---------------------------------------------------------------------


def test_module_constants_are_stable():
    assert NORMALIZER_VERSION == "block_normalizer@v1"
    assert "heading" in BLOCK_TYPES and "code_block" in BLOCK_TYPES
    assert "frontmatter" in BLOCK_TYPES and "other" in BLOCK_TYPES


def test_normalize_markdown_empty_is_empty_list():
    assert normalize_markdown("") == []


def test_normalize_text_empty_is_empty_list():
    assert normalize_text("") == []


def test_normalize_markdown_shapes_the_fixture():
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    kinds = [b.block_type for b in blocks]

    # frontmatter, h1, para, h2, para, 3 ordered list items, code block
    assert kinds == [
        "frontmatter",
        "heading",
        "paragraph",
        "heading",
        "paragraph",
        "ordered_list_item",
        "ordered_list_item",
        "ordered_list_item",
        "code_block",
    ]

    # block_index is dense and in order
    assert [b.block_index for b in blocks] == list(range(len(blocks)))

    # every block_type is in the declared vocabulary
    assert all(b.block_type in BLOCK_TYPES for b in blocks)


def test_normalize_markdown_offsets_round_trip_for_every_block():
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    assert blocks  # sanity
    for b in blocks:
        assert 0 <= b.source_start <= b.source_end <= len(MARKDOWN_FIXTURE)
        span = MARKDOWN_FIXTURE[b.source_start : b.source_end]
        # the span is exactly what the block was cut from (no trailing NL)
        assert not span.endswith("\n")
        if b.block_type != "heading":
            # non-heading blocks store the raw span verbatim as text
            assert b.text == span
    # offsets are non-overlapping and ascending
    for prev, nxt in zip(blocks, blocks[1:]):
        assert prev.source_end <= nxt.source_start


def test_normalize_markdown_heading_depth_and_anchor():
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    headings = [b for b in blocks if b.block_type == "heading"]
    assert [h.depth for h in headings] == [1, 2]
    assert headings[0].text == "Demo Skill"
    assert headings[1].text == "Foo Bar"
    # anchor format: h{depth}-{slug}
    assert headings[0].anchor == "h1-demo-skill"
    assert headings[1].anchor == "h2-foo-bar"


def test_normalize_markdown_frontmatter_is_one_block_covering_the_fence():
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    fm = blocks[0]
    assert fm.block_type == "frontmatter"
    assert fm.text.startswith("---")
    assert fm.text.rstrip().endswith("---")
    assert "name: demo-skill" in fm.text


def test_normalize_markdown_list_items_nest_under_preceding_heading():
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    foo_bar_idx = next(
        b.block_index for b in blocks if b.block_type == "heading" and b.text == "Foo Bar"
    )
    items = [b for b in blocks if b.block_type == "ordered_list_item"]
    assert len(items) == 3
    assert all(it.parent_index == foo_bar_idx for it in items)
    assert [it.text for it in items] == ["1. first step", "2. second step", "3. third step"]


def test_normalize_markdown_paragraph_nests_under_preceding_heading():
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    h1_idx = next(
        b.block_index for b in blocks if b.block_type == "heading" and b.text == "Demo Skill"
    )
    h2_idx = next(
        b.block_index for b in blocks if b.block_type == "heading" and b.text == "Foo Bar"
    )
    paras = [b for b in blocks if b.block_type == "paragraph"]
    assert paras[0].parent_index == h1_idx
    assert paras[0].text == "Intro paragraph one.\nStill the same paragraph."
    assert paras[1].parent_index == h2_idx


def test_normalize_markdown_h2_nests_under_h1():
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    h1_idx = next(b.block_index for b in blocks if b.block_type == "heading" and b.depth == 1)
    h2 = next(b for b in blocks if b.block_type == "heading" and b.depth == 2)
    assert h2.parent_index == h1_idx


def test_normalize_markdown_code_block_interior_not_split():
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    code = [b for b in blocks if b.block_type == "code_block"]
    assert len(code) == 1
    body = code[0].text
    assert body.startswith("```python")
    assert body.rstrip().endswith("```")
    # the interior comment line is inside the single block, not its own
    assert "# a comment that must not become its own block" in body
    assert "def x():" in body


def test_normalize_markdown_unordered_list_and_table():
    md = "## H\n\n- a\n- b\n\n| col |\n| --- |\n| v |\n"
    blocks = normalize_markdown(md)
    kinds = [b.block_type for b in blocks]
    assert kinds == [
        "heading",
        "unordered_list_item",
        "unordered_list_item",
        "table",
    ]
    table = blocks[-1]
    assert md[table.source_start : table.source_end] == "| col |\n| --- |\n| v |"


def test_normalize_text_splits_on_blank_lines_with_offsets():
    txt = "first para line 1\nfirst para line 2\n\nsecond para\n"
    blocks = normalize_text(txt)
    assert [b.block_type for b in blocks] == ["paragraph", "paragraph"]
    assert all(b.depth == 0 and b.parent_index is None and b.anchor is None for b in blocks)
    for b in blocks:
        assert txt[b.source_start : b.source_end] == b.text
    assert blocks[0].text == "first para line 1\nfirst para line 2"
    assert blocks[1].text == "second para"


# ---------------------------------------------------------------------
# persist_artifact_blocks
# ---------------------------------------------------------------------


class _Row(dict):
    pass


class FakeConn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    class _TxnCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def transaction(self):
        return FakeConn._TxnCM(self)

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "INSERT 0 1"

    def index_of(self, needle: str) -> int:
        for i, (s, _) in enumerate(self.statements):
            if needle in s:
                return i
        raise AssertionError(f"no statement matching {needle!r}")

    def inserts(self) -> list[tuple[str, tuple]]:
        return [(s, a) for s, a in self.statements if "INSERT INTO artifact_blocks" in s]


class FakeTxnPool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    class _AcquireCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return FakeTxnPool._AcquireCM(self._conn)


def _blocks_two_nested() -> list[Block]:
    return [
        Block(0, "heading", 1, "Top", 0, 5, "h1-top", None),
        Block(1, "paragraph", 0, "body", 7, 11, None, 0),
    ]


def test_persist_empty_blocks_writes_nothing():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    out = _run(
        persist_artifact_blocks(
            pool,
            artifact_id=ARTIFACT_ID,
            artifact_content_hash=CONTENT_HASH,
            blocks=[],
            created_by="t",
        )
    )
    assert out == []
    assert conn.statements == []


def test_persist_one_insert_per_block_with_column_list_and_on_conflict():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    out = _run(
        persist_artifact_blocks(
            pool,
            artifact_id=ARTIFACT_ID,
            artifact_content_hash=CONTENT_HASH,
            blocks=_blocks_two_nested(),
            created_by="tester",
        )
    )
    assert len(out) == 2
    inserts = conn.inserts()
    assert len(inserts) == 2

    sql0, args0 = inserts[0]
    assert "INSERT INTO artifact_blocks (" in sql0
    for col in (
        "id, artifact_id, artifact_content_hash, block_index, parent_block_id,",
        "block_type, depth, text, source_start, source_end, anchor,",
        "created_by, visibility, owner_id, scope_type, scope_entity_id,",
        "ingestion_context_id",
    ):
        assert col in sql0
    assert (
        "ON CONFLICT (artifact_id, artifact_content_hash, block_index) DO NOTHING"
        in sql0
    )

    # arg order: id, artifact_id, hash, block_index, parent, type, depth, text,
    # source_start, source_end, anchor, created_by, visibility, owner_id,
    # scope_type, scope_entity_id, ingestion_context_id
    assert str(args0[1]) == ARTIFACT_ID
    assert args0[2] == CONTENT_HASH
    assert args0[3] == 0
    assert args0[4] is None            # top-level heading, no parent
    assert args0[5] == "heading"
    assert args0[6] == 1               # depth
    assert args0[7] == "Top"
    assert args0[8] == 0 and args0[9] == 5
    assert args0[10] == "h1-top"
    assert args0[11] == "tester"
    assert args0[12] == "public"
    assert args0[14] == "global"       # scope_type default
    assert args0[16] is None           # ingestion_context_id


def test_persist_resolves_parent_index_to_the_first_inserts_id():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    out = _run(
        persist_artifact_blocks(
            pool,
            artifact_id=ARTIFACT_ID,
            artifact_content_hash=CONTENT_HASH,
            blocks=_blocks_two_nested(),
            created_by="tester",
        )
    )
    inserts = conn.inserts()
    first_id = inserts[0][1][0]
    second_parent = inserts[1][1][4]
    assert second_parent == first_id == out[0]
    # and the ids are uuid7 (valid version-7 UUIDs)
    for row_id in out:
        assert UUID(row_id).version == 7


def test_persist_binds_tenant_before_the_first_write():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    _run(
        persist_artifact_blocks(
            pool,
            artifact_id=ARTIFACT_ID,
            artifact_content_hash=CONTENT_HASH,
            blocks=_blocks_two_nested(),
            created_by="tester",
        )
    )
    set_config_idx = conn.index_of("set_config")
    first_insert_idx = conn.index_of("INSERT INTO artifact_blocks")
    assert set_config_idx < first_insert_idx


def test_persist_forwards_context_scope_and_visibility():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    _run(
        persist_artifact_blocks(
            pool,
            artifact_id=ARTIFACT_ID,
            artifact_content_hash=CONTENT_HASH,
            blocks=[Block(0, "paragraph", 0, "x", 0, 1, None, None)],
            created_by="tester",
            ingestion_context_id="00000000-0000-4000-8000-0000000000c9",
            visibility="private",
            owner_id="user-7",
            scope_type="entity",
            scope_entity_id="repo-42",
        )
    )
    _, args = conn.inserts()[0]
    assert args[12] == "private"
    assert args[13] == "user-7"
    assert args[14] == "entity"
    assert args[15] == "repo-42"
    assert args[16] == "00000000-0000-4000-8000-0000000000c9"


def test_persist_end_to_end_from_normalize_markdown():
    conn = FakeConn()
    pool = FakeTxnPool(conn)
    blocks = normalize_markdown(MARKDOWN_FIXTURE)
    out = _run(
        persist_artifact_blocks(
            pool,
            artifact_id=ARTIFACT_ID,
            artifact_content_hash=CONTENT_HASH,
            blocks=blocks,
            created_by="tester",
        )
    )
    assert len(out) == len(blocks)
    inserts = conn.inserts()
    assert len(inserts) == len(blocks)
    # the list items' parent arg is the Foo Bar heading's inserted id
    foo_bar_pos = next(
        i for i, (_, a) in enumerate(inserts) if a[5] == "heading" and a[7] == "Foo Bar"
    )
    foo_bar_id = inserts[foo_bar_pos][1][0]
    item_rows = [a for _, a in inserts if a[5] == "ordered_list_item"]
    assert item_rows and all(a[4] == foo_bar_id for a in item_rows)


# ---------------------------------------------------------------------
# get_artifact_blocks / get_block_span
# ---------------------------------------------------------------------


class FakeReadPool:
    def __init__(self, rows):
        self._rows = rows
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((_norm(sql), params))
        return self._rows

    async def fetchrow(self, sql, *params):
        self.fetchrow_calls.append((_norm(sql), params))
        return self._rows[0] if self._rows else None


def test_get_artifact_blocks_issues_the_exact_bounded_query():
    pool = FakeReadPool(rows=[])
    result = _run(get_artifact_blocks(pool, ARTIFACT_ID))
    assert result == []
    assert len(pool.fetch_calls) == 1
    sql, params = pool.fetch_calls[0]
    assert "FROM artifact_blocks" in sql
    assert "artifact_id = $1::uuid" in sql
    assert "t_invalid IS NULL" in sql
    assert "ORDER BY block_index ASC" in sql
    assert params == (ARTIFACT_ID,)


def test_get_artifact_blocks_returns_plain_dicts():
    rows = [_Row({"id": "b1", "block_index": 0}), _Row({"id": "b2", "block_index": 1})]
    pool = FakeReadPool(rows=rows)
    result = _run(get_artifact_blocks(pool, ARTIFACT_ID))
    assert result == [{"id": "b1", "block_index": 0}, {"id": "b2", "block_index": 1}]
    assert all(type(r) is dict for r in result)


def test_get_block_span_issues_bounded_single_row_query():
    pool = FakeReadPool(rows=[_Row({"id": "b1", "source_start": 3, "source_end": 9})])
    result = _run(get_block_span(pool, "b1"))
    assert result == {"id": "b1", "source_start": 3, "source_end": 9}
    assert type(result) is dict
    sql, params = pool.fetchrow_calls[0]
    assert "FROM artifact_blocks" in sql
    assert "id = $1::uuid" in sql
    assert "t_invalid IS NULL" in sql
    assert params == ("b1",)


def test_get_block_span_returns_none_when_missing():
    pool = FakeReadPool(rows=[])
    assert _run(get_block_span(pool, "missing")) is None

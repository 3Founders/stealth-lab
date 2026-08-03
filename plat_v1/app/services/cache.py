"""
Input fingerprinting and the routing cache.

The cache is what makes marginal cost collapse, so it is a subsystem rather
than an optimisation. Everything here follows from one decision:

    **A document's fingerprint is its layout, not its content.**

Two invoices from the same vendor with different amounts must land on the
same fingerprint, because what is being cached is *how to read this shape of
document* -- which implementation won and with what parameters -- not the
answer. A content hash would give a ~0% hit rate and make the mechanism
decorative.

That forces the quantisation grid to be coarse. Text extents move with
content ("$45.00" and "$1,284.00" are different widths), so only the
top-left corner of each block is used, snapped to a grid. The tradeoff runs
in both directions and neither end is free:

  too fine   -> the same vendor's invoices miss, and the cache does nothing
  too coarse -> genuinely different layouts collide, and a cached mapping is
                reused on a document it was never validated against

The second failure is the expensive one -- it produces a wrong answer rather
than a slow one -- so the default sits at a third of an inch, which is
comfortably larger than typical content jitter and smaller than the gap
between distinct form layouts. Tune it against real documents before
trusting it on a new corpus.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from uuid import UUID

log = logging.getLogger(__name__)

# Points. 24pt = 1/3 inch at 72dpi.
DEFAULT_GRID = 24.0

BBox = tuple[float, float, float, float]  # x0, top, x1, bottom


@dataclass(frozen=True)
class PageLayout:
    width: float
    height: float
    # Text block bounding boxes.
    blocks: tuple[BBox, ...] = ()
    # Ruled lines and rectangles. Kept separate from text because they are
    # pure structure: a table's borders sit in the same place whatever the
    # table says, so when a page has them they are a strictly better basis
    # for a layout fingerprint than anything derived from words.
    rules: tuple[BBox, ...] = ()


@dataclass(frozen=True)
class DocumentLayout:
    pages: tuple[PageLayout, ...] = ()


def _snap(value: float, grid: float) -> int:
    return int(round(float(value) / grid))


def layout_signature(layout: DocumentLayout, grid: float = DEFAULT_GRID) -> dict[str, Any]:
    """
    The canonical, content-free description a fingerprint is taken over.

    Rows and columns are projected onto separate axes rather than kept as
    (x, y) pairs, and the source of those axes matters more than the
    quantisation does.

    When the page has ruled lines or rectangles, they are the whole
    signature. They are content-independent by construction -- a table's
    borders sit in the same place whatever the table says -- which is exactly
    the property the cache needs, and ruled tables are what bulk document
    processing mostly consists of.

    Without them, the fallback is text positions, and there the naive version
    fails. Two invoices from the same vendor do not have their words in the
    same places: "Hex bolt M8" and "Copper pipe 15mm" differ in length and in
    word count, so the second and third words of a description cell land
    somewhere different every time. Only the *column starts* are stable, and
    they are distinguishable because a column start recurs once per row while
    a mid-cell word appears once. Hence the frequency filter. It is a
    heuristic, and it is why the ruled path is preferred when available.

    Exposed separately from the hash so a mismatch can be debugged by diffing
    two signatures rather than by staring at two hex strings.
    """
    pages = []
    for page in layout.pages:
        if page.rules:
            rows = sorted({_snap(shape[1], grid) for shape in page.rules})
            columns = sorted({_snap(shape[0], grid) for shape in page.rules})
            basis = "rules"
        else:
            # Sorted and de-duplicated after snapping: extraction order varies
            # between libraries and versions, and a wrapped line inside one
            # cell should not read as a different layout from an unwrapped one.
            rows = sorted({_snap(b[1], grid) for b in page.blocks})

            occurrences: dict[int, int] = {}
            for block in page.blocks:
                column = _snap(block[0], grid)
                occurrences[column] = occurrences.get(column, 0) + 1

            threshold = max(1, (len(rows) + 1) // 2)
            columns = sorted(x for x, count in occurrences.items() if count >= threshold)
            basis = "text"

        pages.append(
            {
                "w": _snap(page.width, grid),
                "h": _snap(page.height, grid),
                "basis": basis,
                "rows": rows,
                "columns": columns,
            }
        )
    return {"page_count": len(layout.pages), "grid": grid, "pages": pages}


def layout_fingerprint(layout: DocumentLayout, grid: float = DEFAULT_GRID) -> str:
    payload = json.dumps(layout_signature(layout, grid), sort_keys=True, separators=(",", ":"))
    return "layout:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def read_pdf_layout(path: str | Path) -> DocumentLayout:
    """
    Extract a layout with pdfplumber.

    Ruled lines and rectangles are included alongside words. They are pure
    structure -- a table's borders sit in the same place whatever the table
    says -- so they survive the column filter easily and sharpen the
    signature on exactly the documents most likely to be processed in bulk.

    Imported lazily: the offline test suite fingerprints synthetic layouts and
    must not require a PDF stack to be installed.
    """
    import pdfplumber

    pages: list[PageLayout] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            blocks: list[BBox] = [
                (float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"]))
                for w in (page.extract_words() or [])
            ]
            rules: list[BBox] = [
                (
                    float(shape["x0"]),
                    float(shape["top"]),
                    float(shape["x1"]),
                    float(shape["bottom"]),
                )
                for shape in list(page.lines or []) + list(page.rects or [])
            ]
            pages.append(
                PageLayout(
                    width=float(page.width),
                    height=float(page.height),
                    blocks=tuple(blocks),
                    rules=tuple(rules),
                )
            )
    return DocumentLayout(pages=tuple(pages))


LayoutReader = Callable[[str], DocumentLayout]

_DOCUMENT_SUFFIXES = (".pdf",)


def _looks_like_document(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.lower().endswith(_DOCUMENT_SUFFIXES)
        and Path(value).exists()
    )


def _canonical(value: Any, layout_reader: Optional[LayoutReader]) -> Any:
    """Replace document paths with their layout fingerprint; leave the rest alone."""
    if isinstance(value, dict):
        return {k: _canonical(v, layout_reader) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v, layout_reader) for v in value]
    if _looks_like_document(value):
        reader = layout_reader or read_pdf_layout
        try:
            return layout_fingerprint(reader(value))
        except Exception as exc:  # noqa: BLE001
            # Degrade to the path, not to the file's bytes. A content hash
            # here would look like it worked while quietly guaranteeing a
            # miss on every subsequent document.
            log.warning("could not read layout of %s, fingerprinting by path: %s", value, exc)
            return f"path:{Path(value).name}"
    return value


def fingerprint_inputs(
    inputs: Mapping[str, Any],
    layout_reader: Optional[LayoutReader] = None,
    cache_key: Optional[list[str]] = None,
) -> str:
    """
    A deterministic fingerprint for one stage's inputs.

    Not scoped by task: `cache_entries` is keyed on (task_node_id,
    fingerprint), so the task is already part of the lookup and folding it in
    here would only make two fingerprints incomparable in tests.
    """
    # The key set is part of the hash. Without it, `cache_key=["columns"]`
    # over {columns: X, grid: Y} and `cache_key=None` over {columns: X}
    # produce the same fingerprint -- so editing a task's cache_key would
    # alias two different input populations onto one entry.
    canonical = {
        "keys": sorted(cache_key) if cache_key is not None else None,
        "inputs": _canonical(dict(inputs), layout_reader),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@dataclass
class CacheHit:
    entry_id: UUID
    implementation_id: UUID
    params: dict[str, Any]
    hits: int


class CacheStore:
    """Postgres-backed cache. Swapped for a fake in the router's unit tests."""

    def __init__(self, pool):
        self._pool = pool

    async def probe(self, task_node_id: UUID, fingerprint: str) -> Optional[CacheHit]:
        row = await self._pool.fetchrow(
            """
            SELECT c.id, c.implementation_id, c.params, c.hits
            FROM cache_entries c
            JOIN implementations i ON i.id = c.implementation_id
            WHERE c.task_node_id = $1 AND c.fingerprint = $2
              AND i.enabled AND i.t_invalid IS NULL
            """,
            task_node_id,
            fingerprint,
        )
        if row is None:
            return None
        return CacheHit(
            entry_id=row["id"],
            implementation_id=row["implementation_id"],
            params=row["params"] or {},
            hits=row["hits"],
        )

    async def record_hit(self, entry_id: UUID) -> None:
        await self._pool.execute(
            "UPDATE cache_entries SET hits = hits + 1, last_hit_at = now() WHERE id = $1",
            entry_id,
        )

    async def write(
        self,
        task_node_id: UUID,
        fingerprint: str,
        implementation_id: UUID,
        params: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        Called after any stage that passed its success criteria.

        On conflict the implementation is overwritten: the newest successful
        route is the one worth reusing, and a stale entry pointing at an
        implementation that has since started failing is exactly what the
        cache should not preserve.
        """
        await self._pool.execute(
            """
            INSERT INTO cache_entries (task_node_id, fingerprint, implementation_id, params)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (task_node_id, fingerprint) DO UPDATE
              SET implementation_id = EXCLUDED.implementation_id,
                  params = EXCLUDED.params
            """,
            task_node_id,
            fingerprint,
            implementation_id,
            dict(params or {}),
        )

    async def has_entry(self, task_node_id: UUID, fingerprint: str) -> bool:
        """Whether this layout has been seen before -- used by the first-layout gate."""
        row = await self._pool.fetchrow(
            "SELECT 1 FROM cache_entries WHERE task_node_id = $1 AND fingerprint = $2",
            task_node_id,
            fingerprint,
        )
        return row is not None

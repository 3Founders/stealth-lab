"""
Fingerprinting.

The property under test is the one the whole cache rests on: two documents
with the same *layout* and different *content* must fingerprint identically.
A content hash would satisfy none of these and would give a 0% hit rate in
production while passing any test that only checked determinism.
"""
from __future__ import annotations

from app.services.cache import (
    DEFAULT_GRID,
    DocumentLayout,
    PageLayout,
    fingerprint_inputs,
    layout_fingerprint,
    layout_signature,
)


def invoice(amounts: list[str], *, y_offset: float = 0.0) -> DocumentLayout:
    """
    One page of a vendor's invoice template.

    Block widths scale with the text in them -- "$45.00" and "$1,284,000.00"
    are very different widths -- which is exactly the variation the
    fingerprint has to be blind to.
    """
    blocks = []
    for index, amount in enumerate(amounts):
        top = 100.0 + index * 30.0 + y_offset
        blocks.append((72.0, top, 72.0 + 40.0, top + 12.0))          # label
        blocks.append((400.0, top, 400.0 + 7.0 * len(amount), top + 12.0))  # the amount
    return DocumentLayout(pages=(PageLayout(width=612.0, height=792.0, blocks=tuple(blocks)),))


def test_same_layout_different_content_fingerprints_identically():
    january = invoice(["$45.00", "$12.10", "$7.99"])
    february = invoice(["$1,284,000.00", "$3.50", "$99,999.00"])

    assert layout_fingerprint(january) == layout_fingerprint(february)


def test_different_layouts_do_not_collide():
    three_rows = invoice(["$1.00", "$2.00", "$3.00"])
    four_rows = invoice(["$1.00", "$2.00", "$3.00", "$4.00"])

    assert layout_fingerprint(three_rows) != layout_fingerprint(four_rows)


def test_page_count_changes_the_fingerprint():
    one_page = invoice(["$1.00"])
    two_pages = DocumentLayout(pages=one_page.pages + one_page.pages)

    assert layout_fingerprint(one_page) != layout_fingerprint(two_pages)


def test_page_size_changes_the_fingerprint():
    letter = invoice(["$1.00"])
    a4 = DocumentLayout(
        pages=(PageLayout(width=595.0, height=842.0, blocks=letter.pages[0].blocks),)
    )
    assert layout_fingerprint(letter) != layout_fingerprint(a4)


def test_small_jitter_within_the_grid_is_absorbed():
    """Sub-grid drift between two renderings of the same template is not a new layout."""
    baseline = invoice(["$1.00"])
    nudged = invoice(["$1.00"], y_offset=DEFAULT_GRID / 4)

    assert layout_fingerprint(baseline) == layout_fingerprint(nudged)


def test_a_whole_grid_cell_of_movement_is_a_different_layout():
    baseline = invoice(["$1.00"])
    shifted = invoice(["$1.00"], y_offset=DEFAULT_GRID * 3)

    assert layout_fingerprint(baseline) != layout_fingerprint(shifted)


def test_block_order_does_not_matter():
    page = invoice(["$1.00", "$2.00"]).pages[0]
    reversed_page = PageLayout(page.width, page.height, tuple(reversed(page.blocks)))

    assert layout_fingerprint(DocumentLayout((page,))) == layout_fingerprint(
        DocumentLayout((reversed_page,))
    )


def test_signature_is_debuggable():
    signature = layout_signature(invoice(["$1.00"]))
    assert signature["page_count"] == 1
    assert signature["pages"][0]["basis"] == "text"
    assert signature["pages"][0]["columns"]


# --- the ruled path, which is preferred whenever a page has borders --------


def ruled_page(row_tops: list[float], column_xs: list[float]) -> DocumentLayout:
    rules = [(column_xs[0], top, column_xs[-1], top + 0.7) for top in row_tops]
    rules += [(x, row_tops[0], x + 0.7, row_tops[-1]) for x in column_xs]
    return DocumentLayout(
        pages=(PageLayout(width=612.0, height=792.0, blocks=(), rules=tuple(rules)),)
    )


def test_ruled_pages_ignore_text_entirely():
    grid = ruled_page([100.0, 130.0, 160.0], [60.0, 200.0, 400.0])
    # Same borders, wildly different text sitting inside them.
    with_text = DocumentLayout(
        pages=(
            PageLayout(
                width=612.0,
                height=792.0,
                blocks=((61.0, 101.0, 300.0, 111.0), (205.0, 131.0, 260.0, 141.0)),
                rules=grid.pages[0].rules,
            ),
        )
    )
    assert layout_fingerprint(grid) == layout_fingerprint(with_text)
    assert layout_signature(grid)["pages"][0]["basis"] == "rules"


def test_different_rulings_still_differ():
    three_columns = ruled_page([100.0, 130.0], [60.0, 200.0, 400.0])
    two_columns = ruled_page([100.0, 130.0], [60.0, 400.0])
    assert layout_fingerprint(three_columns) != layout_fingerprint(two_columns)


# --- input fingerprints ----------------------------------------------------


def test_input_fingerprint_is_key_order_independent():
    assert fingerprint_inputs({"a": 1, "b": 2}) == fingerprint_inputs({"b": 2, "a": 1})


def test_input_fingerprint_distinguishes_values():
    assert fingerprint_inputs({"a": 1}) != fingerprint_inputs({"a": 2})


def test_input_fingerprint_uses_layout_for_pdf_paths(tmp_path):
    first = tmp_path / "january.pdf"
    second = tmp_path / "february.pdf"
    first.write_bytes(b"%PDF-1.4 january")
    second.write_bytes(b"%PDF-1.4 february totally different bytes")

    # Same layout reported for both, despite different names and bytes.
    reader = lambda path: invoice(["$1.00"])  # noqa: E731

    assert fingerprint_inputs({"pdf_path": str(first)}, reader) == fingerprint_inputs(
        {"pdf_path": str(second)}, reader
    )


def test_unreadable_pdf_degrades_to_the_filename_not_the_bytes(tmp_path):
    """
    A content hash as a fallback would look like it worked while guaranteeing
    a miss on every subsequent document.
    """
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not really a pdf")

    def explode(_):
        raise ValueError("cannot parse")

    first = fingerprint_inputs({"pdf_path": str(path)}, explode)
    path.write_bytes(b"still not a pdf, but different")
    second = fingerprint_inputs({"pdf_path": str(path)}, explode)

    assert first == second

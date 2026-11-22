"""Thumbnails for procedure outputs (app/services/output_thumbnails.py).

These build real bytes in-process — a PDF with PyMuPDF, a PNG with Pillow — so the test proves the module renders
an actual document rather than checking a mock was called. That is the whole claim: a thumbnail is a picture of
the real artefact, so the test has to have a real artefact in it.

Everything here degrades to a skip when the optional renderer (PyMuPDF) is absent, because an environment without
it is a supported deployment (the caller then draws a labelled tile), not a failing one.
"""
from __future__ import annotations

import io

import pytest

from app.services import output_thumbnails as ot


def _pdf_bytes(pages: int = 3) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Quarterly report - page {i + 1}", fontsize=24)
    return doc.tobytes()


def test_a_pdf_renders_a_webp_and_reports_its_page_count():
    data = _pdf_bytes(3)
    out = ot.make_thumbnail(data, kind="pdf")

    assert out["kind"] == "pdf"
    assert out["pages"] == 3
    assert out["webp"][:4] == b"RIFF"  # a real WebP container, not an empty buffer
    assert out["sha256"] == ot._sha256(out["webp"])


def test_the_same_document_always_gets_the_same_key():
    data = _pdf_bytes(2)
    a = ot.make_thumbnail(data, kind="pdf")
    b = ot.make_thumbnail(data, kind="pdf")
    # Content-addressed: the object key is derived from the picture, so two runs cannot disagree.
    assert a["sha256"] == b["sha256"]


def test_pdf_is_detected_by_its_header_not_only_by_the_kind():
    data = _pdf_bytes(1)
    out = ot.make_thumbnail(data)  # no kind given
    assert out["kind"] == "pdf"
    assert out["pages"] == 1


def test_the_thumbnail_is_bounded_to_the_requested_width():
    from PIL import Image

    data = _pdf_bytes(1)
    out = ot.make_thumbnail(data, kind="pdf", max_w=320)
    img = Image.open(io.BytesIO(out["webp"]))
    assert img.width <= 320


def test_a_raster_artefact_is_downscaled_not_cropped():
    from PIL import Image

    src = Image.new("RGB", (1600, 900), (20, 40, 80))
    buf = io.BytesIO()
    src.save(buf, "PNG")

    out = ot.make_thumbnail(buf.getvalue(), kind="png", max_w=800)
    img = Image.open(io.BytesIO(out["webp"]))
    assert img.width == 800
    # Aspect ratio preserved: cropping would hide part of the thing being sold.
    assert abs((img.height / img.width) - (900 / 1600)) < 0.02


def test_a_malformed_pdf_is_refused_rather_than_producing_a_blank_picture():
    with pytest.raises(ot.ThumbnailUnavailable):
        ot.make_thumbnail(b"%PDF-1.4 not really a pdf", kind="pdf")


def test_an_office_file_without_a_renderer_says_so_instead_of_faking_one():
    # No LibreOffice in this environment: the module must refuse, so the caller draws a labelled tile.
    with pytest.raises(ot.ThumbnailUnavailable):
        ot.make_thumbnail(b"PK\x03\x04fake", kind="pptx")


def test_office_text_can_still_be_read_with_the_standard_library():
    """A .docx/.pptx is a zip of XML; the first text runs are recoverable without any office suite."""
    import zipfile

    xml = (
        b'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
        b"<w:p><w:r><w:t>Refund a captured payment</w:t></w:r></w:p>"
        b"<w:p><w:r><w:t>Check for a duplicate first</w:t></w:r></w:p>"
        b"</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)

    lines = ot.first_lines_from_office(buf.getvalue())
    assert lines[0] == "Refund a captured payment"
    assert "Check for a duplicate first" in lines


def test_an_unknown_kind_is_refused_by_name():
    with pytest.raises(ot.ThumbnailUnavailable) as exc:
        ot.make_thumbnail(b"whatever", kind="xls")
    assert "xls" in str(exc.value)

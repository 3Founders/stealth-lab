"""
Thumbnails for procedure outputs: the picture that stands in for a report or a deck.

Why this module exists
----------------------
A Way page shows what the Way *produces* (docs/procedure_outputs.md). For anything longer than a screenful, the
honest way to show it is a picture of the first page — the same move a video site makes with a thumbnail. That
picture has to be produced deterministically: a model asked to "illustrate this report" will draw a cover that
does not exist, and a page whose whole argument is "here is the real artefact" cannot afford an invented one.

What is guaranteed
------------------
* **No model call.** Rendering is pure: bytes in, WebP out.
* **Content-addressed.** The returned `sha256` keys the object (`thumbs/<sha256>.webp`), so the same artefact
  always maps to the same thumbnail and the URL can be cached forever.
* **Honest failure.** When a format cannot be rendered here, this raises `ThumbnailUnavailable` rather than
  returning a placeholder image. The caller renders a labelled tile instead (the frontend already draws
  `kwm-similar-blabel`); a broken or invented picture is worse than a tidy blank.

What is NOT guaranteed
----------------------
Layout fidelity for non-PDF formats. A `.pptx` is a zip of XML: the standard library can read its text runs, but
positions, theme, fonts and images live in other parts. Rendering those faithfully needs either a headless office
suite (LibreOffice, ~500 MB) or a pager we control. This module therefore:

  - renders PDFs exactly (PyMuPDF, the primary path),
  - extracts PPTX/DOCX *text* for a text-only card at best,
  - and says so, loudly, instead of pretending.

Measured 2026-11-22 on this machine: a 2-page PDF rendered page 1 at 90 dpi and encoded to WebP in ~2.4 KB;
PyMuPDF, Pillow and python-docx are installed; LibreOffice and python-pptx are not.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
import re
from typing import Optional

# Pillow is a declared dependency (requirements.txt: avatar normalisation). PyMuPDF is optional: when it is
# absent, PDF thumbnailing degrades to ThumbnailUnavailable rather than failing the import of this module.
try:  # pragma: no cover - import guard
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]

try:  # pragma: no cover - import guard
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None  # type: ignore[assignment]


WEBP_QUALITY = 82
# 640 px covers the Way page's artefact preview; 1280 px covers a 16:9 tile in the "more like this" rail.
PREVIEW_WIDTH = 640
TILE_WIDTH = 1280


class ThumbnailUnavailable(Exception):
    """This format cannot be rendered with the tools present. The caller should draw a labelled tile."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _encode(img: "Image.Image", max_w: int) -> bytes:
    """Resize to `max_w` and encode WebP. One helper so the encoder settings exist in exactly one place."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.width > max_w:
        img = img.resize((max_w, max(1, round(img.height * max_w / img.width))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
    return buf.getvalue()


def thumb_from_pdf(pdf: bytes, *, max_w: int = PREVIEW_WIDTH, dpi: int = 90) -> tuple[bytes, int]:
    """First page of a PDF as WebP, plus the page count.

    The page count is returned because the Way page prints it ("12 pages") and it is free here — one open of the
    document answers both questions, so the caller never has to open the PDF a second time.
    """
    if fitz is None or Image is None:
        raise ThumbnailUnavailable("PyMuPDF/Pillow are not installed; cannot render a PDF thumbnail")
    try:
        doc = fitz.open(stream=pdf, filetype="pdf")
    except Exception as exc:  # malformed PDF
        raise ThumbnailUnavailable(f"not a readable PDF: {exc}") from exc
    if doc.page_count == 0:
        raise ThumbnailUnavailable("PDF has no pages")
    pages = doc.page_count
    # PyMuPDF's dpi scale: get_pixmap(dpi=...) resolves to zoom = dpi/72 internally on modern versions.
    pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return _encode(img, max_w), pages


def thumb_from_image(data: bytes, *, max_w: int = PREVIEW_WIDTH) -> bytes:
    """A raster upload (PNG/JPEG/WebP) normalised to WebP. The artefact *is* an image, so the thumbnail is the
    artefact, downscaled — never cropped, because cropping an image artefact would hide part of the product."""
    if Image is None:
        raise ThumbnailUnavailable("Pillow is not installed; cannot normalise an image")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ThumbnailUnavailable(f"not a readable image: {exc}") from exc
    return _encode(img, max_w)


_DOCX_PARA = re.compile(rb"<w:t[^>]*>(.*?)</w:t>", re.S)
_PPTX_TEXT = re.compile(rb"<a:t>(.*?)</a:t>", re.S)


def first_lines_from_office(data: bytes, *, limit: int = 6) -> list[str]:
    """The first few text runs of a .docx or .pptx, read with the STANDARD LIBRARY only.

    Deliberately narrow: this is not a renderer and does not claim to be one. It exists so a deck with no
    renderer available can still get a *truthful* tile (the real title, the real first bullets) instead of a
    picture of nothing. Positions, theme, fonts and images are all ignored — that is the documented limit.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            if "word/document.xml" in names:
                raw = z.read("word/document.xml")
                pattern = _DOCX_PARA
            else:
                slides = sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
                if not slides:
                    raise ThumbnailUnavailable("no word/document.xml and no ppt/slides/slideN.xml")
                raw = z.read(slides[0])
                pattern = _PPTX_TEXT
    except zipfile.BadZipFile as exc:
        raise ThumbnailUnavailable(f"not a readable Office file: {exc}") from exc

    out: list[str] = []
    for m in pattern.finditer(raw):
        # The run text sits between the tags. Decoding that directly avoids re-parsing a fragment whose
        # namespace prefixes (<w:t>, <a:t>) are declared on an ancestor this snippet does not carry.
        text = _unescape(m.group(1).decode("utf-8", "replace")).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _unescape(s: str) -> str:
    """The five XML entities Office actually emits in text runs."""
    return (
        s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def make_thumbnail(
    data: bytes,
    *,
    kind: Optional[str] = None,
    max_w: int = PREVIEW_WIDTH,
) -> dict:
    """One call the caller makes: what kind of artefact is this, give me a thumbnail.

    Returns `{"webp": bytes, "sha256": str, "kind": str, "pages": int|None, "text": list[str]|None}`.
    Raises `ThumbnailUnavailable` for anything it cannot render — the caller must handle that, not paper over it.
    """
    k = (kind or "").lower().lstrip(".")
    head = data[:8]
    if k == "pdf" or head.startswith(b"%PDF"):
        webp, pages = thumb_from_pdf(data, max_w=max_w)
        return {"webp": webp, "sha256": _sha256(webp), "kind": "pdf", "pages": pages, "text": None}
    if k in ("png", "jpg", "jpeg", "webp", "gif") or head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff":
        webp = thumb_from_image(data, max_w=max_w)
        return {"webp": webp, "sha256": _sha256(webp), "kind": k or "image", "pages": 1, "text": None}
    if k in ("pptx", "docx"):
        # No faithful renderer present: return the real text, and let the caller draw a labelled tile from it.
        raise ThumbnailUnavailable(
            "Office layout rendering is not available (no LibreOffice); "
            "call first_lines_from_office() for a text-only tile"
        )
    raise ThumbnailUnavailable(f"no thumbnail renderer for kind {k or 'unknown'!r}")

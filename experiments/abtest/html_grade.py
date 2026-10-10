"""Shared grader for the page-recreation suites (Design2Code-HARD, WebSight): render with headless Chromium, compare.

Both pages are rendered by the same browser at 1280x1024 (the reference HTML too, never the dataset's screenshot,
so fonts and rendering match). Scores:
  * text recall: the share of the reference's visible text lines (normalised, >= 2 characters) found in the page;
  * visual closeness of the two first-screen screenshots (grayscale, 640x512): 1 - mean|ref - page| / mean|ref -
    blank white page|, so the reference scores 1 and a blank page 0. (SSIM was tried first and dropped: mostly-white
    pages gave a blank page 0.84.)
Rules (fixed before any run, the same for both arms): VISIBLE = text recall >= 0.8 over the reference's lines at
index 0, 2, 4, ...; GRADE = text recall >= 0.8 over all lines AND visual closeness >= 0.5.
"""
from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path

VIEW = {"width": 1280, "height": 1024}
TEXT_MIN = 0.8
VISUAL_MIN = 0.5


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def render(html: str) -> tuple[list[str], "object"]:
    """-> (visible text lines, grayscale numpy screenshot at 640x512)."""
    from playwright.sync_api import sync_playwright
    import numpy as np
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="html_") as tmp:
        f = Path(tmp, "page.html")
        f.write_text(html, encoding="utf-8")
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                pg = b.new_page(viewport=VIEW)
                pg.goto(f.as_uri(), wait_until="load", timeout=60000)
                try:
                    pg.wait_for_load_state("networkidle", timeout=5000)
                except Exception:  # noqa: BLE001 -- a page that keeps loading is graded as it is
                    pass
                text = pg.evaluate("() => document.body ? document.body.innerText : ''")
                shot = pg.screenshot(full_page=False)
            finally:
                b.close()
    lines = [x for x in (_norm(l) for l in text.splitlines()) if len(x) >= 2]
    img = Image.open(io.BytesIO(shot)).convert("L").resize((640, 512))
    return lines, np.asarray(img, dtype=float)


def recall(ref_lines: list[str], page_lines: list[str]) -> float:
    if not ref_lines:
        return 1.0
    page = "\n".join(page_lines)
    return sum(1 for l in ref_lines if l in page) / len(ref_lines)


def visual(ref, page) -> float:
    import numpy as np
    base = float(np.abs(ref - 255.0).mean())
    return 1.0 - float(np.abs(ref - page).mean()) / max(base, 1e-6)


def scores(ref_html: str, html: str) -> dict:
    ref_lines, ref_img = render(ref_html)
    lines, img = render(html)
    return {"text_visible": recall(ref_lines[0::2], lines), "text_all": recall(ref_lines, lines),
            "visual": visual(ref_img, img), "ref_lines": len(ref_lines)}


def visible_ok(s: dict) -> bool:
    return s["text_visible"] >= TEXT_MIN


def graded_ok(s: dict) -> bool:
    return s["text_all"] >= TEXT_MIN and s["visual"] >= VISUAL_MIN

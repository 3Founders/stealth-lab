"""Layout grader for the page-recreation suites (v2), after Design2Code's low-level element matching.

Why: v1 (html_grade.py) compared the first screen pixel by pixel; a page that is right but shifted a few pixels, or a
section a little taller, scored ~0.1 "visual" -- Opus passed 5 of 30 Design2Code pages. v2 compares what a person
compares: which text blocks are on the page, where, and in what colour.

Both pages are rendered by the same headless Chromium at 1280 px wide (the reference HTML too). From each render:
  * text blocks: every element's own visible text with its box (x, y, w, h over the full page) and text colour;
  * a blurred full-page thumbnail (layout and colour masses, insensitive to small shifts).
Blocks are matched one-to-one (Hungarian) on text similarity (difflib ratio >= 0.5). Scores, each 0..1:
  * block_match: matched text area / all text area, both pages (missing and extra blocks both cost);
  * text:        mean similarity of the matched pairs;
  * position:    1 - mean distance of matched block centres (x over page width, y over the reference's height);
  * color:       1 - mean RGB distance of matched blocks' text colour / max distance;
  * look:        1 - mean |ref - page| / mean |ref - white| over the blurred 64-px-wide thumbnails.
Pass rule (fixed 2026-10-14 before re-grading any answer, the same for both arms):
  block_match >= 0.8 AND text >= 0.8 AND position >= 0.85 AND color >= 0.75.
  (Set on synthetic pages only -- the reference itself, blank, text without layout, text in reverse order -- where
  position >= 0.75 still passed reversed text on a short page; no real answer had been re-graded yet.)
  `look` is reported but NOT part of the rule: on arm A's 55 answers it was 0.0 for pages scoring 1.0 on blocks, text,
  position and colour (websight-362, -236, -52: image placeholders and backgrounds drawn differently) -- the pixel
  comparison's old flaw, blurred. Removed for that defect, after seeing those answers; the four rules above are unchanged.
"""
from __future__ import annotations

import difflib
import io
import re
import tempfile
from pathlib import Path

WIDTH = 1280
BLOCK_MIN, TEXT_MIN, POS_MIN, COLOR_MIN, LOOK_MIN = 0.8, 0.8, 0.85, 0.75, 0.4

_BLOCKS_JS = r"""
() => {
  const out = [];
  const docH = Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0, 1);
  const els = document.body ? document.body.querySelectorAll('*') : [];
  for (const el of els) {
    let own = '';
    for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
    own = own.replace(/\s+/g, ' ').trim();
    if (own.length < 2) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const m = (st.color.match(/[\d.]+/g) || [0, 0, 0]).slice(0, 3).map(Number);
    out.push({t: own.toLowerCase(), x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height, c: m});
  }
  return {blocks: out, height: docH};
}
"""


def render(html: str) -> dict:
    """-> {blocks, height, thumb (numpy grayscale, 64 px wide)}."""
    from playwright.sync_api import sync_playwright
    import numpy as np
    from PIL import Image, ImageFilter
    with tempfile.TemporaryDirectory(prefix="html2_") as tmp:
        f = Path(tmp, "page.html")
        f.write_text(html, encoding="utf-8")
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": WIDTH, "height": 1024})
                pg.goto(f.as_uri(), wait_until="load", timeout=60000)
                try:
                    pg.wait_for_load_state("networkidle", timeout=5000)
                except Exception:  # noqa: BLE001 -- a page that keeps loading is graded as it is
                    pass
                info = pg.evaluate(_BLOCKS_JS)
                shot = pg.screenshot(full_page=True)
            finally:
                b.close()
    img = Image.open(io.BytesIO(shot)).convert("L")
    h = max(1, round(img.height * 64 / img.width))
    thumb = img.resize((64, h)).filter(ImageFilter.GaussianBlur(2))
    return {"blocks": info["blocks"], "height": float(info["height"]), "thumb": np.asarray(thumb, dtype=float)}


def _area(b: dict) -> float:
    return max(b["w"] * b["h"], 1.0)


def _look(ref, page) -> float:
    import numpy as np
    h = ref.shape[0]
    pg = page[:h] if page.shape[0] >= h else np.vstack([page, np.full((h - page.shape[0], page.shape[1]), 255.0)])
    base = float(np.abs(ref - 255.0).mean())
    return max(0.0, 1.0 - float(np.abs(ref - pg).mean()) / max(base, 1e-6))


def compare(ref: dict, page: dict) -> dict:
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    R, P = ref["blocks"], page["blocks"]
    look = _look(ref["thumb"], page["thumb"])
    if not R:
        return {"block_match": 1.0, "text": 1.0, "position": 1.0, "color": 1.0, "look": look, "ref_blocks": 0}
    if not P:
        return {"block_match": 0.0, "text": 0.0, "position": 0.0, "color": 0.0, "look": look, "ref_blocks": len(R)}
    sim = np.zeros((len(R), len(P)))
    for i, a in enumerate(R):
        for j, b in enumerate(P):
            sim[i, j] = difflib.SequenceMatcher(None, a["t"], b["t"]).ratio()
    rows, cols = linear_sum_assignment(-sim)
    pairs = [(i, j) for i, j in zip(rows, cols) if sim[i, j] >= 0.5]
    total = sum(_area(b) for b in R) + sum(_area(b) for b in P)
    matched = sum(_area(R[i]) + _area(P[j]) for i, j in pairs)
    if not pairs:
        return {"block_match": 0.0, "text": 0.0, "position": 0.0, "color": 0.0, "look": look, "ref_blocks": len(R)}
    H = max(ref["height"], 1.0)
    text = float(np.mean([sim[i, j] for i, j in pairs]))
    dist = [max(abs((R[i]["x"] + R[i]["w"] / 2) - (P[j]["x"] + P[j]["w"] / 2)) / WIDTH,
                abs((R[i]["y"] + R[i]["h"] / 2) - (P[j]["y"] + P[j]["h"] / 2)) / H) for i, j in pairs]
    col = [float(np.linalg.norm(np.array(R[i]["c"], float) - np.array(P[j]["c"], float))) / (255 * 3 ** 0.5) for i, j in pairs]
    return {"block_match": matched / total, "text": text, "position": max(0.0, 1.0 - float(np.mean(dist))),
            "color": max(0.0, 1.0 - float(np.mean(col))), "look": look, "ref_blocks": len(R)}


def scores(ref_html: str, html: str) -> dict:
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in compare(render(ref_html), render(html)).items()}


def graded_ok(s: dict) -> bool:
    return (s["block_match"] >= BLOCK_MIN and s["text"] >= TEXT_MIN and s["position"] >= POS_MIN
            and s["color"] >= COLOR_MIN)

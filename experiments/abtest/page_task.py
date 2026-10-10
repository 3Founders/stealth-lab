"""Page recreation as A-vs-C tasks: rebuild a web page from its screenshot (Design2Code-HARD, WebSight).

The agent sees TASK.md, screenshot.png (the page to recreate) and index.html (empty), and writes one HTML file.
Grading: html_grade.py (headless Chromium; text recall and visual closeness against the reference HTML, rendered
the same way). VISIBLE check: text recall >= 0.8 over every other reference text line; GRADE: text recall >= 0.8
over all lines AND visual closeness >= 0.5 (rules fixed before any run, the same for both arms).

Tasks: a seeded random sample, kept when the reference page has at least 5 visible text lines (so the text check
means something); no filtering on how hard a page looks. Suites: "d2c" (Design2Code-HARD, 80 real pages) and
"websight" (WebSight v0.2, synthetic Tailwind pages, one shard).
"""
from __future__ import annotations

import io
import random
import sys
from functools import lru_cache
from pathlib import Path

import html_grade

ROOT = Path(__file__).resolve().parents[1]
D2C = ROOT / "design2code" / "data" / "Design2Code-HARD"
WS = ROOT / "websight" / "data"
CHECK = "python -m pytest -q test_check.py"
MIN_LINES = 5


@lru_cache(maxsize=1)
def _websight() -> list[dict]:
    import pyarrow.parquet as pq
    f = pq.ParquetFile(WS / "train-00000.parquet")
    return next(f.iter_batches(batch_size=400, columns=["image", "text"])).to_pylist()


def reference(suite: str, key: str) -> tuple[str, bytes]:
    if suite == "d2c":
        return (D2C / f"{key}.html").read_text(encoding="utf-8", errors="replace"), (D2C / f"{key}.png").read_bytes()
    r = _websight()[int(key)]
    return r["text"], r["image"]["bytes"]


def keys(suite: str) -> list[str]:
    if suite == "d2c":
        return sorted((p.stem for p in D2C.glob("*.html")), key=lambda s: int(s[1:]))
    return [str(i) for i in range(len(_websight()))]


def sample_suite(suite: str, n: int, seed: int) -> list[dict]:
    ks = keys(suite)
    random.Random(seed).shuffle(ks)
    picked = []
    for k in ks:
        if len(picked) >= n:
            break
        try:
            lines, _ = html_grade.render(reference(suite, k)[0])
        except Exception:  # noqa: BLE001 -- a reference that does not render here is not a task
            continue
        if len(lines) >= MIN_LINES:
            picked.append(k)
    return [{"task_id": f"{suite}-{k}", "suite": suite, "workspace": suite, "page": k} for k in picked]


def prompt(task: dict) -> str:
    return ("Recreate the web page shown in screenshot.png as a single HTML file, index.html (inline CSS is fine; "
            "match its text, layout and look -- details in TASK.md). Check it with `python -m pytest -q "
            "test_check.py`.")


def files(task: dict, checker: Path) -> dict:
    _, png = reference(task["suite"], task["page"])
    md = ("# Task\n\nRecreate the page in `screenshot.png` (1280 px wide) as one self-contained HTML file, "
          "`index.html`: the same visible text, layout, colours and proportions. The check renders your page in a "
          "headless browser at 1280x1024 and compares it with the original page.\n")
    test = (f'import subprocess, sys\n\n\ndef test_check():\n'
            f'    r = subprocess.run([sys.executable, r"{checker}", "{task["suite"]}", "{task["page"]}", "index.html"],\n'
            f'                       capture_output=True, text=True, encoding="utf-8", errors="replace")\n'
            f'    print(r.stdout[-2000:])\n'
            f'    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]\n')
    return {"TASK.md": md, "screenshot.png": png, "index.html": "", "test_check.py": test}


def solution_file(task: dict) -> str:
    return "index.html"


def grade(task: dict, solution: str) -> dict:
    s = html_grade.scores(reference(task["suite"], task["page"])[0], solution)
    ok = html_grade.graded_ok(s)
    return {"resolved": ok, "check_pass": html_grade.visible_ok(s), "scores": {k: round(v, 3) for k, v in s.items()},
            "error": None if ok else f"text {s['text_all']:.2f}, visual {s['visual']:.2f}"}


if __name__ == "__main__":
    # the visible check: `python page_task.py <suite> <key> index.html` -> exit 0 when the visible text is there
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    suite, key, page = sys.argv[1], sys.argv[2], Path(sys.argv[3]).read_text(encoding="utf-8", errors="replace")
    s = html_grade.scores(reference(suite, key)[0], page)
    ok = html_grade.visible_ok(s)
    print(f"text recall (checked lines): {s['text_visible']:.2f} (needs >= {html_grade.TEXT_MIN})")
    print("CHECK PASS" if ok else "CHECK FAIL")
    sys.exit(0 if ok else 1)

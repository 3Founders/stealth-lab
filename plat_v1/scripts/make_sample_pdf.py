"""
Generate a small ruled-table PDF for the end-to-end check.

Hand-rolled rather than reportlab: the end-to-end script should not need a
dependency that nothing in the application uses, and the file it needs is a
few hundred bytes of table. Two documents with the same column layout and
different numbers are exactly the pair the layout fingerprint has to treat as
identical, so this takes the rows as an argument.

Usage:
    python scripts/make_sample_pdf.py out.pdf
"""
from __future__ import annotations

import sys
from pathlib import Path

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0

# Column left edges, in PDF points from the left margin.
COLUMN_X = [60.0, 200.0, 330.0, 410.0, 490.0]
TABLE_RIGHT = 560.0
TOP_Y = 700.0
ROW_HEIGHT = 26.0

HEADER = ["SKU", "Description", "Qty", "Unit Price", "Ordered"]

DEFAULT_ROWS = [
    ["A-1001", "Hex bolt M8", "120", "$0.42", "2026-01-04"],
    ["A-1002", "Washer 8mm", "500", "$0.06", "2026-01-04"],
    ["B-2010", "Bearing 6203", "24", "$3.75", "2026-01-11"],
    ["B-2011", "Bearing 6204", "18", "$4.10", "2026-01-11"],
    ["C-3300", "Drive belt 900mm", "6", "$27.50", "2026-02-02"],
]

# Same layout, different content. The fingerprint must not tell these apart.
VARIANT_ROWS = [
    ["Z-9001", "Copper pipe 15mm", "8", "$14.20", "2026-03-09"],
    ["Z-9002", "Elbow joint", "64", "$1.05", "2026-03-09"],
    ["Y-4410", "Solder wire", "3", "$18.99", "2026-03-15"],
    ["Y-4411", "Flux paste", "12", "$6.40", "2026-03-15"],
    ["X-7700", "Pipe cutter", "1", "$41.00", "2026-03-21"],
]


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(rows: list[list[str]]) -> bytes:
    grid = [HEADER, *rows]
    parts: list[str] = ["0.7 w"]

    bottom_y = TOP_Y - len(grid) * ROW_HEIGHT

    # Ruled lines. pdfplumber's "lines" table strategy reads these directly,
    # which is what the deterministic detector is built around -- a table
    # that has to be inferred from whitespace is the harder case, and the
    # point of this fixture is to exercise the happy path end to end.
    for index in range(len(grid) + 1):
        y = TOP_Y - index * ROW_HEIGHT
        parts.append(f"{COLUMN_X[0] - 10:.2f} {y:.2f} m {TABLE_RIGHT:.2f} {y:.2f} l S")
    for x in [COLUMN_X[0] - 10, *COLUMN_X[1:], TABLE_RIGHT]:
        parts.append(f"{x:.2f} {TOP_Y:.2f} m {x:.2f} {bottom_y:.2f} l S")

    for row_index, row in enumerate(grid):
        # Baseline sits a little above the row's lower rule.
        y = TOP_Y - (row_index + 1) * ROW_HEIGHT + 8.0
        for column_index, cell in enumerate(row):
            x = COLUMN_X[column_index]
            parts.append(
                f"BT /F1 10 Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({_escape(cell)}) Tj ET"
            )

    return "\n".join(parts).encode("latin-1")


def build_pdf(rows: list[list[str]] | None = None) -> bytes:
    stream = _content_stream(rows if rows is not None else DEFAULT_ROWS)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:.0f} {PAGE_HEIGHT:.0f}] "
            f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode("latin-1"),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_offset = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")

    return bytes(out)


def write_sample(path: str | Path, rows: list[list[str]] | None = None) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(build_pdf(rows))
    return destination


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "sample_invoice.pdf"
    written = write_sample(target)
    print(f"wrote {written} ({written.stat().st_size} bytes)")

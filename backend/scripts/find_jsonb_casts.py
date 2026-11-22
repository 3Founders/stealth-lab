"""Find call sites that pass a Python object where the SQL casts the parameter to ::jsonb.

asyncpg requires JSON text for a ::jsonb cast; a list or dict raises DataError at execute time. This scans for
`$N::jsonb` in a query string and reports the statement, so the fix can be applied everywhere at once rather than one
production failure at a time. Read-only, offline.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app"
CAST = re.compile(r"\$(\d+)::jsonb")


def main() -> None:
    for path in sorted(ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in CAST.finditer(text):
            start = text.rfind("\n", 0, max(0, m.start() - 400))
            chunk = text[max(start, 0):m.end() + 400]
            # The enclosing string literal, trimmed to one line for the report.
            line_no = text[:m.start()].count("\n") + 1
            snippet = " ".join(chunk.split())[:160]
            print(f"{path.relative_to(ROOT.parent)}:{line_no}  ${m.group(1)}::jsonb")
            print(f"    {snippet}")
            print()


if __name__ == "__main__":
    main()

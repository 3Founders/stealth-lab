"""
Task A: the "prior solved example" -- solved for real via the
iterate-until-success loop, and that real solution becomes Task B's
trajectory hint. Deliberately simple, stdlib-only (csv, json) so there's
no library-choice confound the way PDF tasks had (PyMuPDF vs PyPDF2 vs
pdfplumber vs reportlab all being valid but incompatible guesses).

Generates transactions.csv and computes the ground-truth expected
output directly from the same rules stated in the instructions --
exact-match verification, no fuzzy/OCR-tolerant comparison needed.
"""
import csv
import json
import random
from pathlib import Path

RULES = {
    "group_by": "category",
    "exclude_status": "void",
    "round_digits": 2,
}

CATEGORIES = ["groceries", "utilities", "entertainment", "transport", "healthcare"]
STATUSES = ["completed", "completed", "completed", "void", "pending"]


def generate(seed: int = 42, n_rows: int = 40) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n_rows):
        rows.append({
            "date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "category": rng.choice(CATEGORIES),
            "amount": round(rng.uniform(5, 500), 2),
            "status": rng.choice(STATUSES),
        })

    # Ground truth, computed directly from the stated rules -- this IS
    # the spec; the model's code must reproduce this exactly.
    totals: dict[str, dict] = {}
    for row in rows:
        if row["status"] == RULES["exclude_status"]:
            continue
        cat = row["category"]
        totals.setdefault(cat, {"total_amount": 0.0, "count": 0})
        totals[cat]["total_amount"] += row["amount"]
        totals[cat]["count"] += 1

    expected = {
        cat: {"total_amount": round(v["total_amount"], RULES["round_digits"]), "count": v["count"]}
        for cat, v in sorted(totals.items())  # alphabetical, per the rules
    }
    return rows, expected


def write_files(output_dir: Path, seed: int = 42) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, expected = generate(seed=seed)

    with (output_dir / "transactions.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "category", "amount", "status"])
        writer.writeheader()
        writer.writerows(rows)

    (output_dir / "expected_output.json").write_text(json.dumps(expected, indent=2))


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    write_files(out)
    print(f"wrote transactions.csv and expected_output.json to {out}")

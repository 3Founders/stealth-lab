"""
Task C: the actual test of whether retrieval + a real solved trajectory
generalizes beyond a single pair (Task A -> Task B), by trying a THIRD
instance in the same family. Same shape (group-by -> exclude-by-status
-> sum+count -> round -> sort -> JSON), genuinely different domain and
a different rounding rule (0 decimals -- distinct from Task A's 2 and
Task B's 1) so it isn't solvable by copying either prior solution
verbatim.
"""
import csv
import json
import random
from pathlib import Path

RULES = {
    "group_by": "warehouse",
    "exclude_status": "returned",
    "round_digits": 0,
}

WAREHOUSES = ["north", "south", "east", "west", "central"]
STATUSES = ["shipped", "shipped", "shipped", "returned", "pending"]


def generate(seed: int = 7, n_rows: int = 40) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n_rows):
        rows.append({
            "date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "warehouse": rng.choice(WAREHOUSES),
            "quantity": round(rng.uniform(1, 500), 2),
            "status": rng.choice(STATUSES),
        })

    totals: dict[str, dict] = {}
    for row in rows:
        if row["status"] == RULES["exclude_status"]:
            continue
        wh = row["warehouse"]
        totals.setdefault(wh, {"total_quantity": 0.0, "count": 0})
        totals[wh]["total_quantity"] += row["quantity"]
        totals[wh]["count"] += 1

    expected = {
        wh: {"total_quantity": round(v["total_quantity"], RULES["round_digits"]), "count": v["count"]}
        for wh, v in sorted(totals.items())
    }
    return rows, expected


def write_files(output_dir: Path, seed: int = 7) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, expected = generate(seed=seed)

    with (output_dir / "shipments.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "warehouse", "quantity", "status"])
        writer.writeheader()
        writer.writerows(rows)

    (output_dir / "expected_output.json").write_text(json.dumps(expected, indent=2))


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    write_files(out)
    print(f"wrote shipments.csv and expected_output.json to {out}")

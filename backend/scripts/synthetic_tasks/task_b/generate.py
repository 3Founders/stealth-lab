"""
Task B: the actual test task. Same shape as Task A (group-by -> sum +
count -> precise formatting rules -> JSON out) but a different domain
and different specific rules, so it's not solvable by literally
copy-pasting Task A's code -- genuine transfer, not memorization.
"""
import csv
import json
import random
from pathlib import Path

RULES = {
    "group_by": "event_type",
    "exclude_status": "cancelled",
    "round_digits": 1,
}

EVENT_TYPES = ["login", "purchase", "upload", "search", "export"]
STATUSES = ["completed", "completed", "completed", "cancelled", "pending"]


def generate(seed: int = 99, n_rows: int = 40) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n_rows):
        rows.append({
            "date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "event_type": rng.choice(EVENT_TYPES),
            "duration": round(rng.uniform(1, 300), 2),
            "status": rng.choice(STATUSES),
        })

    totals: dict[str, dict] = {}
    for row in rows:
        if row["status"] == RULES["exclude_status"]:
            continue
        et = row["event_type"]
        totals.setdefault(et, {"total_duration": 0.0, "count": 0})
        totals[et]["total_duration"] += row["duration"]
        totals[et]["count"] += 1

    expected = {
        et: {"total_duration": round(v["total_duration"], RULES["round_digits"]), "count": v["count"]}
        for et, v in sorted(totals.items())
    }
    return rows, expected


def write_files(output_dir: Path, seed: int = 99) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, expected = generate(seed=seed)

    with (output_dir / "activity_logs.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "event_type", "duration", "status"])
        writer.writeheader()
        writer.writerows(rows)

    (output_dir / "expected_output.json").write_text(json.dumps(expected, indent=2))


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    write_files(out)
    print(f"wrote activity_logs.csv and expected_output.json to {out}")

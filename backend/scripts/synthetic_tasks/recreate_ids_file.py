import json
from pathlib import Path

ids = {
    "trajectory_csv_groupby_aggregate": "893cdcb3-a878-4424-81c6-d32932a7f526",
    "trajectory_csv_dedupe": "0c559da2-55cd-441e-ae14-57cb26d88678",
}

out_path = Path(__file__).parent / "trajectory_library_ids.json"
out_path.write_text(json.dumps(ids, indent=2))
print(f"wrote {out_path}")
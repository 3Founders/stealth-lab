"""Run the step-0 pilot with EVERY database pointer forced at localhost.

backend/.env points DATABASE_URL / DATABASE_URL_DIRECT / CONTROL_DATABASE_URL at
production (Neon). pydantic-settings gives real env vars priority over the
dotenv file, so overriding the three here is what actually re-points the app.
This is the isolation the step-0 pilot requires, enforced from the outside
rather than trusted to the code under test.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
BACKEND = REPO / "backend"

# Load every key from backend/.env (so provider keys are present), then force
# the database pointers at the local shard.
for line in (BACKEND / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

local = os.environ["DATABASE_URL_LOCAL"]
for key in ("DATABASE_URL", "DATABASE_URL_DIRECT", "CONTROL_DATABASE_URL", "SEARCH_DATABASE_URL"):
    os.environ[key] = local

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.ingestion import traj_pilot_cli as cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(cli.main(sys.argv[1:]))

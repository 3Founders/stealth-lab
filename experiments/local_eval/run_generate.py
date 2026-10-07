"""Run experiments/swebench/generate.py for this experiment (same agent, worktrees and resumability).

    backend/.venv/Scripts/python experiments/local_eval/run_generate.py --part calibration --arm A0 --max-steps 40
    backend/.venv/Scripts/python experiments/local_eval/run_generate.py --part test --arm A0      # then L1, L2, L3

Run it with the BACKEND venv (the agent imports `app`). It selects this experiment's config and maps the General
Compute endpoint from backend/.env (GENERAL_COMPUTE_BASE_URL / GENERAL_COMPUTE_API_KEY) onto the variables
generate.py reads (EXPERIMENT_BASE_URL / EXPERIMENT_API_KEY). Values already in the environment win; nothing is
printed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAP = {"GENERAL_COMPUTE_BASE_URL": "EXPERIMENT_BASE_URL", "GENERAL_COMPUTE_API_KEY": "EXPERIMENT_API_KEY"}


def env_from_dotenv() -> dict[str, str]:
    path = ROOT / "backend" / ".env"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in MAP:
            out[MAP[key.strip()]] = value.strip().strip('"')
    return out


def main() -> int:
    env = {**os.environ, "KEL_SWEBENCH_CONFIG": str(HERE / "experiment.json"), "PYTHONUTF8": "1"}
    for k, v in env_from_dotenv().items():
        env.setdefault(k, v)
    env.setdefault("EXPERIMENT_BASE_URL", "https://api.generalcompute.com/v1")
    if not env.get("EXPERIMENT_API_KEY"):
        raise SystemExit("no EXPERIMENT_API_KEY / GENERAL_COMPUTE_API_KEY in the environment or backend/.env")
    gen = ROOT / "experiments" / "swebench" / "generate.py"
    return subprocess.call([sys.executable, str(gen), *sys.argv[1:]], cwd=gen.parent, env=env)


if __name__ == "__main__":
    sys.exit(main())

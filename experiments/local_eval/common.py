"""Paths, config and small helpers shared by the local-vs-global evaluation scripts.

Nothing here imports `app` or touches a database: the task set, the notes and the analysis are built from local
files only (the pinned SWE-rebench parquet files and the one-time corpus export).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONFIG_PATH = HERE / "experiment.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
DATA = HERE / "data"      # downloaded datasets, parsers (gitignored)
RUNS = HERE / "runs"      # instances, design, notes, attempts, grades (gitignored)
CACHE = HERE / "cache"    # repo clones (gitignored)

ARMS = ("A0", "L1", "L2", "L3")
NOTE_ARMS = ("L1", "L2", "L3")


def corpus_dir() -> Path:
    c = CONFIG["corpus"]
    return Path(os.environ.get(c["path_env"]) or (HERE / c["default_path"])).resolve()


def parquet_path(name: str, file: str) -> Path:
    return DATA / name.split("/")[1] / file


def repo_of(row_ref: str | None) -> str | None:
    """'owner__name-123' -> 'owner/name' (lowercase); None for refs that are not benchmark instance ids."""
    m = re.match(r"(.+?)__(.+)-\d+$", row_ref or "")
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def org_of(repo: str | None) -> str | None:
    return repo.split("/")[0].lower() if repo else None


def pr_number(instance_id: str | None) -> int | None:
    m = re.search(r"-(\d+)$", instance_id or "")
    return int(m.group(1)) if m else None


def stable_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def iso(ts) -> str:
    """Datasets store created_at as a timestamp or a string; compare as 'YYYY-MM-DD HH:MM:SS'."""
    return str(ts).replace("T", " ")[:19]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: max(0, n - 20)].rstrip() + "\n...[truncated]"

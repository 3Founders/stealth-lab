#!/usr/bin/env python3
"""Entry point:  python scripts/ops/ops.py <command>   (wrappers: scripts/ops/stealth-ops, stealth-ops.cmd)

Run it with the backend's Python environment (it needs asyncpg; boto3 only for object storage)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stealth_ops.cli import main  # noqa: E402

if __name__ == "__main__":
    main()

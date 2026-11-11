"""Compatibility wrapper: `search_goals.py` is the old name of `recommend_goal.py`.

The Phase 1 harness is `python scripts/recommend_goal.py "<task>"`. This file keeps the old
invocation (`python scripts/search_goals.py "<task>" --k 5 --semantic --json`) working for
anyone or any doc that still uses it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recommend_goal import main

if __name__ == "__main__":
    main()

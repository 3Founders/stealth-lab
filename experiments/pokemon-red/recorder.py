#!/usr/bin/env python3
"""
CLI shim -- all real logic lives in backend/app/experiments/pokemon_red_recorder.py
(shared with app/game_mcp/server.py's automatic call-counting hook, so the
CLI and the hook can never disagree about file formats or paths). This
file only wires sys.path so `python experiments/pokemon-red/recorder.py`
works without installing the backend package.

Usage:
    python experiments/pokemon-red/recorder.py start --condition raw --checkpoint pokemon_experiment_start_v2
    python experiments/pokemon-red/recorder.py finish --success
    python experiments/pokemon-red/recorder.py status
    python experiments/pokemon-red/recorder.py summarize
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(_BACKEND))

from app.experiments.pokemon_red_recorder import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

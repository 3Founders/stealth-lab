"""
Put the project root on sys.path so `app` and `tests` import without an
install step. The app is run from source via uvicorn, so there is no package
to install and no console script to hang a path off.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

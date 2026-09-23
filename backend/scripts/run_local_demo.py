"""Run the backend against a local, non-production database for a UI demo.

Sets DATABASE_URL / STEALTHLAB_ENV in os.environ BEFORE importing the app,
so pydantic-settings (which reads backend/.env directly) sees these real
process env vars first -- .env itself is never touched. Usage:

    DEMO_DATABASE_URL=postgresql://... python scripts/run_local_demo.py
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
os.chdir(_BACKEND_DIR)

demo_dsn = os.environ.get("DEMO_DATABASE_URL")
if not demo_dsn:
    _dsn_file = os.path.join(os.path.dirname(__file__), "..", ".demo_dsn.txt")
    if os.path.exists(_dsn_file):
        demo_dsn = open(_dsn_file, encoding="utf-8").read().strip()
if not demo_dsn:
    print("DEMO_DATABASE_URL is required (or backend/.demo_dsn.txt)", file=sys.stderr)
    sys.exit(1)

os.environ["DATABASE_URL"] = demo_dsn
os.environ["STEALTHLAB_ENV"] = "TEST"
os.environ.pop("DATABASE_URL_PREVIOUS", None)
os.environ["FRONTEND_ORIGIN"] = "http://localhost:3000,http://localhost:3001,http://localhost:3100"

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)

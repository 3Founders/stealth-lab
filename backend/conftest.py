"""
Root-level pytest config for backend/.

Excludes the manual live-DB smoke scripts (backend/test_*_live.py) from
pytest collection entirely. These are NOT part of the test suite -- they
are standalone scripts meant to be run directly (`python test_whatever_
live.py`), and several of them set a hardcoded, unconditional
os.environ["DATABASE_URL"] at module level with no restore guard. That
assignment fires the instant pytest *imports* the file to look for test
functions, even though pytest finds zero `test_*` functions inside any of
them -- and clobbers DATABASE_URL for the rest of the pytest process,
producing ~111 unrelated InvalidPasswordError failures in every *_e2e.py
module collected afterward. Different root cause from, and not covered
by, tests/conftest.py's load_dotenv guard (1897abc) -- that guard only
loads when pytest collects backend/tests/, and this leak is a hardcoded
literal, not a dotenv value.

The documented ship-checklist command (`pytest tests/`, run from backend/)
was never affected -- it only collects backend/tests/ and never touches
these root-level files. This exclusion protects the OTHER invocation shape
(a bare `pytest -q` or `pytest .` from backend/), which both CORE-A and
CORE-B hit by accident and board-flagged (build-board.md, CORE-A's
payload-cap wave note; CORE-B item 12) without fixing, since it was out
of scope for what they were each doing.
"""
collect_ignore_glob = ["test_*_live.py"]

"""
Regression test for conftest.py's pytest_configure guard against the
DATABASE_URL leak (see conftest.py's own docstring for the full story:
app.mcp_server.server's module-level load_dotenv() call used to mutate
os.environ process-wide during collection, and test_procedures_e2e.py /
test_schema_drift.py could cache that leaked value into a module-level
constant depending on collection order).

This does not re-import app.mcp_server.server (that's
test_mcp_check_procedure_offline.py's job) -- it only confirms the
mechanism conftest.py relies on: by the time any test runs,
dotenv.load_dotenv has already been wrapped so that a real .env load
(needed for other keys, e.g. STEALTHLAB_MCP_TOKEN) can never leave
DATABASE_URL behind in os.environ when it wasn't there already.
"""
import os

import dotenv


def test_load_dotenv_never_leaves_database_url_behind():
    assert "DATABASE_URL" not in os.environ, (
        "test assumes no ambient DATABASE_URL in this run -- if this fires, "
        "someone exported one by hand, which the guard deliberately leaves "
        "untouched; this test can't distinguish that from a real regression."
    )
    dotenv.load_dotenv()
    assert "DATABASE_URL" not in os.environ

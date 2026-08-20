"""
Back-compat shim (ticket 15, HTN relocation): the real implementation
moved to backend/app/execution/htn_agent.py. Every name this module used
to define is re-exported here unchanged, so the ~50 existing
`from htn_agent import X` / `import htn_agent` call sites across this
repo's tests and experiment scripts keep working without modification.

The `import *` below covers the PUBLIC names only -- it skips every
`_underscore` name, and app.execution.htn_agent defines no `__all__` to
override that. So private helpers that real call sites import must be
named explicitly; see the second import statement. This was not a
theoretical gap: `_node_row` was missing, and because a failed import is
a COLLECTION error rather than a test failure, pytest aborted the entire
run ("Interrupted: 1 error during collection") and executed zero tests.
Any future call site importing a private helper needs a line added there.

New code should import from app.execution.htn_agent directly. This
module exists for backward compatibility during and after the
relocation, not as a second place the engine's logic lives -- there is
exactly one real implementation, at the path above.

Relies on `backend` already being on sys.path by the time this shim is
imported -- true for every real call site in this repo today (confirmed
by reading each one): pytest's own rootdir insertion when tests run from
backend/, and explicit sys.path.insert(... / "backend") in every
experiment script entry point (run_graph_experiment.py,
run_symbolic_instance.py) and in app/mcp_server/server.py. Inserted here
too, defensively, so this shim also works from a caller that hasn't done
so itself.
"""
import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from app.execution.htn_agent import *  # noqa: F401,F403

# Private helpers with real call sites -- `import *` above does not
# re-export these (see docstring). Add to this line, don't create a
# second one, so there's exactly one place future additions go.
from app.execution.htn_agent import _node_row  # noqa: F401

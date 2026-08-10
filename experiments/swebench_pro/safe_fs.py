"""
Shared cleanup helper. Real motivation: an OSError("No space left on
device") occurred mid-run (graph_experiment_htn_fix_check.jsonl,
gravitational/teleport instance), and every cleanup call across this
codebase used shutil.rmtree(path, ignore_errors=True) -- meaning if
removal was silently failing somewhere over a long run (a locked file
mid-extraction is a real, known Windows failure mode this same codebase
already works around elsewhere, e.g. pro_harness.py's explicit
newline="\n" handling), there would be zero signal that anything was
wrong until disk space actually ran out.

safe_rmtree logs a failure instead of swallowing it, so a future run
can confirm or rule this out directly rather than guessing again.
"""
from __future__ import annotations

import logging
import shutil

log = logging.getLogger(__name__)


def safe_rmtree(path) -> bool:
    """Returns True if the path is now gone (already-absent counts as
    success), False if removal was attempted and failed -- logged
    loudly either way a failure occurs, not silently ignored."""
    try:
        shutil.rmtree(path, ignore_errors=False)
        return True
    except FileNotFoundError:
        return True  # already gone -- not a failure
    except OSError as exc:
        log.error("cleanup failed for %s: %s -- disk usage will NOT be freed "
                   "for this path; if this recurs, it is a real leak, not noise", path, exc)
        return False

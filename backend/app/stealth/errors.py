"""Errors for the `.stealth/` projection.

`StealthProjectionError` historically lived in
`app.execution.stealth_projection`; it is defined here now and re-exported
there so every existing `except stealth_projection.StealthProjectionError`
call site keeps working unchanged.
"""
from __future__ import annotations


class StealthProjectionError(Exception):
    """Raised when a projection cannot be generated from canonical state
    (e.g. the `procedure_run_id` does not exist). The projection is never
    written half-formed or fabricated in this case."""

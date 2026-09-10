"""Concrete behavior verifiers, imported here for their registration side
effect (`app.execution.behavior_verification.register_behavior_verifier`).

Mirrors `app.execution.implementations`' closed-registry shape: this
package is the one place that must import every concrete verifier module
so its registration actually runs. A caller that needs a specific
verifier looked up by name goes through
`app.execution.behavior_verification.get_behavior_verifier` -- it never
imports a concrete verifier module directly, so adding a new verifier
never requires touching any caller, only this package's import list.
"""
from __future__ import annotations

from app.execution.verifiers import mcp_lazy_tool_schemas as mcp_lazy_tool_schemas  # noqa: F401

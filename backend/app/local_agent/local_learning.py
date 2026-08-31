"""
Phase 12 of the product spec (personal procedure learning loop): "When a
user performs a successful, repeatable workflow: execution -> candidate
personal procedure ... either explicitly ask the user to save it; OR
automatically create a clearly marked local candidate. Do NOT
automatically publish it globally."

This is the local, offline, DB-free counterpart to
`app/mcp_server/server.py::find_best_way`'s ad-hoc-capture +
`extract_procedure()` behavior -- deliberately SMALLER, not a port. The
real server-side pipeline is LLM-assisted and DB-bound
(`app/services/procedure_extraction/__init__.py::extract_procedure()`):
it derives claims from the claims graph and calls an LLM to generalize a
procedure from multi-signal evidence. A local agent has none of that by
design (no Postgres, no claims graph, no LLM call here) -- see
`local_store.py`'s own module docstring for why. So this module does the
one thing a local run's own OWN data actually supports without
fabrication: turn the run's real step notes into a real candidate
procedure, verbatim, the same "candidate first, earn verified later"
posture `LocalProcedureStore.capture_local_procedure` already enforces
(candidate/fresh/active, provenance="system_pending_review" -- this
project's own existing convention for system/user-submitted,
not-yet-approved content, matching find_best_way's `adhoc = await
capture_procedure(..., provenance="system_pending_review", ...)` call).

SUCCESS BAR: mirrors, verbatim, the SAME real proxy `find_best_way` uses
to gate its own `extract_procedure()` call and `LocalAgentRunner.run()`
uses to gate `record_local_execution_outcome`/`report_execution`:
`graph_outcome == "success" and bool(combined_patch)`. Not a different,
locally-invented bar.

WHEN TO CALL THIS (deliberately not wired in here). This module is
new/importable only -- it does NOT touch `local_agent/runner.py`.
`maybe_capture_local_candidate` is meant to be called by
`LocalAgentRunner.run()` (or an equivalent caller) ONLY when
`LocalRunResult.matched_procedure` was `None` going in, i.e. only on the
"no_match" path where no existing local-or-global procedure was searched,
matched, and reused for this task. A run that already matched and reused
an existing procedure must NOT also spawn a redundant new candidate for
the same task -- that precondition is the caller's responsibility to
check (see each function's docstring below); this module does not and
cannot enforce it itself since it never sees the search step.

TWO MODES THE SPEC NAMES, BOTH REAL HERE.
  - auto-create: `maybe_capture_local_candidate` writes the candidate
    directly via `store.capture_local_procedure`.
  - explicit-ask: `describe_candidate_for_confirmation` builds the same
    real summary a CLI/UI layer can show a user BEFORE the store is ever
    written to, so the actual write only happens if/when the caller (not
    this module) decides to call `store.capture_local_procedure` itself
    off the back of a user's yes.
Neither path ever promotes a local candidate to the global corpus --
there is no such call in this module, on purpose (Rule 6: no implicit
private -> global promotion).
"""
from __future__ import annotations

from typing import Optional

from app.local_agent.local_store import LocalProcedureStore

# Reused, not re-invented: the exact success proxy find_best_way and
# LocalAgentRunner.run() already gate on. Documented here as named
# constants purely so a caller/test can assert against them by name
# rather than re-deriving the bar from prose.
SUCCESS_BAR_DESCRIPTION = "graph_outcome == 'success' and bool(combined_patch)"


def _is_real_success(*, run_succeeded: bool) -> bool:
    """The caller (LocalAgentRunner-shaped code) computes
    `run_succeeded = graph_result.outcome == "success" and
    bool(combined_patch)` itself, the same way find_best_way and
    LocalAgentRunner.run() already do -- this module takes that already-
    computed boolean as its own single success signal rather than
    re-deriving it from a graph_outcome string + patch text, so there is
    exactly one place in the codebase that spells out the proxy."""
    return bool(run_succeeded)


def _steps_from_node_notes(node_notes: list[str]) -> list[dict]:
    """One step per real node note, in the order the run actually
    produced them -- no fabricated steps, no re-ordering, no
    generalization. A node note has the real shape
    `"step {order} ({goal}): stop_reason=..., tool_calls=..."` (see
    `_run_local_node` / `run_node` in both find_best_way and
    LocalAgentRunner.run()); the goal is pulled back out of that note's
    own text where the shape matches, and the raw note text is kept
    verbatim as `properties.raw_note` either way so nothing is silently
    dropped even when a caller supplies notes in a different shape."""
    steps = []
    for i, note in enumerate(node_notes):
        goal = note
        if "(" in note and ")" in note:
            try:
                goal = note.split("(", 1)[1].rsplit(")", 1)[0].strip() or note
            except (IndexError, ValueError):
                goal = note
        steps.append({
            "order": i,
            "goal": goal,
            "properties": {"raw_note": note},
        })
    return steps


def maybe_capture_local_candidate(
    store: LocalProcedureStore,
    *,
    task_description: str,
    node_notes: list[str],
    files_edited: list[str],
    combined_patch: str,
    run_succeeded: bool,
    repo_root: str,
    source_episode_ids: Optional[list[str]] = None,
) -> Optional[dict]:
    """
    Capture a candidate LOCAL procedure directly from a successful ad-hoc
    run's own real data -- the local, deterministic analogue of
    find_best_way's ad-hoc `capture_procedure(...,
    provenance="system_pending_review", ...)` + later `extract_procedure`
    step, scaled down to what a DB-free local store can actually support
    (no LLM generalization, no claims graph -- see module docstring).

    PRECONDITION (caller's responsibility, not checked here): call this
    ONLY when the run that just happened did NOT match and reuse an
    existing local-or-global procedure, i.e. only where
    `LocalRunResult.matched_procedure` was `None` going into the run.
    Calling this after a matched-procedure run would create a redundant
    duplicate candidate for a task that already has a working procedure.

    Gate (mirrors find_best_way / LocalAgentRunner.run() verbatim --
    see SUCCESS_BAR_DESCRIPTION): only when `run_succeeded` is True AND
    `combined_patch` is genuinely non-empty. Returns `None` cleanly (no
    row written) when the bar isn't met -- a real, common, and
    unsurprising case (most ad-hoc runs don't produce a keepable
    procedure), not an error.

    Steps are built ONE PER `node_notes` ENTRY, in the run's own order --
    never fabricated. `files_edited` is folded into the captured
    procedure's `scope` as an observation-like note (not a step) so the
    provenance of what the procedure actually touched survives without
    inventing a step that never happened.

    Returns the same `{"id", "procedure_id"}` shape
    `capture_local_procedure` returns, or `None`.
    """
    if not _is_real_success(run_succeeded=run_succeeded):
        return None
    if not (combined_patch or "").strip():
        return None

    steps = _steps_from_node_notes(node_notes)
    name = f"local candidate: {task_description[:80]}"

    return store.capture_local_procedure(
        name=name,
        goal=task_description,
        steps=steps,
        scope={"files_edited": list(files_edited)},
        evidence_refs=[{
            "kind": "local_ad_hoc_run",
            "combined_patch_present": True,
            "files_edited": list(files_edited),
        }],
        source_episode_ids=source_episode_ids or [],
        provenance="system_pending_review",
        scope_type="repository",
        scope_entity_id=repo_root,
    )


def describe_candidate_for_confirmation(
    *,
    task_description: str,
    node_notes: list[str],
    files_edited: list[str],
    combined_patch: str,
    run_succeeded: bool,
) -> str:
    """
    Real, human-readable one-paragraph summary of what
    `maybe_capture_local_candidate` WOULD capture, for the explicit-ask
    mode the spec also names ("explicitly ask the user to save it") --
    intended for a CLI/UI layer to show a user before it decides, on the
    user's own yes, whether to call `store.capture_local_procedure`
    itself. This function never touches a store and never writes
    anything.

    Content is drawn from the actual arguments passed in (goal, real
    step count, real files touched) -- never a generic template
    independent of the run's own data. When the same success bar
    `maybe_capture_local_candidate` uses isn't met, says so plainly
    instead of describing a capture that would not happen.
    """
    if not (_is_real_success(run_succeeded=run_succeeded) and (combined_patch or "").strip()):
        return (
            f"No candidate procedure would be saved for \"{task_description}\": "
            f"the run did not meet the success bar ({SUCCESS_BAR_DESCRIPTION})."
        )

    step_count = len(node_notes)
    files_part = (
        f"{len(files_edited)} file(s) ({', '.join(files_edited)})"
        if files_edited else "no files"
    )
    return (
        f"Save a new personal procedure for \"{task_description}\"? "
        f"It would capture {step_count} step(s) from this run, touching "
        f"{files_part}, as a local candidate (provenance=system_pending_review, "
        f"scope=repository). It stays private to this workspace and is never "
        f"published globally unless separately promoted."
    )

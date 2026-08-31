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

from typing import Any, Optional

from app.execution.graph_executor import NodeResult
from app.local_agent.local_episode_evidence import (
    LocalEpisodeEvidence,
    build_local_episode_evidence,
)
from app.local_agent.local_store import LocalProcedureStore
from app.services.environment_facts import EnvironmentFact

# Reused, not re-invented: the exact success proxy find_best_way and
# LocalAgentRunner.run() already gate on. Documented here as named
# constants purely so a caller/test can assert against them by name
# rather than re-deriving the bar from prose.
SUCCESS_BAR_DESCRIPTION = "graph_outcome == 'success' and bool(combined_patch)"

# Same real, ABSTAIN-preferring precedent app/services/skill_ingestion.py's
# _abstract_capability_statement() already established for its own optional
# LLM pass -- mirrored here, not re-invented, so both call sites degrade
# identically. See _abstract_capability_statement_local()'s own docstring.
_CAPABILITY_ABSTRACTION_SYSTEM_PROMPT = """\
You are given a task description and the real steps a coding agent took to complete it.
Produce exactly one line:
CAPABILITY: <one sentence naming the general skill this represents, with NO specific file names, \
repository names, tool names, package names, command strings, or version numbers -- it must \
describe something that would apply to a DIFFERENT project doing a similar kind of work>

If you cannot produce a genuinely abstract statement, reply with exactly: ABSTAIN
"""


def _is_real_success(*, run_succeeded: bool) -> bool:
    """The caller (LocalAgentRunner-shaped code) computes
    `run_succeeded = graph_result.outcome == "success" and
    bool(combined_patch)` itself, the same way find_best_way and
    LocalAgentRunner.run() already do -- this module takes that already-
    computed boolean as its own single success signal rather than
    re-deriving it from a graph_outcome string + patch text, so there is
    exactly one place in the codebase that spells out the proxy."""
    return bool(run_succeeded)


def _steps_from_evidence(evidence: LocalEpisodeEvidence) -> list[dict]:
    """One step per real `LocalStep` in `evidence.steps` -- itself one per
    real `NodeResult`, in the run's own order (see
    local_episode_evidence.py). Each step's own real
    stop_reason/tool_calls/files_edited/patch verification lands in
    `properties.verification`/`properties` for THAT step specifically,
    not just the run's aggregate -- the richer replacement for the old
    flattened-note-only shape."""
    steps = []
    for step in evidence.steps:
        properties = dict(step.properties)
        properties["verification"] = dict(step.verification)
        steps.append({"order": step.order, "goal": step.goal, "properties": properties})
    return steps


def _abstract_capability_statement_local(
    client: Any, *, task_description: str, step_goals: list[str],
    model: str = "gemma-4-31B-it",
) -> Optional[str]:
    """Optional, injectable, LLM-assisted abstraction pass -- mirrors
    skill_ingestion.py's `_abstract_capability_statement()` ABSTAIN
    discipline verbatim (same degrade-on-anything-uncertain posture):
    returns None -- never a fabricated string -- on no client, an API
    error, an explicit ABSTAIN, or a malformed response. This module
    itself makes NO LLM call unless a caller explicitly supplies `client`;
    the default path (no client) never touches this function's body
    beyond the `client is None` check, so `maybe_capture_local_candidate`
    stays synchronous/derivation-only for every existing caller."""
    if client is None:
        return None
    user_prompt = (
        f"Task: {task_description}\n"
        "Steps taken:\n" + "\n".join(f"- {g}" for g in step_goals)
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CAPABILITY_ABSTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=160,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 -- a model call's own failure degrades to None, never raises
        return None
    if text == "ABSTAIN":
        return None
    capability: Optional[str] = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("CAPABILITY:"):
            capability = line[len("CAPABILITY:"):].strip()
            break
    return capability or None


def maybe_capture_local_candidate(
    store: LocalProcedureStore,
    *,
    task_description: str,
    node_notes: list[str],
    files_edited: list[str],
    combined_patch: str,
    run_succeeded: bool,
    repo_root: str,
    node_results: Optional[dict[int, NodeResult]] = None,
    environment_facts: Optional[list[EnvironmentFact]] = None,
    source_episode_ids: Optional[list[str]] = None,
    client: Optional[Any] = None,
    embedding: Optional[list[float]] = None,
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

    Steps are built ONE PER real `NodeResult` (via
    `local_episode_evidence.build_local_episode_evidence`), in the run's
    own order -- never fabricated, never re-ordered. Each step carries its
    OWN real verification (`declared_finished`, `produced_patch`) and its
    own `files_edited`/`patch_present`/`tool_calls` -- richer than the
    prior flattened-note-per-step shape, still never inventing anything
    the run itself didn't produce. `node_results`/`environment_facts` are
    optional: when omitted (the pre-existing call shape), steps still
    build correctly from `node_notes` alone with each step's per-node
    verification honestly defaulted to "no richer signal available"
    rather than fabricated as true.

    `files_edited` lands in the captured procedure's `scope` (as before)
    AND the run's real probed `environment_facts`, if supplied, are
    folded into `scope["environment"]` as informational context ONLY --
    this function deliberately never synthesizes a formal invariant
    expression (e.g. "pandas_version >= X") from a single run's observed
    environment values; `invariants` stays `[]` (see
    local_episode_evidence.py's own docstring for why this is a
    deliberate, documented scope limit, not an oversight).

    `client`, if supplied, is used for ONE optional, injectable,
    ABSTAIN-preferring capability-abstraction call
    (`_abstract_capability_statement_local`) whose result lands in
    `scope["capability_statement"]` when it succeeds; this function makes
    NO LLM call itself when `client` is omitted (the default), and never
    fabricates a placeholder statement when the call abstains or fails.

    `embedding`, if supplied, is forwarded verbatim to
    `store.capture_local_procedure` -- this function never computes one
    itself (no network call belongs in a pure capture path). Omitted
    (the default, unchanged from before this parameter existed): the
    captured row's `embedding` column stays NULL, same as always. A
    caller that already has a real embedding of `task_description` in
    hand (e.g. `LocalAgentRunner.run()`, which already computes one for
    its own search step) should pass it here so the candidate this
    function writes is findable by real semantic similarity later, not
    just by lexical substring match -- `search_local_procedures` already
    supports ranking by a stored embedding, this was simply never given
    one on the ad-hoc-capture path.

    Returns the same `{"id", "procedure_id"}` shape
    `capture_local_procedure` returns, or `None`.
    """
    if not _is_real_success(run_succeeded=run_succeeded):
        return None
    if not (combined_patch or "").strip():
        return None

    evidence = build_local_episode_evidence(
        task_description=task_description,
        node_notes=node_notes,
        node_results=node_results or {},
        files_edited=files_edited,
        combined_patch=combined_patch,
        run_succeeded=run_succeeded,
        environment_facts=environment_facts,
    )

    steps = _steps_from_evidence(evidence)
    name = f"local candidate: {task_description[:80]}"

    scope: dict = {"files_edited": list(files_edited)}
    if evidence.environment:
        scope["environment"] = evidence.environment

    capability_statement = _abstract_capability_statement_local(
        client, task_description=task_description,
        step_goals=[s["goal"] for s in steps],
    )
    if capability_statement:
        scope["capability_statement"] = capability_statement

    evidence_ref = dict(evidence.evidence_ref)
    evidence_ref["declared_success"] = evidence.verification["declared_success"]
    evidence_ref["verified_success"] = evidence.verification["verified_success"]

    return store.capture_local_procedure(
        name=name,
        goal=task_description,
        steps=steps,
        scope=scope,
        evidence_refs=[evidence_ref],
        source_episode_ids=source_episode_ids or [],
        provenance="system_pending_review",
        scope_type="repository",
        scope_entity_id=repo_root,
        embedding=embedding,
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

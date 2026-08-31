"""
Multi-episode procedure generalization (V1 product spec, P0). Produces ONE
synthesized `procedures` row from SEVERAL real, compatible, individually-
successful episodes -- the capability `extract_procedure()` deliberately
does not have (its own module docstring: "ONE EXTRACTION PER EPISODE, not
per claim" -- confirmed unchanged this session by re-reading
procedure_extraction/__init__.py and grepping this package for any
cross-episode synthesis logic; none existed before this file).

INTEGRATION POINT, decided and justified: this module builds on
`episode_evidence.build_episode_evidence()` (one `EpisodeEvidence` per
candidate episode id), NOT on `extract_procedure(..., dry_run=True)`.
Two real reasons, not a style preference:

  1. `extract_procedure()` requires a caller-supplied `goal_text` and
     `outcome` per episode (evidence.py's own documented scope: those are
     "CALLER-SUPPLIED context, not derived from the database"). A
     synthesis caller handing over a batch of candidate episode ids
     picked by embedding similarity does not, in general, have a hand-
     authored goal/outcome pair for each one ready to supply -- forcing
     one would mean inventing it, exactly what this whole build refuses
     to do elsewhere. `build_episode_evidence()` needs only a real
     episode id and is fully DB-derived (declared_goal/observed_goal_
     signals/verification all honestly computed from real rows).
  2. `EpisodeEvidence` already carries the one real signal per-episode
     "outcome" would otherwise have to fake: `verification` (built only
     from real `test_run` observations) and `failures_retries` (built
     only from observations that explicitly record a failure). Episode-
     level "did this succeed" is used HERE as "did this episode record
     no failure" (`_has_recorded_failure` below) -- a real, inspectable
     read of the episode's own observations, not a caller's unverifiable
     assertion.

  This module still runs every candidate episode's evidence through
  `derive.py`'s existing derivation functions (`derive_preconditions`,
  `derive_scope`, `derive_step_skeleton`, `derive_slots`,
  `derive_failure_conditions`) after adapting `EpisodeEvidence` to the
  flat `ProcedureEvidence` shape those functions already consume (see
  `_adapt_to_procedure_evidence` below) -- so every per-episode
  precondition/scope/step/slot/failure-condition computed here is the
  EXACT SAME real derivation `DeterministicExtractor`/
  `GroundedHybridExtractor` already use for one episode, not a second,
  parallel implementation. `synthesize_procedure()`'s own job, on top of
  that, is genuinely new: comparing, aligning, and generalizing several
  episodes' worth of that derived data, then capturing ONE result via
  `capture_procedure()` -- reused unchanged, exactly like
  `extract_procedure()` reuses it.

COMPATIBILITY CHECK, three real, inspectable gates, ALL must pass before
any merging is attempted (short-circuits at the first one that fails,
returning a `SynthesisResult(synthesized=False, refusal_reason=...)`
naming exactly which episodes and which fact disagreed):

  (a) Every contributing episode must carry NO recorded failure signal
      (`EpisodeEvidence.failures_retries` empty) -- a real, observation-
      grounded stand-in for "this episode succeeded", not a caller's
      unverifiable label.
  (b) Every contributing episode must agree on whether real verification
      evidence exists at all (`EpisodeEvidence.verification is not
      None`) -- "both used a real test_run, or neither", per this
      module's own brief.
  (c) STRUCTURAL alignment over each episode's real tool-call skeleton
      (`derive_step_skeleton`'s own `StepGroup` list, tool names only,
      counts dropped): pairwise normalized edit-distance similarity,
      clustered via `dedup.py::complete_linkage_clusters` -- the SAME
      complete-linkage primitive `find_duplicate_clusters` already uses
      for procedures/task_nodes/knowledge_nodes, reused rather than
      reinvented, and for the SAME reason that module's own docstring
      gives: naive transitive union-find lets A~B~C~D cluster even when
      A and D are not themselves similar. An episode whose tool-call
      PATTERN does not align with the rest lands in its own cluster and
      the whole batch is refused, naming that episode. This is real
      structural comparison, not embedding similarity re-labeled --
      embedding similarity is used ONLY as an upstream candidate filter
      by whatever caller assembled `episode_ids` in the first place
      (this module does not do that filtering itself; see "What this
      does NOT do" below).
  (d) PREDICATE CONTRADICTION: every real precondition this module
      derives (via `derive_preconditions`, which is itself already
      restricted to `environment_probe.PROBE_PREDICATE_VOCABULARY` by
      construction) is grouped by predicate NAME across all episodes,
      ignoring subject (subjects are per-project -- `project:<id>` --
      and legitimately differ even for compatible episodes). If any
      predicate has MORE THAN ONE distinct real object value across the
      set (e.g. `language=python` in one episode, `language=javascript`
      in another), that is a genuine contradiction -- refused, not
      averaged or silently dropped. This is the "pandas<2.0 vs
      pandas>=2.0" case from the brief, expressed in this codebase's own
      closed predicate vocabulary rather than invented as a separate
      mechanism.

ALIGNMENT + GENERALIZATION, once compatibility holds:

  - STEPS: the richest (longest) contributing episode's own
    `derive_step_skeleton()` output becomes the merged step backbone,
    turned into `ProcedureStep`s via `literal_steps_from_skeleton()` --
    REUSED, not reimplemented. Real, stated limitation: this is a
    conservative backbone choice, not a true multi-sequence alignment
    (no attempt to interleave/reorder steps that appear in a different
    relative position across episodes) -- see "What this does NOT do".
  - PRECONDITIONS: the (subject, predicate, object) triples common to
    EVERY contributing episode, by exact intersection -- NOT a
    generalized/templated subject. `applicability.py::check_hard_
    constraints()` looks up `project_state(subject=...)` by the LITERAL
    subject string at reuse time (confirmed by reading that function),
    so inventing a synthetic subject would make the gate permanently
    unsatisfiable -- exactly the failure mode V1 (precondition
    groundedness) exists to prevent, just not one V1's own check (which
    only validates predicate names) would catch. Keeping only literally-
    identical triples is the honest choice: a precondition that held for
    every contributing episode's own project is real; one that held for
    only some is correctly dropped, not weakened into something
    unenforceable.
  - SCOPE: `derive_scope()`'s `language` narrowing signal is UNIONED
    (not intersected) across episodes -- scope is `applicability.py`'s
    explicitly SOFT narrowing layer (derive.py's own docstring: "a real
    narrowing signal... not a disqualifying precondition"), so a
    procedure demonstrated to work across two languages honestly stays
    offered to projects using either, unlike a hard-gated precondition.
  - SLOTS: `derive_slots()` is run per episode (same `repo_root` for
    all -- see limitation below); if every episode resolves to the SAME
    binder name (including `'literal'` as a valid uniform choice), the
    merged procedure gets exactly ONE generalized `SlotSpec` naming that
    binder, with a GENERIC description that never repeats a literal
    per-episode file path -- this is the concrete mechanism for "avoid
    leaking source-specific paths into abstract text" applied to slots,
    not just to `capability_statement`. If episodes resolve to DIFFERENT
    binders, that is treated as a real structural disagreement (a
    different method for finding what to edit) and synthesis refuses.
  - CAPABILITY_STATEMENT: built DETERMINISTICALLY from the merged step
    backbone's tool names through a fixed, hand-written phrase table
    (`_GENERIC_ACTION_PHRASES`) -- no model call, no per-episode text
    ever enters this field. This guarantees V4 (capability abstraction)
    passes BY CONSTRUCTION, the same discipline derive.py's own
    docstring establishes for preconditions: "this file is what makes
    [inventing an unmatchable precondition] unnecessary" -- here it is
    what makes leaking a source-specific token into the one field that
    gets embedded for cross-domain retrieval structurally impossible,
    not just validator-checked after the fact.
  - GOAL/NAME: built from each episode's own `declared_goal`/
    `observed_goal_signals` (literal, informative) -- `goal` is NOT
    scanned by V4 (only `capability_statement` is; confirmed by reading
    validators.py), so keeping it literal matches `DeterministicExtractor`'s
    own existing precedent (`goal=evidence.goal_text`, unabridged).

PROVENANCE: `source_episode_ids` names every contributing episode, full
stop -- never narrowed to a "representative" subset. `evidence_refs`
carries each episode's own real provenance chain (`EpisodeEvidence.
provenance` -- source_event_ids, observation_count) so a reviewer can
trace the synthesized procedure back to every real observation it rests
on. `family_id`: if any LIVE procedure already carries one of these
episode ids in ITS OWN `source_episode_ids` (a prior single-episode
extraction from the same episode, or an earlier synthesis sharing a
member), the earliest such row's id becomes this procedure's
`family_id` -- `procedures.family_id` is a real, existing, self-
referencing column (db/18_procedures.sql) with no other real writer
today (grepped this session); this is the honest, narrow use of it this
pass makes: grouping a synthesized procedure with whatever concrete,
single-episode procedure(s) already exist over the same real evidence,
not inventing a second family/variant table.

CONTRADICTION HANDLING -- the choice made and why: (a) REFUSE, returning
`SynthesisResult(synthesized=False, refusal_reason=...)`, never (b)
silently producing two linked variant procedures. Reasoning: this
module's compatibility gate already tells you EXACTLY which fact
disagreed (a named predicate, or which episode's tool-call pattern did
not align) -- a human or a later, deliberate pass can decide whether
that is "two real variants worth keeping side by side" or "these were
never the same task to begin with". Silently emitting two procedures
today would mean inventing that judgment call without evidence this
pass has any way to make correctly, and would require a second, new
convention for what "linked variants" means beyond what `family_id`
already expresses for the compatible case above -- exactly the kind of
preemptive second mechanism the codebase's dedup precedent
(`dedup.py`'s own docstring: "borderline merges are NOT auto-applied...
a cluster is a candidate for merge") argues against building ahead of a
real need. `merge_duplicate_procedures()`'s own contract (returning
`None` on an ambiguous/already-resolved state rather than guessing) is
the same discipline, reused in spirit here.

TRUST LEVEL: a synthesized procedure is captured via the SAME
`capture_procedure()` every other producer uses, which always starts a
new row `verification_state='candidate'` (db/18_procedures.sql's own
default) -- ticket 13's promotion threshold (>=10 successes, 0
failures, across >=3 distinct contexts, `procedures.py::
record_execution_outcome`) is untouched and unshortcut by this module.
Being synthesized from N prior successful episodes does NOT grant a
head start on that threshold, does NOT set `verification_state=
'verified'`, and does NOT skip `approval_status` (stamped 'proposed',
exactly like `extract_procedure()`'s own follow-up UPDATE) -- a
synthesized procedure is a CANDIDATE like any other, gated by
`applicability.py`'s existing approval/verification checks before it is
ever offered for reuse.

WHAT THIS DOES NOT DO, stated rather than implied:
  - No embedding-based candidate filtering lives in this module --
    `episode_ids` is assumed to already be a reasonably-related
    candidate set (a caller's job, e.g. via an episode-level embedding
    search this pass does not build). This module's own compatibility
    gates are what decide merge-or-refuse; embeddings, per the brief,
    are never treated as sufficient proof of equivalence here.
  - No true multi-sequence alignment: the merged step backbone is the
    single richest episode's own skeleton, not an interleaved consensus
    structure. A reordering of the SAME steps across episodes (B, A, C
    vs A, B, C) will show up as a low edit-distance similarity and get
    refused as "incompatible", even though a smarter aligner might
    recognize it as the same method in a different order.
  - No support for more than a small number of episodes in one call --
    the tool-sequence clustering is O(n^2) (same complexity dedup.py's
    own `complete_linkage_clusters` already accepts at its own scale)
    and every derivation function makes at least one DB round trip per
    episode; this is fine at single-digit N, not designed or tested
    beyond it.
  - No cross-repo_root synthesis: one `repo_root` is accepted for the
    WHOLE batch (passed through to every episode's `derive_slots()`
    call) -- episodes whose code actually lives in different checkouts
    cannot get a correct binder-coverage computation from this pass.
  - No numeric invariant synthesis: `invariants` is always `[]` on the
    synthesized procedure (neither `derive.py` nor either extraction
    strategy ever populates it today, confirmed by reading both this
    session -- this module inherits that same honest gap, does not
    attempt to invent one).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import asyncpg

from app.services.dedup import complete_linkage_clusters
from app.services.procedure_extraction.derive import (
    derive_failure_conditions,
    derive_preconditions,
    derive_scope,
    derive_slots,
    derive_step_skeleton,
    literal_steps_from_skeleton,
)
from app.services.procedure_extraction.episode_evidence import (
    EpisodeEvidence,
    build_episode_evidence,
)
from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.schema import (
    ExtractedProcedure,
    Predicate,
    SlotSpec,
)
from app.services.procedure_extraction.validators import ValidationContext, validate
from app.services.procedures import capture_procedure

SYNTHESIS_TAG = "multi_episode_synthesis_v1@1"

# Conservative on purpose: two episodes whose tool-call PATTERN differs by
# more than ~40% of its length are treated as different methods, not the
# same method with minor variation. Configuration, not a literal buried in
# logic -- same discipline procedures.py's own ticket-13 constants use.
TOOL_SEQUENCE_SIMILARITY_THRESHOLD = 0.6

MIN_CANDIDATE_EPISODES = 2

# Deterministic, fixed vocabulary only -- see module docstring's
# CAPABILITY_STATEMENT section for why this table, not a model call, is
# what makes V4 (capability abstraction) unable to fail structurally.
_GENERIC_ACTION_PHRASES: dict[str, str] = {
    "Read": "inspect the relevant files",
    "Grep": "search the codebase for relevant context",
    "Glob": "search the codebase for relevant context",
    "Edit": "apply a targeted code change",
    "MultiEdit": "apply a targeted code change",
    "Write": "apply a targeted code change",
    "NotebookEdit": "apply a targeted code change",
    "Bash": "run a supporting shell command",
    "TodoWrite": "track the work as a checklist",
}


@dataclass
class SynthesisResult:
    """Mirrors `ExtractionResult`'s own contract (a structured result,
    never a bare Optional): `synthesized` distinguishes a real refusal
    (expected, not an error -- an incompatible batch) from a genuine
    persisted result, the same way `ExtractionResult.succeeded` does for
    single-episode extraction."""
    synthesized: bool
    procedure_id: Optional[str] = None
    version_row_id: Optional[str] = None
    extracted: Optional[ExtractedProcedure] = None
    contributing_episode_ids: list[str] = field(default_factory=list)
    refusal_reason: Optional[str] = None
    validation_failures: list[str] = field(default_factory=list)


def _adapt_to_procedure_evidence(ev: EpisodeEvidence) -> ProcedureEvidence:
    """The one seam that lets this module reuse derive.py's real
    derivation functions unchanged: `EpisodeEvidence` (rich, per-episode)
    -> `ProcedureEvidence` (flat, what derive.py actually consumes).
    `started_at` uses the episode's own first real observation's
    `extracted_at` -- an honest proxy (EpisodeEvidence carries no
    dedicated episode-start timestamp), same "approximate, labeled as
    such" discipline `episode_evidence.py`'s own `StateSnapshot` uses.
    `outcome` is always 'success' here -- by the time this function runs,
    the caller (`synthesize_procedure` below) has already refused any
    episode carrying a recorded failure signal, so this is a real,
    checked fact, not an assumption."""
    started_at = ev.initial_state.extracted_at if ev.initial_state else None
    goal_text = (
        ev.declared_goal
        or (ev.observed_goal_signals[0] if ev.observed_goal_signals else None)
        or f"episode {ev.episode_id}"
    )
    return ProcedureEvidence(
        goal_text=goal_text, outcome="success",
        observations=ev.observations, tool_sequence=ev.tool_calls,
        started_at=started_at, project_id=ev.project_id,
        episode_id=ev.episode_id, session_id=ev.session_id,
    )


def _has_recorded_failure(ev: EpisodeEvidence) -> bool:
    """Real, observation-grounded stand-in for episode-level 'outcome'
    (episodes carry no such column -- confirmed against db/01_ontology.sql
    this session). `failures_retries` is already built by
    `build_episode_evidence()` from explicit failure signals only
    (a `test_run` with `passed is False`, a `command_executed` with a
    recorded nonzero exit) -- reused here, not recomputed."""
    return bool(ev.failures_retries)


def _levenshtein(a: list[str], b: list[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def _tool_sequence_similarity(a: list[str], b: list[str]) -> float:
    """Normalized edit-distance similarity over ORDERED tool-name
    sequences (skeleton counts dropped -- "Read x3" and "Read x1" are the
    same structural step). 1.0 for two empty sequences (vacuously
    identical); 0.0 whenever one side is empty and the other isn't."""
    if not a and not b:
        return 1.0
    dist = _levenshtein(a, b)
    return 1 - dist / max(len(a), len(b))


def _find_predicate_contradiction(
    preconditions_by_episode: dict[str, list[Predicate]],
) -> Optional[str]:
    """Real contradiction test over derive.py's own closed-vocabulary
    preconditions (see module docstring, gate (d)): grouped by predicate
    NAME (subject deliberately ignored -- subjects are per-project and
    differ legitimately), any predicate carrying more than one distinct
    real object value across the contributing episodes is a genuine
    disagreement about the environment these episodes ran in, refused
    rather than merged or averaged."""
    by_predicate: dict[str, dict[Optional[str], list[str]]] = {}
    for episode_id, preds in preconditions_by_episode.items():
        for p in preds:
            by_predicate.setdefault(p.predicate, {}).setdefault(p.object, []).append(episode_id)
    for predicate, by_object in by_predicate.items():
        if len(by_object) > 1:
            return (
                f"contradictory precondition across episodes: predicate {predicate!r} has "
                f"different real values {dict(by_object)} -- refusing to merge into one "
                f"falsely-universal procedure"
            )
    return None


def _intersect_predicates(
    preconditions_by_episode: dict[str, list[Predicate]],
) -> list[Predicate]:
    """Exact (subject, predicate, object) intersection -- see module
    docstring's PRECONDITIONS section for why the subject is never
    generalized/templated: applicability.py's check_hard_constraints()
    looks claims up by the LITERAL subject at reuse time, so only a
    triple that held for EVERY contributing episode's own real project
    survives onto the synthesized procedure."""
    sets = [
        {(p.subject, p.predicate, p.object) for p in preds}
        for preds in preconditions_by_episode.values()
    ]
    if not sets:
        return []
    common = set.intersection(*sets)
    return [
        Predicate(subject=s, predicate=pred, object=obj)
        for s, pred, obj in sorted(common, key=lambda t: (t[0] or "", t[1], t[2] or ""))
    ]


def _union_scope(scopes: list[dict]) -> dict:
    """UNION, not intersection -- scope is applicability.py's explicitly
    SOFT narrowing layer (derive.py's own docstring), unlike hard-gated
    preconditions, so a procedure demonstrated to work across two
    languages honestly stays offered to projects using either."""
    languages: list[str] = []
    for scope in scopes:
        for lang in scope.get("language") or []:
            if lang not in languages:
                languages.append(lang)
    return {"language": languages} if languages else {}


def _merge_slots(
    slots_by_episode: dict[str, list[SlotSpec]],
) -> tuple[Optional[str], list[SlotSpec]]:
    """One generalized slot per shared binder, NEVER a concatenation of
    per-episode literal descriptions -- this is the mechanism for
    avoiding leaked source-specific paths in the (very common, when no
    repo_root is available) case where derive_slots() itself falls back
    to a literal SlotSpec whose `description` names a real file path per
    episode. Returns (refusal_reason_or_None, merged_slots)."""
    non_empty = {eid: slots for eid, slots in slots_by_episode.items() if slots}
    if not non_empty:
        return None, []
    binder_names: set[str] = set()
    for slots in non_empty.values():
        binder_names.update(s.binder for s in slots)
    if len(binder_names) > 1:
        return (
            f"episodes resolve file-selection to different binders "
            f"{sorted(binder_names)} -- a real structural disagreement about HOW the "
            f"target files were found, refusing to merge into one slot",
            [],
        )
    binder_name = next(iter(binder_names))
    merged = [
        SlotSpec(
            name="target_files",
            binder=binder_name,
            description=(
                f"files this procedure edits, bound via {binder_name} at instantiation "
                f"time -- generalized across {len(non_empty)} contributing episodes; no "
                f"literal per-episode path is retained here"
            ),
        )
    ]
    return None, merged


def _build_capability_statement(tool_names: list[str], *, n_episodes: int) -> str:
    """Deterministic by construction -- see module docstring's
    CAPABILITY_STATEMENT section for why this, not a model call, is what
    makes V4 unable to fail structurally: every phrase in
    `_GENERIC_ACTION_PHRASES` is a fixed string, so nothing episode-
    specific can ever reach this field."""
    phrases: list[str] = []
    for name in tool_names:
        phrase = _GENERIC_ACTION_PHRASES.get(name, f"perform a supporting {name} action")
        if not phrases or phrases[-1] != phrase:
            phrases.append(phrase)
    body = "; then ".join(phrases) if phrases else "perform the shared procedure pattern"
    return (
        f"A generalized method observed consistently across {n_episodes} independent "
        f"episodes: {body}."
    )


def _build_goal(evidences: dict[str, EpisodeEvidence]) -> str:
    """Literal, deliberately -- `goal` is never scanned by V4 (confirmed
    by reading validators.py: only capability_statement is), so this
    matches DeterministicExtractor's own precedent of keeping `goal`
    literal and informative rather than abstracted."""
    labels = []
    for episode_id, ev in evidences.items():
        label = ev.declared_goal or (
            ev.observed_goal_signals[0] if ev.observed_goal_signals else None
        ) or f"episode {episode_id[:8]}"
        labels.append(label)
    return f"Generalized procedure synthesized from {len(evidences)} compatible episodes: " + "; ".join(labels)


async def _find_family_id(pool: asyncpg.Pool, episode_ids: list[str]) -> Optional[str]:
    """Narrow, real use of `procedures.family_id` (see module docstring's
    PROVENANCE section): links this synthesis to the EARLIEST already-
    live procedure whose own source_episode_ids overlaps this batch, if
    one exists. Never fabricates a family relationship where none is
    demonstrated by shared real evidence."""
    row = await pool.fetchrow(
        "SELECT id FROM procedures WHERE t_invalid IS NULL "
        "AND source_episode_ids && $1::uuid[] "
        "ORDER BY t_created ASC LIMIT 1",
        episode_ids,
    )
    return str(row["id"]) if row else None


async def synthesize_procedure(
    pool: asyncpg.Pool,
    episode_ids: list[str],
    *,
    repo_root: Optional[str] = None,
    entry_seed_files_by_episode: Optional[dict[str, list[str]]] = None,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    dry_run: bool = False,
) -> SynthesisResult:
    """
    The public entry point (this package's new `synthesize_procedure()`,
    parallel to `__init__.py::extract_procedure()`, kept in its own
    module rather than added to that file so single-episode extraction's
    own code path is untouched by this pass).

    `dry_run=True` mirrors `extract_procedure()`'s own contract: runs
    every gate and the full merge, returns the candidate WITHOUT calling
    `capture_procedure()`.
    """
    episode_ids = list(dict.fromkeys(episode_ids))  # de-dup, order-preserving
    if len(episode_ids) < MIN_CANDIDATE_EPISODES:
        return SynthesisResult(
            False, contributing_episode_ids=episode_ids,
            refusal_reason=(
                f"need at least {MIN_CANDIDATE_EPISODES} candidate episodes to synthesize, "
                f"got {len(episode_ids)}"
            ),
        )

    evidences: dict[str, EpisodeEvidence] = {}
    for episode_id in episode_ids:
        evidences[episode_id] = await build_episode_evidence(pool, episode_id, repo_root=repo_root)

    # --- gate (a): no contributing episode may carry a recorded failure ---
    failed = [eid for eid, ev in evidences.items() if _has_recorded_failure(ev)]
    if failed:
        return SynthesisResult(
            False, contributing_episode_ids=episode_ids,
            refusal_reason=(
                f"episodes {failed} carry a real recorded failure signal -- synthesis "
                f"inputs must be demonstrated successes, not asserted ones"
            ),
        )

    # --- gate (b): verification-signal agreement ---
    has_verification = {eid: ev.verification is not None for eid, ev in evidences.items()}
    if len(set(has_verification.values())) > 1:
        return SynthesisResult(
            False, contributing_episode_ids=episode_ids,
            refusal_reason=(
                f"episodes disagree on whether real test_run verification evidence "
                f"exists: {has_verification} -- both or neither is required"
            ),
        )

    proc_evidences = {eid: _adapt_to_procedure_evidence(ev) for eid, ev in evidences.items()}

    # --- gate (c): structural tool-sequence alignment, complete-linkage ---
    tool_names = {
        eid: [g.tool_name for g in derive_step_skeleton(pe)]
        for eid, pe in proc_evidences.items()
    }

    def _sim(a: str, b: str) -> float:
        return _tool_sequence_similarity(tool_names[a], tool_names[b])

    clusters = complete_linkage_clusters(episode_ids, _sim, TOOL_SEQUENCE_SIMILARITY_THRESHOLD)
    main_cluster = max(clusters, key=len)
    if len(main_cluster) < len(episode_ids):
        outliers = [e for e in episode_ids if e not in main_cluster]
        return SynthesisResult(
            False, contributing_episode_ids=episode_ids,
            refusal_reason=(
                f"episodes {outliers} do not structurally align with the rest (tool-call "
                f"pattern similarity below {TOOL_SEQUENCE_SIMILARITY_THRESHOLD}) -- refusing "
                f"to merge materially different methods into one falsely-universal procedure"
            ),
        )

    # --- gate (d): predicate contradiction over derived preconditions ---
    preconditions_by_episode: dict[str, list[Predicate]] = {}
    for eid, pe in proc_evidences.items():
        preconditions_by_episode[eid] = await derive_preconditions(pool, pe)
    contradiction = _find_predicate_contradiction(preconditions_by_episode)
    if contradiction:
        return SynthesisResult(False, contributing_episode_ids=episode_ids, refusal_reason=contradiction)

    # --- ALIGNMENT + GENERALIZATION (compatibility established) ---
    merged_preconditions = _intersect_predicates(preconditions_by_episode)

    scopes = [await derive_scope(pool, pe) for pe in proc_evidences.values()]
    merged_scope = _union_scope(scopes)

    seed_map = entry_seed_files_by_episode or {}
    slots_by_episode: dict[str, list[SlotSpec]] = {}
    for eid, pe in proc_evidences.items():
        slots_by_episode[eid] = derive_slots(
            pe, repo_root=repo_root, entry_seed_files=seed_map.get(eid, []),
        )
    slot_refusal, merged_slots = _merge_slots(slots_by_episode)
    if slot_refusal:
        return SynthesisResult(False, contributing_episode_ids=episode_ids, refusal_reason=slot_refusal)

    skeletons = {eid: derive_step_skeleton(pe) for eid, pe in proc_evidences.items()}
    reference_id = max(skeletons, key=lambda eid: len(skeletons[eid]))
    backbone = skeletons[reference_id]
    if not backbone:
        return SynthesisResult(
            False, contributing_episode_ids=episode_ids,
            refusal_reason="no episode contributed a non-empty tool-call skeleton -- nothing to synthesize",
        )
    steps = literal_steps_from_skeleton(backbone)

    failure_conditions: list[str] = []
    for pe in proc_evidences.values():
        for fc in derive_failure_conditions(pe):
            if fc not in failure_conditions:
                failure_conditions.append(fc)

    capability_statement = _build_capability_statement(
        [g.tool_name for g in backbone], n_episodes=len(episode_ids),
    )
    goal = _build_goal(evidences)
    name = goal[:100]

    extracted = ExtractedProcedure(
        name=name, goal=goal, capability_statement=capability_statement,
        steps=steps, slots=merged_slots, preconditions=merged_preconditions,
        scope=merged_scope, failure_conditions=failure_conditions, invariants=[],
    )

    from app.services.environment_probe import PROBE_PREDICATE_VOCABULARY
    from app.services.procedure_extraction import _evidence_tokens

    evidence_tokens: frozenset[str] = frozenset()
    for pe in proc_evidences.values():
        evidence_tokens = evidence_tokens | _evidence_tokens(pe)
    allowed_binders = frozenset({s.binder for s in merged_slots}) | frozenset({"literal"})
    ctx = ValidationContext(
        probe_vocabulary=PROBE_PREDICATE_VOCABULARY,
        evidence_tokens=evidence_tokens, allowed_binders=allowed_binders,
    )
    failures = validate(extracted, ctx)
    if failures:
        return SynthesisResult(
            False, extracted=extracted, contributing_episode_ids=episode_ids,
            validation_failures=[str(f) for f in failures],
            refusal_reason="synthesized procedure failed the shared extraction validators",
        )

    if dry_run:
        return SynthesisResult(True, extracted=extracted, contributing_episode_ids=episode_ids)

    family_id = await _find_family_id(pool, episode_ids)
    evidence_refs = [
        {
            "episode_id": eid,
            "source_event_ids": evidences[eid].provenance["source_event_ids"],
            "observation_count": evidences[eid].provenance["observation_count"],
            "extractor_versions": evidences[eid].provenance["extractor_versions"],
        }
        for eid in episode_ids
    ]

    project_ids = {ev.project_id for ev in evidences.values() if ev.project_id}
    if len(project_ids) == 1:
        scope_type, scope_entity_id = "project", next(iter(project_ids))
    else:
        scope_type, scope_entity_id = "global", None

    result = await capture_procedure(
        pool,
        name=name, goal=goal,
        steps=[s.model_dump() for s in extracted.steps],
        parameter_schema={
            "slots": [s.model_dump() for s in extracted.slots],
            "extraction_method": SYNTHESIS_TAG,
        },
        preconditions=[p.model_dump() for p in extracted.preconditions],
        scope=extracted.scope,
        failure_conditions=extracted.failure_conditions,
        invariants=[],
        family_id=family_id,
        evidence_refs=evidence_refs,
        source_episode_ids=list(episode_ids),
        owner_id=owner_id, visibility=visibility,
        provenance="system_pending_review",
        scope_type=scope_type, scope_entity_id=scope_entity_id,
    )

    # Same follow-up UPDATE convention extract_procedure() uses for the
    # migration-20 columns capture_procedure() does not carry (approval_
    # status/capability_statement/extracted_by) -- kept identical rather
    # than widening that function's signature for this caller alone.
    await pool.execute(
        "UPDATE procedures SET approval_status = 'proposed', "
        "capability_statement = $2, extracted_by = $3 WHERE id = $1::uuid",
        result["id"], capability_statement, SYNTHESIS_TAG,
    )

    return SynthesisResult(
        True, procedure_id=result["procedure_id"], version_row_id=result["id"],
        extracted=extracted, contributing_episode_ids=episode_ids,
    )

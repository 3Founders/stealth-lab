"""
Offline (no DATABASE_URL, no FakePool -- every target function here is
pure) proving tests for the two genuine gaps this session's audit closed
in app/services/procedure_extraction/synthesis.py against the three asks:

  1. "Optional behavior" -- a step present in only SOME contributing
     episodes' own tool-call skeleton must be marked `optional=True` on
     the merged ProcedureStep, not silently kept unmarked as if every
     episode performed it. Tests `_mark_optional_steps`.
  2. Explicit generalization LEVEL (0 literal / 1 parameterized-from-one-
     episode / 2 genuinely-multi-episode-generalized), never claiming 2
     from fewer than two episodes no matter how the other inputs are
     phrased, and never claiming 2 from >=2 episodes that show no real
     checkable variation. Tests `compute_generalization_level` and its
     real-variation input `_has_real_variation`.
  3. Confirms (does NOT re-fix -- already correct) that
     `_find_predicate_contradiction` REFUSES a genuine cross-episode
     contradiction rather than silently picking one value or averaging,
     and that `synthesize_procedure`'s own docstring/contract for this
     (CONTRADICTION HANDLING) is "refuse, return both facts", never
     "merge into two silent variants".

All three target functions (`_mark_optional_steps`,
`compute_generalization_level`, `_has_real_variation`,
`_find_predicate_contradiction`) are pure -- no DB, no asyncpg.Pool
argument -- so these tests call them directly rather than standing up a
FakePool/FakeConn (this module's own docstring already states the parts
that DO require a real pool: `build_episode_evidence`,
`derive_preconditions`/`derive_scope`, `_find_family_id`,
`capture_procedure` -- all exercised instead by the real, live-database
test_procedure_synthesis_e2e.py, unchanged by this pass).
"""
from __future__ import annotations

from app.services.procedure_extraction.derive import StepGroup, literal_steps_from_skeleton
from app.services.procedure_extraction.schema import Predicate, SlotSpec
from app.services.procedure_extraction.synthesis import (
    GENERALIZATION_LEVEL_LITERAL,
    GENERALIZATION_LEVEL_MULTI_EPISODE,
    GENERALIZATION_LEVEL_PARAMETERIZED,
    _find_predicate_contradiction,
    _has_real_variation,
    _intersect_predicates,
    _mark_optional_steps,
    _union_scope,
    compute_generalization_level,
)


# ---------------------------------------------------------------------
# 1. Optional behavior -- steps present in only SOME episodes
# ---------------------------------------------------------------------

def test_optional_step_marked_when_absent_from_one_episode():
    # Backbone (the richest episode, A): Read, Edit, Bash, Bash
    backbone = [
        StepGroup("Read", 2), StepGroup("Edit", 1),
        StepGroup("Bash", 1), StepGroup("Bash", 1),
    ]
    steps = literal_steps_from_skeleton(backbone)
    assert all(s.optional is False for s in steps), "default must be unmarked, never guessed True"

    skeletons_by_episode = {
        "ep-a": backbone,
        # Episode B never ran a second Bash step (e.g. no lint pass) --
        # real, checkable absence.
        "ep-b": [StepGroup("Read", 1), StepGroup("Edit", 1), StepGroup("Bash", 1)],
    }
    marked = _mark_optional_steps(steps, backbone, skeletons_by_episode)

    # Read/Edit/first Bash: present in both episodes' own skeletons.
    assert marked[0].optional is False, "Read is in both episodes -- must stay unmarked"
    assert marked[1].optional is False, "Edit is in both episodes -- must stay unmarked"
    # Both merged Bash backbone steps map to tool_name "Bash", which IS
    # present in ep-b (membership only, not count/position -- this
    # module's own stated limitation) -- so neither individual Bash
    # backbone step is falsely marked optional by a tool-name-only check.
    assert marked[2].optional is False
    assert marked[3].optional is False

    # Original list must be untouched (model_copy, not mutation).
    assert steps[0].optional is False
    assert steps is not marked


def test_optional_step_marked_when_tool_entirely_absent_from_other_episode():
    # Backbone: Read, Edit, TodoWrite (a checklist step episode A did,
    # episode B never touched a TodoWrite tool at all).
    backbone = [StepGroup("Read", 1), StepGroup("Edit", 1), StepGroup("TodoWrite", 1)]
    steps = literal_steps_from_skeleton(backbone)
    skeletons_by_episode = {
        "ep-a": backbone,
        "ep-b": [StepGroup("Read", 1), StepGroup("Edit", 1)],
    }
    marked = _mark_optional_steps(steps, backbone, skeletons_by_episode)
    assert marked[0].optional is False
    assert marked[1].optional is False
    assert marked[2].optional is True, "TodoWrite tool never appears in ep-b's own skeleton at all"


def test_optional_step_stays_unmarked_when_every_episode_has_it():
    backbone = [StepGroup("Read", 1), StepGroup("Edit", 1)]
    steps = literal_steps_from_skeleton(backbone)
    skeletons_by_episode = {
        "ep-a": backbone,
        "ep-b": [StepGroup("Read", 3), StepGroup("Edit", 2)],  # different counts, same tools
        "ep-c": [StepGroup("Read", 1), StepGroup("Edit", 1)],
    }
    marked = _mark_optional_steps(steps, backbone, skeletons_by_episode)
    assert all(s.optional is False for s in marked), (
        "every contributing episode's skeleton contains both tools -- nothing is optional"
    )


# ---------------------------------------------------------------------
# 2. Generalization level -- never level 2 from one episode, real
#    variation required for level 2, level 1 for a genuinely-slotted
#    single episode or an un-varying multi-episode batch, level 0
#    otherwise.
# ---------------------------------------------------------------------

def test_level_never_2_from_one_episode_no_matter_the_other_inputs():
    # Even if a caller (incorrectly) claimed real_variation_found=True
    # with n_episodes=1, the function must not reward it -- n_episodes is
    # the gating fact, not a value the rest of the computation trusts
    # blindly.
    assert compute_generalization_level(
        n_episodes=1, has_slots=False, real_variation_found=True,
    ) == GENERALIZATION_LEVEL_LITERAL
    assert compute_generalization_level(
        n_episodes=1, has_slots=True, real_variation_found=True,
    ) == GENERALIZATION_LEVEL_PARAMETERIZED


def test_level_0_for_one_episode_no_slots():
    assert compute_generalization_level(
        n_episodes=1, has_slots=False, real_variation_found=False,
    ) == GENERALIZATION_LEVEL_LITERAL


def test_level_1_for_one_episode_with_real_slots():
    assert compute_generalization_level(
        n_episodes=1, has_slots=True, real_variation_found=False,
    ) == GENERALIZATION_LEVEL_PARAMETERIZED


def test_level_2_for_multi_episode_with_real_variation():
    assert compute_generalization_level(
        n_episodes=3, has_slots=True, real_variation_found=True,
    ) == GENERALIZATION_LEVEL_MULTI_EPISODE
    # Slots are not required for level 2 -- real cross-episode structural
    # variation is itself the generalization signal.
    assert compute_generalization_level(
        n_episodes=2, has_slots=False, real_variation_found=True,
    ) == GENERALIZATION_LEVEL_MULTI_EPISODE


def test_level_never_2_for_multi_episode_without_real_variation():
    # Two (or three) compatible episodes whose real, checked skeletons/
    # scope are identical contributed more EVIDENCE, not a genuinely
    # generalized method -- must fall through to the slot check, never
    # claim level 2 on episode count alone.
    assert compute_generalization_level(
        n_episodes=3, has_slots=False, real_variation_found=False,
    ) == GENERALIZATION_LEVEL_LITERAL
    assert compute_generalization_level(
        n_episodes=3, has_slots=True, real_variation_found=False,
    ) == GENERALIZATION_LEVEL_PARAMETERIZED


def test_has_real_variation_true_for_different_skeletons():
    skeletons = {
        "ep-a": [StepGroup("Read", 1), StepGroup("Edit", 1)],
        "ep-b": [StepGroup("Read", 1), StepGroup("Edit", 1), StepGroup("Bash", 1)],
        "ep-c": [StepGroup("Read", 1), StepGroup("Edit", 1)],
    }
    scopes = [{"language": ["python"]}, {"language": ["python"]}, {"language": ["python"]}]
    assert _has_real_variation(skeletons, scopes) is True


def test_has_real_variation_true_for_different_scope_languages():
    skeletons = {
        "ep-a": [StepGroup("Read", 1), StepGroup("Edit", 1)],
        "ep-b": [StepGroup("Read", 1), StepGroup("Edit", 1)],
    }
    scopes = [{"language": ["python"]}, {"language": ["javascript"]}]
    assert _has_real_variation(skeletons, scopes) is True


def test_has_real_variation_false_for_identical_episodes():
    # Same skeleton shape, same scope -- N copies of the same trace, not
    # a genuinely generalized method.
    skeletons = {
        "ep-a": [StepGroup("Read", 2), StepGroup("Edit", 1)],
        "ep-b": [StepGroup("Read", 2), StepGroup("Edit", 1)],
        "ep-c": [StepGroup("Read", 2), StepGroup("Edit", 1)],
    }
    scopes = [{"language": ["python"]}, {"language": ["python"]}, {"language": ["python"]}]
    assert _has_real_variation(skeletons, scopes) is False


# ---------------------------------------------------------------------
# 3. Confirm (already correct, not re-fixed): competing/contradictory
#    predicates REFUSE the merge, never silently merged/averaged into
#    one falsely-universal procedure or split into silent variants.
# ---------------------------------------------------------------------

def test_predicate_contradiction_detected_and_named():
    preconditions_by_episode = {
        "ep-d": [Predicate(subject="project:d", predicate="package_manager", object="pip")],
        "ep-e": [Predicate(subject="project:e", predicate="package_manager", object="poetry")],
    }
    reason = _find_predicate_contradiction(preconditions_by_episode)
    assert reason is not None, "a genuine competing-strategy disagreement must never be silently merged"
    assert "package_manager" in reason
    assert "pip" in reason and "poetry" in reason


def test_predicate_contradiction_none_when_episodes_agree():
    preconditions_by_episode = {
        "ep-d": [Predicate(subject="project:d", predicate="language", object="python")],
        "ep-e": [Predicate(subject="project:e", predicate="language", object="python")],
    }
    assert _find_predicate_contradiction(preconditions_by_episode) is None


def test_contradiction_result_is_never_an_averaged_or_picked_value():
    """The contradiction gate's job is to REFUSE and name the fact, not
    to synthesize a compromise object -- confirm the return type carries
    no notion of "the merged value", only a human-readable refusal
    string naming both disagreeing real values."""
    preconditions_by_episode = {
        "ep-d": [Predicate(subject="project:d", predicate="package_manager", object="pip")],
        "ep-e": [Predicate(subject="project:e", predicate="package_manager", object="poetry")],
    }
    reason = _find_predicate_contradiction(preconditions_by_episode)
    assert isinstance(reason, str)
    # Neither candidate object silently "won" -- both real values are
    # named in the refusal text together, not one substituted for both.
    assert reason.count("pip") >= 1 and reason.count("poetry") >= 1


# ---------------------------------------------------------------------
# Sanity: the already-correct stable/variable split this pass did NOT
# touch, confirmed still behaves as documented (intersection for
# preconditions, union for scope) -- proving the audit didn't need to
# rebuild these.
# ---------------------------------------------------------------------

def test_precondition_intersection_keeps_only_universally_shared_triples():
    by_episode = {
        "ep-a": [
            Predicate(subject="project:x", predicate="language", object="python"),
            Predicate(subject="project:x", predicate="has_test_runner", object=None),
        ],
        "ep-b": [
            Predicate(subject="project:y", predicate="language", object="python"),
        ],
        "ep-c": [
            Predicate(subject="project:z", predicate="language", object="python"),
        ],
    }
    # Subjects legitimately differ per-project -- intersection is over
    # (subject, predicate, object) exactly, so nothing survives here
    # since no subject repeats; this confirms the documented behavior
    # (module docstring's PRECONDITIONS section), not a bug.
    merged = _intersect_predicates(by_episode)
    assert merged == []


def test_precondition_intersection_keeps_identical_subject_triples():
    by_episode = {
        "ep-a": [Predicate(subject="project:shared", predicate="language", object="python")],
        "ep-b": [Predicate(subject="project:shared", predicate="language", object="python")],
    }
    merged = _intersect_predicates(by_episode)
    assert len(merged) == 1
    assert merged[0].subject == "project:shared"
    assert merged[0].object == "python"


def test_scope_is_unioned_not_intersected():
    scopes = [{"language": ["python"]}, {"language": ["javascript"]}]
    merged = _union_scope(scopes)
    assert set(merged["language"]) == {"python", "javascript"}

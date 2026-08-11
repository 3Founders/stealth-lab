"""
The missing piece between graph_memory.py's pure retrieval and the
platform's actual thesis: does the corpus get BETTER as it grows, not
just bigger. After a new instance's knowledge_node is live in the graph
(post-restore, no longer held out), this checks whether it's similar
enough to an EXISTING node to warrant a real synthesis/dedup debate --
and if so, runs the exact same real, already-validated pipeline this
session proved works end-to-end on the synthetic Task A/B pair:
retrieval -> trigger -> debate -> auto-preserve -> validate -> apply.

Deliberately reuses every piece rather than reimplementing any of it:
  - find_reusable_nodes()          (app/services/reuse_detection.py)
  - create_conflict_trigger_for_pair() (app/services/knowledge_conflict.py)
  - LoopOrchestrator + default_panel/judge/layer2_agent (app/debate/*)
  - auto_preserve_missing_keys(), preflight_validate() -- the exact
    functions verified against the real property-key bug found on the
    synthetic pair; the SWE-bench corpus's knowledge_nodes use the same
    knowledge_nodes table and the same properties-replacement semantics
    in KnowledgeUpdater, so the same bug class applies here unchanged.

HONEST, UNTESTED AS OF WRITING: everything this imports has been
verified separately and for real (Experiment 3's PEP corpus, the
synthetic Task A/B pipeline). This specific composition, on real
SWE-bench knowledge_nodes, has not been run once yet. Treat the first
real invocation of consider_debate_curation() as a real test, not an
assumed-working extension -- same discipline as everything else this
session, and the actual next step before trusting this at any scale.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "scripts" / "synthetic_tasks"))

from app.debate.panel import default_judge, default_layer2_agent, default_panel  # noqa: E402
from app.models.change import ChangeSet  # noqa: E402
from app.services.knowledge_conflict import create_conflict_trigger_for_pair  # noqa: E402
from app.services.knowledge_update import ChangeApplicationError, KnowledgeUpdater  # noqa: E402
from app.services.loop import LoopOrchestrator  # noqa: E402
from app.services.reuse_detection import find_reusable_nodes  # noqa: E402

# Reusing the exact, already-validated functions -- not reimplementing
# them a second time for this new context.
from apply_debate_result import auto_preserve_missing_keys, preflight_validate  # noqa: E402

CURATION_THRESHOLD = 0.70  # the shared platform default -- deliberately
                            # NOT the 0.65 experimental override used for
                            # the small synthetic library; SWE-bench's
                            # real corpus is large enough that the
                            # calibration question from the synthetic
                            # tasks (too few real matches to judge
                            # threshold placement) doesn't apply the
                            # same way. Revisit with real data if this
                            # also turns out too strict here.


async def consider_debate_curation(
    pool, new_knowledge_node_id: str, instance_content: str,
    threshold: float = CURATION_THRESHOLD,
    approver_id: str = "swebench_debate_curation",
) -> Optional[dict]:
    """
    Returns None if no genuine match was found (a real, valid "nothing
    to do" outcome, not a failure). Otherwise returns a dict describing
    what happened -- including real failure modes (validation refused,
    apply refused) as legitimate, informative outcomes, not exceptions,
    so a caller running this across many instances can log and continue
    rather than crash the whole run on one bad proposal.
    """
    candidates = await find_reusable_nodes(pool, problem=instance_content)
    real_matches = [
        c for c in candidates
        if c.table == "knowledge_nodes" and c.id != new_knowledge_node_id and c.similarity >= threshold
    ]
    if not real_matches:
        return None

    winner = real_matches[0]  # find_reusable_nodes already returns rank-sorted

    trigger_id = await create_conflict_trigger_for_pair(
        pool, new_knowledge_node_id, winner.id, winner.similarity, approver_id=approver_id,
    )
    orchestrator = LoopOrchestrator(
        pool, default_panel(), default_judge(), layer2_agent=default_layer2_agent(),
    )
    scorecards = await orchestrator.run(trigger_id)

    if not scorecards:
        return {"outcome": "no_scorecards", "matched_node": winner.id, "similarity": winner.similarity}

    best = None
    for sc in scorecards:
        if sc.layer1.passed and (best is None or sc.layer1.groundedness_score > best.layer1.groundedness_score):
            best = sc
    if best is None:
        return {"outcome": "no_passing_candidate", "matched_node": winner.id, "similarity": winner.similarity}

    candidate_row = await pool.fetchrow(
        "SELECT change_set FROM candidates WHERE id = $1", best.candidate_id,
    )
    change_set_dict = candidate_row["change_set"]

    # The exact real pipeline verified on the synthetic Task A/B pair --
    # not reimplemented, imported and reused unchanged.
    change_set_dict = await auto_preserve_missing_keys(pool, change_set_dict)
    problems = await preflight_validate(pool, change_set_dict)
    if problems:
        return {
            "outcome": "validation_refused", "matched_node": winner.id,
            "similarity": winner.similarity, "problems": problems,
        }

    change_set = ChangeSet.model_validate(change_set_dict)
    updater = KnowledgeUpdater(pool)
    try:
        applied = await updater.apply(change_set, approver_id=approver_id)
    except ChangeApplicationError as exc:
        return {
            "outcome": "apply_refused", "matched_node": winner.id,
            "similarity": winner.similarity, "error": str(exc),
        }

    return {
        "outcome": "applied", "matched_node": winner.id,
        "similarity": winner.similarity, "applied": applied,
        "groundedness": best.layer1.groundedness_score,
    }

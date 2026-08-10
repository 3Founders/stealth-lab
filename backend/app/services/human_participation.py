"""
Human participation in a debate (Docket roadmap item).

The real design question this answers: the debate engine runs entirely
synchronously inside one request, there's no natural point where
execution pauses and waits for anything. Rather than rebuilding that
into a general pause/resume job system (a much larger, separate piece
of work -- see the infrastructure gaps in PROJECT_STATUS_AND_ROADMAP.md),
this uses the one pause point that already, genuinely exists: a debate
sitting in PENDING_APPROVAL, waiting for a human decision. A human can
add an argument there, the panel runs one more real round reacting to
it (via DebateEngine.run_continuation_round, the exact same per-round
logic every other round in this project has always used, not a
reimplementation), and new scorecards reflect whatever came out of
that -- still awaiting the same human decision, just better informed.

The debate deliberately never leaves PENDING_APPROVAL for this. The
state machine has no transition back into it from anywhere (by design --
see app/debate/state_machine.py), and this isn't a case that needs one;
the debate was never actually decided yet, so it was never really
somewhere else to return from.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import asyncpg

from app.db.graph_store import GraphStore
from app.debate.engine import DebateEngine
from app.debate.panel import default_judge, default_panel
from app.models.change import ChangeSet
from app.models.debate import Candidate, Citation, DebateResult, DebateTurn
from app.services.loop import LoopOrchestrator, Scorecard, _render_graph_context

log = logging.getLogger(__name__)


class DebateNotPendingApproval(Exception):
    pass


async def _load_debate_state(
    pool: asyncpg.Pool, debate_id: UUID
) -> tuple[dict, list[DebateTurn], list[Candidate]]:
    debate = await pool.fetchrow("SELECT * FROM debates WHERE id = $1", debate_id)
    if debate is None:
        raise LookupError(f"no debate {debate_id}")
    if debate["state"] != "PENDING_APPROVAL":
        raise DebateNotPendingApproval(
            f"debate {debate_id} is in state={debate['state']!r}, not "
            "PENDING_APPROVAL -- a human argument only makes sense while "
            "a decision is genuinely still open."
        )

    turn_rows = await pool.fetch(
        "SELECT * FROM debate_turns WHERE debate_id = $1 ORDER BY round_number, created_at",
        debate_id,
    )
    turns = [
        DebateTurn(
            id=r["id"], debate_id=r["debate_id"], round_number=r["round_number"],
            speaker_id=r["speaker_id"], speaker_kind=r["speaker_kind"],
            speaker_role=r["speaker_role"], model_used=r["model_used"],
            action=r["action"], candidate_id=r["candidate_id"], content=r["content"],
            cites=[Citation(**c) for c in r["cites"]], created_at=r["created_at"],
        )
        for r in turn_rows
    ]

    cand_rows = await pool.fetch(
        "SELECT * FROM candidates WHERE debate_id = $1 ORDER BY created_at", debate_id
    )
    candidates = [
        Candidate(
            id=r["id"], debate_id=r["debate_id"], summary=r["summary"],
            rationale=r["rationale"], change_set=ChangeSet(**r["change_set"]),
            supporters=list(r["supporters"]),
        )
        for r in cand_rows
    ]

    return dict(debate), turns, candidates


async def add_human_turn(
    pool: asyncpg.Pool,
    debate_id: UUID,
    author: str,
    content: str,
    action: str = "propose",
    candidate_id: Optional[UUID] = None,
) -> list[Scorecard]:
    """
    Adds a human argument to a debate genuinely still awaiting a
    decision, runs one real additional round of the same panel reacting
    to it, and produces new scorecards for whatever the round produced.

    `action` follows the same vocabulary as an agent turn -- 'propose' a
    new consideration, 'amend' an existing candidate (needs
    `candidate_id`), or 'pass' (recorded for the transcript, triggers no
    new round, since there's nothing for the panel to react to).
    """
    debate, turns, candidates = await _load_debate_state(pool, debate_id)
    by_id = {c.id: c for c in candidates}

    if action == "amend" and candidate_id is not None:
        target = by_id.get(candidate_id)
        if target is not None:
            target.rationale += f"\n\n[argument from {author}]\n{content}"

    new_round_number = debate["round_number"] + 1
    human_turn = DebateTurn(
        debate_id=debate_id,
        round_number=new_round_number,
        speaker_id=author,
        speaker_kind="human",
        speaker_role="participant",
        action=action,  # type: ignore[arg-type]
        candidate_id=candidate_id,
        content=content,
    )
    turns.append(human_turn)
    await pool.execute(
        "INSERT INTO debate_turns (id, debate_id, round_number, speaker_id, "
        "speaker_kind, speaker_role, action, candidate_id, content, cites) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        human_turn.id, human_turn.debate_id, human_turn.round_number,
        human_turn.speaker_id, human_turn.speaker_kind, human_turn.speaker_role,
        human_turn.action, human_turn.candidate_id, human_turn.content, [],
    )

    if action == "pass":
        # Recorded for the transcript, nothing for the panel to react to
        # -- no new round, no new scorecards, just an entry a reviewer
        # can read alongside the rest before deciding.
        return []

    trigger = await pool.fetchrow("SELECT * FROM triggers WHERE id = $1", debate["trigger_id"])
    graph = GraphStore(pool)
    graph_context = await _render_graph_context(graph, trigger["task_node_id"], pool)
    trigger_context = {
        "rule": trigger["rule_name"], "metric": trigger["metric_name"],
        "observed": float(trigger["observed_value"]), "threshold": float(trigger["threshold"]),
        "sample_size": trigger["sample_size"],
    }

    engine = DebateEngine(default_panel())
    new_turns, candidates, _ = await engine.run_continuation_round(
        debate_id, new_round_number, turns, candidates, trigger_context, graph_context,
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE debates SET round_number = $2 WHERE id = $1",
                debate_id, new_round_number,
            )
            for c in candidates:
                await conn.execute(
                    "INSERT INTO candidates (id, debate_id, summary, rationale, "
                    "change_set, supporters, no_action_justified) VALUES ($1,$2,$3,$4,$5,$6,$7) "
                    "ON CONFLICT (id) DO UPDATE SET rationale = EXCLUDED.rationale, "
                    "supporters = EXCLUDED.supporters, "
                    "no_action_justified = EXCLUDED.no_action_justified, updated_at = now()",
                    c.id, c.debate_id, c.summary, c.rationale,
                    c.change_set.model_dump(mode="json"), c.supporters,
                    c.no_action_justified,
                )
            for t in new_turns:
                await conn.execute(
                    "INSERT INTO debate_turns (id, debate_id, round_number, speaker_id, "
                    "speaker_kind, speaker_role, model_used, action, candidate_id, content, cites) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    t.id, t.debate_id, t.round_number, t.speaker_id, t.speaker_kind,
                    t.speaker_role, t.model_used, t.action, t.candidate_id, t.content,
                    [c.model_dump(mode="json") for c in t.cites],
                )

    # A real DebateResult, not a duck-typed stand-in -- _evaluate() reads
    # both .turns (to collect each candidate's citations) and
    # .candidates, missing .turns would have crashed with an
    # AttributeError, caught by checking the real method signature
    # rather than assuming what it needed.
    all_turns = turns + new_turns
    result = DebateResult(
        debate_id=debate_id, trigger_id=trigger["id"], rounds_used=new_round_number,
        termination_reason="round_cap", turns=all_turns, candidates=candidates,
    )
    eligible = result.eligible_candidates(1)  # any real support counts, post-argument

    orchestrator = LoopOrchestrator(pool, default_panel(), default_judge())
    return await orchestrator._evaluate(result, eligible, trigger["task_node_id"])

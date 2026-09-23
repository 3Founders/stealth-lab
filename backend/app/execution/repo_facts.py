"""
Repo facts (`.stealth/claims.md`) as input to `find_ways` -- final_thing.md,
option B + tie-break.

Two uses, both request-scoped (never stored, never logged, never written to
the shared judgment cache -- these are facts about a user's private repo):

1. Goal tie-break (cheap, no LLM): when `resolve_intent` returns "ambiguous",
   each candidate Goal's name is scored against the repo facts by token
   overlap. A candidate is picked only if that pushes it past the same
   decisiveness margin `resolve_intent` itself uses; otherwise it stays
   ambiguous.

2. Procedure check (the existing claim-conditioned NLI/JEV judge): for the
   Procedures `resolve_goal` found feasible, pick the 5-20 repo facts most
   related to each one and run them through the SAME judge chain
   (JEV -> Gemini -> Gemma) and the SAME hard-reject rule
   `claim_conditioned_retrieval` uses -- a REQUIRED condition contradicted at
   >= 0.75 drops the Procedure; the rest are re-ordered by verdict. If the
   judge chain is unavailable the order is left untouched and the result says
   "not_checked" -- the plan is never blocked on it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_REPO_CLAIMS = 200
MAX_REPO_CLAIMS_BYTES = 64_000
CLAIMS_PER_PROCEDURE_MIN = 5
CLAIMS_PER_PROCEDURE_MAX = 20
MAX_JUDGE_CALLS = 5

_STOP = frozenset(
    "the and for with from into that this using use uses used via are was were can will "
    "new all any its your you our their when then than not but also only each per".split()
)
_VERDICT_RANK = {"APPLICABLE": 0, "PARTIALLY_APPLICABLE": 1, "UNKNOWN": 2, "INAPPLICABLE": 3}


def parse_repo_claims(text: str) -> tuple[list[dict], bool]:
    """claims.md text -> [{claim_id, statement, topic, scope, source}], truncated?
    Reuses local_sync's own CLAIM-line parser -- one grammar, one parser."""
    from app.stealth.local_sync import _parse_claims_md

    truncated = len(text.encode("utf-8")) > MAX_REPO_CLAIMS_BYTES
    if truncated:
        text = text.encode("utf-8")[:MAX_REPO_CLAIMS_BYTES].decode("utf-8", "ignore")
    claims = [
        {"claim_id": o.local_id, "statement": o.name_or_statement, "topic": o.topic,
         "scope": o.scope, "source": (o.extra or {}).get("source")}
        for o in _parse_claims_md(text) if o.name_or_statement
    ]
    if len(claims) > MAX_REPO_CLAIMS:
        claims, truncated = claims[:MAX_REPO_CLAIMS], True
    return claims, truncated


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9]+", (text or "").lower()) if len(t) >= 3 and t not in _STOP}


def overlap(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def best_fit(text: str, claims: list[dict]) -> tuple[float, Optional[str]]:
    best, best_id = 0.0, None
    for c in claims:
        s = overlap(text, c["statement"])
        if s > best:
            best, best_id = s, c["claim_id"]
    return best, best_id


def goal_tiebreak(candidates: list[Any], claims: list[dict], *, margin: float) -> tuple[Optional[Any], list[dict]]:
    """`candidates`: resolve_intent GoalCandidate objects (have .goal, .score),
    already judged "too close to call" by search score. Search can't separate
    them, so repo fit alone decides -- with the same decisiveness margin
    resolve_intent uses. Returns (winner or None, per-candidate detail)."""
    scored = []
    for c in candidates:
        fit, cid = best_fit(str(c.goal.get("canonical_name") or ""), claims)
        scored.append((fit, cid, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    detail = [{"goal_id": str(s[2].goal.get("id")), "search_score": s[2].score,
               "repo_fit": round(s[0], 4), "best_fact": s[1]} for s in scored]
    if not scored or scored[0][0] <= 0:
        return None, detail
    if len(scored) == 1 or scored[0][0] - scored[1][0] >= margin:
        return scored[0][2], detail
    return None, detail


def _procedure_text(proc: dict) -> str:
    parts = [str(proc.get("name") or ""), str(proc.get("goal") or "")]
    for p in proc.get("preconditions") or []:
        if isinstance(p, dict):
            parts.append(" ".join(str(p.get(k) or "") for k in ("subject", "predicate", "object")))
    for s in proc.get("steps") or []:
        if isinstance(s, dict):
            parts.append(str(s.get("goal") or s.get("description") or ""))
    return " ".join(parts)


def select_claims_for(proc: dict, claims: list[dict]) -> list[dict]:
    """The 5-20 repo facts most related to this Procedure; the floor pads with
    the next-best so the judge always sees some repo context."""
    text = _procedure_text(proc)
    ranked = sorted(claims, key=lambda c: overlap(text, c["statement"]), reverse=True)
    related = [c for c in ranked if overlap(text, c["statement"]) > 0][:CLAIMS_PER_PROCEDURE_MAX]
    if len(related) < CLAIMS_PER_PROCEDURE_MIN:
        related = ranked[:CLAIMS_PER_PROCEDURE_MIN]
    return related


@dataclass
class RepoFactsProcedureSelector:
    """Plugged into resolve_goal via context["_procedure_selector"]. Called
    with a Goal's feasible Procedures; returns (kept in order, rejected)."""
    claims: list[dict]
    judge: Any = None
    threshold: float = 0.75
    calls: int = 0
    status: str = "ok"
    detail: str = ""
    judged: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def report(self) -> dict:
        return {"status": self.status, "detail": self.detail, "judge_calls": self.calls,
                "judged": self.judged, "rejected": self.rejected}

    def _not_checked(self, reason: str) -> None:
        if self.status == "ok":
            self.status, self.detail = "not_checked", reason

    async def __call__(self, goal_name: str, feasible: list[dict], *, depth: int = 0) -> tuple[list[dict], list[dict]]:
        from app.services.applicability_judge import JudgeCandidateInput, extract_requirement_conditions
        from app.services.claim_conditioned_retrieval import _is_hard_rejected

        if not feasible or not self.claims:
            return feasible, []
        conds = {str(p["id"]): extract_requirement_conditions(p) for p in feasible}
        # Judge only where it can change something: the root Goal, a choice
        # between several Procedures, or a Procedure with real conditions.
        if depth > 0 and len(feasible) == 1 and not conds[str(feasible[0]["id"])]:
            return feasible, []
        if self.judge is None:
            self._not_checked("no semantic judge configured")
            return feasible, []
        if self.calls >= MAX_JUDGE_CALLS:
            self._not_checked(f"judge call budget ({MAX_JUDGE_CALLS}) reached")
            return feasible, []

        inputs = [
            JudgeCandidateInput(
                candidate_id=str(p["id"]), candidate_version=p.get("version"),
                candidate_purpose=str(p.get("goal") or p.get("name") or ""),
                conditions=conds[str(p["id"])],
                claims=[{"claim_id": c["claim_id"], "statement": c["statement"]} for c in select_claims_for(p, self.claims)],
            )
            for p in feasible
        ]
        self.calls += 1
        try:
            judgments = await self.judge.judge_batch(goal_name, inputs)
        except Exception as exc:  # noqa: BLE001 -- chain exhausted / provider error: never block the plan
            self._not_checked(f"semantic judge unavailable: {type(exc).__name__}")
            return feasible, []

        by_id = {j.candidate_id: j for j in judgments}
        kept, rejected = [], []
        for idx, p in enumerate(feasible):
            j = by_id.get(str(p["id"]))
            if j is None:
                kept.append((len(_VERDICT_RANK), idx, p))
                continue
            fit = {"verdict": j.verdict, "supporting_fact_ids": j.supporting_claim_ids,
                   "blocking_fact_ids": j.blocking_claim_ids, "reason": j.reason, "provider": j.model}
            p["_repo_fit"] = fit
            self.judged.append({"goal": goal_name, "procedure_id": str(p.get("procedure_id") or p["id"]),
                                "name": p.get("name"), **fit})
            if _is_hard_rejected(j, conds[str(p["id"])], threshold=self.threshold):
                rejected.append(p)
                self.rejected.append({"goal": goal_name, "procedure_id": str(p.get("procedure_id") or p["id"]),
                                      "name": p.get("name"), "blocking_fact_ids": j.blocking_claim_ids,
                                      "reason": j.reason})
                continue
            kept.append((_VERDICT_RANK.get(j.verdict, len(_VERDICT_RANK)), idx, p))
        kept.sort(key=lambda t: (t[0], t[1]))  # verdict first, original verified/recency order second
        return [p for _, _, p in kept], rejected

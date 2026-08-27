"""
LLM-judge adjudication for semantic_label pairs that fail error_floor.py's
token-Jaccard rubric (RESEARCH brief:
.scratch/research/observation-labeling-technique-brief.md - STALE's
LLM-judge-over-lexical-match finding, 95.8% human-agreement validated in
their own Appendix E.3).

PREP ONLY as of 2026-08-27 fourth wave: built and offline-tested against a
fake transport (zero network calls). NOT yet run against a real model -
the account's free-tier daily cap (50 req/day) was confirmed still
exhausted this session via a live probe (HTTP 429, X-RateLimit-Remaining:
0, X-RateLimit-Reset -> 2026-08-28T00:00:00Z) before this module was
written, so no live judge calls happened this wave. Ready to run the
moment the cap resets; nothing here is mirrored into any production
prompt or into error_floor.py's default behavior.

Design shape, per the brief's concrete recommendation:
  - error_floor.py's Jaccard rule stays the free, deterministic, default
    FIRST pass, completely unchanged (`judge=None` everywhere by default -
    byte-identical to every grading run before this module existed).
  - Only pairs of the SAME observation_type that FAIL Jaccard get routed to
    ONE adjudicating judge call: a closed yes/no verdict, not open scoring,
    so it composes cleanly with error_floor.py's existing TP/FP/FN
    accounting instead of requiring a new metric.
  - Ratchet's asymmetry caution (arXiv:2605.22148, cited in the brief): a
    judge false POSITIVE silently corrupts the metric it grades, while a
    false NEGATIVE only costs a little recall and is cheap to notice/redo.
    An unparseable or ambiguous verdict here therefore defaults to NO
    MATCH, never to match-by-default.
  - The brief's step 4 (3-call majority vote before this backs any
    headline/public number) is deliberately NOT implemented here - this is
    a single-call judge sized for dev-loop diagnostics; upgrading to
    majority vote is future work, not silently assumed done.
  - The brief's step 3 (validate judge-vs-human agreement on a hand-sample
    before trusting the swap) is also NOT done here - it requires live
    calls this session's quota does not have. Do not treat this module as
    validated until that pass exists.

Reuses openrouter_arms.OpenRouterClient wholesale (same backoff/chain/
SpendLog machinery, same backend/.env key) - no new HTTP code, no new
retry logic.

Offline tests: tests/test_semantic_judge.py (fake client, zero network).
"""
from __future__ import annotations

import asyncio
import json
import re

JUDGE_SCHEMA = '{"match": true|false}'

JUDGE_SYSTEM_PROMPT = (
    "You adjudicate whether two short labels describe the SAME underlying "
    "change to a software project, allowing for paraphrase, abbreviation, "
    "and synonym choice (e.g. 'CI' = 'continuous integration', 'workflow' "
    "= 'pipeline', 'updated' = 'added' when both mean a config changed). "
    "Answer false if the labels describe genuinely different changes, "
    "even if they share some words. Do not guess generously - only answer "
    "true when a careful engineer reading both labels would agree they "
    "refer to the same real-world event.\n"
    f"Reply with ONLY this JSON object, no other text:\n{JUDGE_SCHEMA}"
)


def build_messages(gold_label: str, pred_label: str) -> list[dict]:
    user = (f'A (gold label): "{gold_label}"\n'
            f'B (predicted label): "{pred_label}"\n'
            "Do A and B describe the same underlying change?")
    return [{"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user}]


def parse_verdict(content: str) -> bool | None:
    """Model reply -> True/False, or None if unparseable. Callers treat
    None as NO MATCH (see module docstring's asymmetry-caution note) - this
    function itself stays a neutral parser, not the policy decision."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("match"), bool):
            return obj["match"]
    # Tolerant fallback for a bare yes/no reply with no JSON at all.
    lowered = text.lower()
    has_yes = re.search(r"\byes\b|\btrue\b", lowered) is not None
    has_no = re.search(r"\bno\b|\bfalse\b", lowered) is not None
    if has_yes and not has_no:
        return True
    if has_no and not has_yes:
        return False
    return None


class SemanticJudge:
    """One adjudicating call per disputed pair. `client` is any
    openrouter_arms.FrontierClient-compatible object (real OpenRouterClient
    or a fake) - dependency injection is what makes this offline-testable
    with zero network, matching every other live component in this lane."""

    def __init__(self, client):
        self.client = client
        self.calls = 0
        self.unparseable = 0

    async def adjudicate_async(self, gold_label: str, pred_label: str,
                               excerpt_id: str = "") -> bool:
        resp = await self.client.chat(build_messages(gold_label, pred_label),
                                      task_id=excerpt_id, arm="JUDGE")
        self.calls += 1
        verdict = parse_verdict(resp.get("content", ""))
        if verdict is None:
            self.unparseable += 1
            return False  # conservative default - see module docstring
        return verdict

    def adjudicate(self, gold_label: str, pred_label: str,
                   excerpt_id: str = "") -> bool:
        return asyncio.run(
            self.adjudicate_async(gold_label, pred_label, excerpt_id))

    def as_error_floor_judge(self):
        """-> sync `(gold_label, pred_label) -> bool` matching
        error_floor.py's `judge=...` contract, for grade_excerpt /
        observations_match / semantic_match."""
        return lambda g, p: self.adjudicate(g, p)

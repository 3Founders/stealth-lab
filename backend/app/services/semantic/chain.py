"""
SemanticJudge: ordered provider fallback with bounded, classified retries.

    JEV -> Gemini -> Gemma      (order from SEMANTIC_PROVIDER_PRIMARY/_FALLBACKS)

* Per provider: up to `per_provider_attempts` tries on TRANSIENT errors with
  exponential backoff + jitter. PERMANENT/UNSUPPORTED move to the next
  provider immediately.
* Never raises for provider failure: returns ChainResult(ok=False). (The one exception is
  ingest_budget.BudgetExceeded in an ingestion worker: that is a cost stop, not a provider failure.) Whether
  that means "requeue" (NLI) or "retain context" (compaction) is the CALLER's
  policy -- and neither ever substitutes a heuristic judgment.
* There is deliberately no deterministic provider in this module.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.services import ingest_budget
from app.services.semantic.errors import (
    ErrorKind,
    SemanticJudgmentUnavailable,
    classify_exception,
)
from app.services.semantic.policy import METRICS, RetryPolicy, SemanticMetrics
from app.services.semantic.providers import (
    CAP_APPLICABILITY,
    CAP_IDENTITY,
    CAP_RELATION,
    CAP_RETENTION,
    CAP_SUMMARY,
    SemanticProvider,
)

log = logging.getLogger(__name__)


@dataclass
class Attempt:
    provider: str
    attempt: int
    ok: bool
    error_kind: Optional[str] = None
    detail: str = ""
    latency_ms: float = 0.0


@dataclass
class ChainResult:
    ok: bool
    value: Any = None
    provider: Optional[str] = None
    model: Optional[str] = None
    fallback_used: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    latency_ms: float = 0.0
    reason: str = ""

    def unwrap(self) -> Any:
        if not self.ok:
            raise SemanticJudgmentUnavailable(self.reason or "all semantic providers failed",
                                              attempts=self.attempts)
        return self.value


class SemanticJudge:
    def __init__(
        self, providers: list[SemanticProvider], policy: Optional[RetryPolicy] = None,
        metrics: Optional[SemanticMetrics] = None, *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.providers = list(providers)
        self.policy = policy or RetryPolicy()
        self.metrics = metrics if metrics is not None else METRICS
        self._sleep, self._rng, self._mono = sleep, rng, monotonic

    @classmethod
    def from_settings(cls, settings=None, **kw) -> "SemanticJudge":
        from app.services.semantic.policy import policy_from_settings
        from app.services.semantic.providers import build_provider_chain

        policy = kw.pop("policy", None) or policy_from_settings(settings)
        return cls(build_provider_chain(settings, timeout_s=policy.timeout_s), policy, **kw)

    @property
    def chain_id(self) -> str:
        return ">".join(f"{p.name}:{p.model}" for p in self.providers) or "none"

    # ---- operations -------------------------------------------------

    async def judge_applicability(self, goal: str, candidates: list) -> ChainResult:
        if not candidates:
            return ChainResult(ok=True, value=[])
        return await self._run("applicability", CAP_APPLICABILITY, lambda p: p.applicability(goal, candidates))

    async def judge_retention(self, state: dict, units: list[dict]) -> ChainResult:
        if not units:
            return ChainResult(ok=True, value=[])
        return await self._run("retention", CAP_RETENTION, lambda p: p.retention(state, units))

    async def summarize_context(self, state: dict, units: list[dict]) -> ChainResult:
        if not units:
            return ChainResult(ok=True, value={})
        return await self._run("summary", CAP_SUMMARY, lambda p: p.summarize(state, units))

    async def judge_claim_relation(self, statement_a: str, statement_b: str) -> ChainResult:
        return await self._run("claim_relation", CAP_RELATION, lambda p: p.claim_relation(statement_a, statement_b))

    async def judge_identity(self, kind: str, a: str, b: str) -> ChainResult:
        """Relation of the NEW object A to the EXISTING object B (see
        prompts.IDENTITY_RELATIONS). Never heuristic: on total failure the
        result is ok=False and the caller must fail closed / retry."""
        return await self._run("identity", CAP_IDENTITY, lambda p: p.identity(kind, a, b))

    # ---- core -------------------------------------------------------

    async def _run(self, op: str, capability: str, call: Callable[[SemanticProvider], Awaitable[Any]]) -> ChainResult:
        m, pol = self.metrics, self.policy
        start = self._mono()
        attempts: list[Attempt] = []
        m.inc("semantic.invocations")
        m.inc(f"semantic.{op}.invocations")
        eligible = [p for p in self.providers if p.supports(capability)]
        deadline_hit = False

        for p in eligible:
            for n in range(1, pol.per_provider_attempts + 1):
                if pol.deadline_s is not None and self._mono() - start >= pol.deadline_s:
                    deadline_hit = True
                    break
                await ingest_budget.guard(op)   # ingestion workers only: BudgetExceeded propagates (never a chain failure)
                t0 = self._mono()
                try:
                    value = await asyncio.wait_for(call(p), timeout=pol.timeout_s)
                except Exception as exc:  # noqa: BLE001 -- classified below; never swallowed silently
                    kind = classify_exception(exc)
                    dt = (self._mono() - t0) * 1000
                    attempts.append(Attempt(p.name, n, False, kind.value, str(exc)[:300], dt))
                    if kind is ErrorKind.UNSUPPORTED:
                        break
                    m.inc("semantic.provider_failures")
                    m.inc(f"semantic.provider_failures.{p.name}.{kind.value}")
                    m.event("provider_failure", op=op, provider=p.name, kind=kind.value, attempt=n)
                    if kind is ErrorKind.PERMANENT or n >= pol.per_provider_attempts:
                        break
                    delay = pol.backoff(n, self._rng)
                    if pol.deadline_s is not None and (self._mono() - start) + delay >= pol.deadline_s:
                        deadline_hit = True
                        break
                    m.inc("semantic.retries")
                    await self._sleep(delay)
                    continue
                dt = (self._mono() - t0) * 1000
                attempts.append(Attempt(p.name, n, True, latency_ms=dt))
                await ingest_budget.record_judge(p.name, p.model, op)
                fallback = p is not eligible[0]
                m.inc(f"semantic.provider_selected.{p.name}")
                if fallback:
                    m.inc("semantic.fallback_used")
                m.event("provider_selected", op=op, provider=p.name, fallback=fallback)
                return ChainResult(True, value, p.name, p.model, fallback, attempts, (self._mono() - start) * 1000)
            if deadline_hit:
                break

        reason = ("no semantic provider configured for " + capability if not eligible
                  else "deadline exceeded" if deadline_hit else "all semantic providers failed")
        m.inc("semantic.chain_exhausted")
        m.event("chain_exhausted", op=op, reason=reason, attempts=len(attempts))
        if attempts:
            log.warning(
                "semantic: chain exhausted for %s (%s): %s",
                op, reason,
                " | ".join(f"{a.provider}#{a.attempt} {a.error_kind}: {a.detail}" for a in attempts),
            )
        return ChainResult(False, None, None, None, False, attempts, (self._mono() - start) * 1000, reason)

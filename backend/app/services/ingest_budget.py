"""Model-spend budget for the ingestion path.

The API already enforces ``DAILY_LLM_BUDGET_USD`` through ``CostGovernor`` (``llm_spend`` ledger,
rolling 24h). Ingestion workers spend on embeddings and on the judge chain (JEV -> Gemini -> Gemma)
but neither was recorded in that ledger nor checked against it. This module wires the SAME ledger and
the SAME cap into the worker; there is no second cost system.

    budget available   -> continue
    warning threshold  -> log + telemetry event (+ visible in ``admin metrics`` / ``stealth-ops watch``)
    budget exceeded    -> the worker stops LEASING new work; a call that starts while over budget raises
                          ``BudgetExceeded``, which the worker treats as "hand the job back, do not spend an attempt"

Only a worker installs the guard (``install``); with nothing installed every hook is a no-op, so the
API/MCP paths (already gated per request) and tests are unchanged.

Token counts are estimates (the same ~4 chars/token heuristic CostGovernor documents); this is a
guardrail, not billing. JEV is a self-hosted service and is priced at the "local" rate (0) unless
``INGEST_JEV_COST_PER_CALL_USD`` says otherwise.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.governance import BudgetExceeded, CostGovernor, estimate_tokens

log = logging.getLogger(__name__)

INGEST_SCOPE_KEY = "ingestion"
_PROVIDER_PRICING_NAME = {"gemini": "google", "google": "google", "voyage": "voyage", "gemma": "local",
                          "local": "local", "jev": "local", "openai": "openai", "anthropic": "anthropic"}
# Rough per-call judge sizes (identity/relation prompts are short); overridable, never zero for paid providers.
_JUDGE_TOKENS_IN = int(os.environ.get("INGEST_JUDGE_EST_TOKENS_IN", "900"))
_JUDGE_TOKENS_OUT = int(os.environ.get("INGEST_JUDGE_EST_TOKENS_OUT", "120"))


@dataclass(frozen=True)
class BudgetStatus:
    state: str            # ok | warn | exceeded | unavailable
    spent_usd: float
    cap_usd: float
    fraction: float
    warn_fraction: float

    def as_dict(self) -> dict:
        return {"state": self.state, "spent_24h_usd": round(self.spent_usd, 4), "daily_cap_usd": self.cap_usd,
                "fraction": round(self.fraction, 4), "warn_fraction": self.warn_fraction}


class IngestBudget:
    def __init__(self, pool: Any, *, cap_usd: Optional[float] = None, warn_fraction: Optional[float] = None,
                 cache_s: float = 10.0):
        from app.config import settings

        self._gov = CostGovernor(pool)
        self._cap = float(settings.daily_llm_budget_usd if cap_usd is None else cap_usd)
        self._warn = float(os.environ.get("INGEST_BUDGET_WARN_FRACTION", "0.8") if warn_fraction is None else warn_fraction)
        self._cache_s = cache_s
        self._cached: Optional[tuple[float, BudgetStatus]] = None
        self._warned_at = 0.0

    async def status(self, *, fresh: bool = False) -> BudgetStatus:
        now = time.monotonic()
        if not fresh and self._cached and now - self._cached[0] < self._cache_s:
            return self._cached[1]
        try:
            spent = await self._gov.spend_since(datetime.now(timezone.utc) - timedelta(days=1))
        except Exception as exc:  # noqa: BLE001 -- unknown spend is NOT "no spend": callers pause
            log.error("ingest budget: ledger unavailable: %s", exc)
            st = BudgetStatus("unavailable", 0.0, self._cap, 0.0, self._warn)
        else:
            frac = spent / self._cap if self._cap > 0 else 1.0
            st = BudgetStatus("exceeded" if frac >= 1.0 else "warn" if frac >= self._warn else "ok",
                              spent, self._cap, frac, self._warn)
        self._cached = (now, st)
        if st.state == "warn" and now - self._warned_at > 60:
            self._warned_at = now
            log.warning("ingest budget WARNING: $%.2f of $%.2f (%.0f%%) spent in the last 24h", st.spent_usd, st.cap_usd, st.fraction * 100)
            try:
                from app import telemetry
                telemetry.add_event("ingest_budget_warning", spent_usd=st.spent_usd, cap_usd=st.cap_usd)
            except Exception:  # noqa: BLE001 -- telemetry never breaks ingestion
                pass
        return st

    async def assert_available(self) -> None:
        st = await self.status()
        if st.state in ("exceeded", "unavailable"):
            raise BudgetExceeded(
                f"ingestion model budget {st.state}: ${st.spent_usd:.2f} of ${st.cap_usd:.2f} in the last 24h"
                if st.state == "exceeded" else "ingestion model budget ledger unavailable")

    async def record(self, provider: str, model: str, operation: str, tokens_in: int, tokens_out: int = 0) -> None:
        pricing = _PROVIDER_PRICING_NAME.get((provider or "").lower(), provider)
        await self._gov.record(pricing, model, operation, tokens_in, tokens_out, scope_key=INGEST_SCOPE_KEY)
        self._cached = None   # next status() re-reads the ledger


_ACTIVE: Optional[IngestBudget] = None


def install(pool: Any, **kw: Any) -> IngestBudget:
    global _ACTIVE
    _ACTIVE = IngestBudget(pool, **kw)
    return _ACTIVE


def uninstall() -> None:
    global _ACTIVE
    _ACTIVE = None


def active() -> Optional[IngestBudget]:
    return _ACTIVE


async def guard(_operation: str = "") -> None:
    """Called before every paid model call on the ingestion path. No-op unless a worker installed a budget."""
    if _ACTIVE is not None:
        await _ACTIVE.assert_available()


async def record_embedding(provider: str, model: str, texts: list[str]) -> None:
    if _ACTIVE is not None:
        await _ACTIVE.record(provider, model, "embedding", sum(estimate_tokens(t) for t in texts))


async def record_completion(model: str, op: str, usage: Any, provider: str = "google") -> None:
    """Chat-completion calls on the ingestion path (document/claim extraction), priced from the
    response's own `usage` -- before this, extraction never reached llm_spend at all, so its cost
    was invisible and the daily cap undercounted."""
    if _ACTIVE is None or usage is None:
        return
    await _ACTIVE.record(provider, model, op, int(getattr(usage, "prompt_tokens", 0) or 0),
                         int(getattr(usage, "completion_tokens", 0) or 0))


async def record_judge(
    provider: str, model: str, op: str, *, tokens_in: Optional[int] = None, tokens_out: Optional[int] = None,
) -> None:
    if _ACTIVE is None:
        return
    tokens_in = _JUDGE_TOKENS_IN if tokens_in is None else max(0, int(tokens_in))
    tokens_out = _JUDGE_TOKENS_OUT if tokens_out is None else max(0, int(tokens_out))
    if (provider or "").lower() == "jev":
        per_call = float(os.environ.get("INGEST_JEV_COST_PER_CALL_USD", "0"))
        if per_call <= 0:
            await _ACTIVE.record("jev", model, f"judge:{op}", tokens_in, tokens_out)
            return
        await _ACTIVE.record("openai", model, f"judge:{op}", int(per_call * 1_000_000 / 2.5), 0)
        return
    await _ACTIVE.record(provider, model, f"judge:{op}", tokens_in, tokens_out)

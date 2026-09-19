"""
Semantic providers. Every provider implements the same four operations;
one that does not expose an operation raises ProviderError(UNSUPPORTED) and
is skipped by the chain (never retried, never counted as an outage).

    JEVProvider          operator-hosted decision-oriented judge over HTTP.
                         Only the operations listed in JEV_CAPABILITIES are
                         used (default: applicability). It never generates
                         summaries.
    OpenAICompatProvider Gemini (OpenAI-compatible endpoint) and Gemma via the
                         existing local OpenAI-compatible server. Implements
                         all four operations with the shared prompts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from app.services.semantic import prompts
from app.services.semantic.errors import ErrorKind, ProviderError, classify_exception

log = logging.getLogger(__name__)

CAP_APPLICABILITY = "applicability"
CAP_RETENTION = "retention"
CAP_SUMMARY = "summary"
CAP_RELATION = "claim_relation"
CAP_IDENTITY = "identity"
ALL_CAPS = frozenset({CAP_APPLICABILITY, CAP_RETENTION, CAP_SUMMARY, CAP_RELATION, CAP_IDENTITY})


class SemanticProvider:
    name = "base"
    model = ""
    model_version = "v1"
    capabilities: frozenset = frozenset()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def _unsupported(self, op: str):
        raise ProviderError(ErrorKind.UNSUPPORTED, f"{op} not exposed", provider=self.name)

    async def applicability(self, goal: str, candidates: list) -> list:
        self._unsupported("applicability")

    async def retention(self, state: dict, units: list[dict]) -> list[dict]:
        self._unsupported("retention")

    async def summarize(self, state: dict, units: list[dict]) -> dict:
        self._unsupported("summary")

    async def claim_relation(self, statement_a: str, statement_b: str) -> dict:
        self._unsupported("claim_relation")

    async def identity(self, kind: str, a: str, b: str) -> dict:
        self._unsupported("identity")


# ------------------------------------------------------------------ JEV


class JEVProvider(SemanticProvider):
    name = "jev"

    def __init__(self, remote: Any, capabilities: set[str]):
        self._remote = remote  # RemoteHTTPJudge (owns the HTTP transport + error classification)
        self.model = getattr(remote, "model", "jev")
        # JEV never does text synthesis, whatever the config says.
        self.capabilities = frozenset(capabilities & (ALL_CAPS - {CAP_SUMMARY}))

    async def applicability(self, goal, candidates):
        return await self._remote.judge_batch(goal, candidates)

    async def retention(self, state, units):
        body = await self._remote.post_json("/judge-retention", {"state": state, "units": units})
        try:
            return prompts.parse_retention(json.dumps(body), [u["unit_id"] for u in units])
        except ValueError as exc:
            raise ProviderError(ErrorKind.TRANSIENT, f"invalid retention reply: {exc}", provider=self.name) from exc

    async def claim_relation(self, statement_a, statement_b):
        body = await self._remote.post_json(
            "/judge-claim-relation", {"claim_a": statement_a, "claim_b": statement_b})
        return _validated_relation(body, self.name)

    async def identity(self, kind, a, b):
        body = await self._remote.post_json("/judge-identity", {"kind": kind, "a": a, "b": b})
        try:
            return prompts.parse_identity(kind, body)
        except ValueError as exc:
            raise ProviderError(ErrorKind.TRANSIENT, f"invalid identity reply: {exc}", provider=self.name) from exc


# --------------------------------------------------------- OpenAI-compat


class OpenAICompatProvider(SemanticProvider):
    """`clients` is a list of AsyncOpenAI-shaped objects (one per API key;
    rate-limit on one rotates to the next before the chain sees a failure)."""

    capabilities = ALL_CAPS

    def __init__(self, name: str, clients: list[Any], model: str, *, max_concurrency: int = 4):
        self.name = name
        self.model = model
        self._clients = clients
        self._sem = asyncio.Semaphore(max_concurrency)

    async def _complete(self, system: str, user: str, max_tokens: int) -> str:
        last: Optional[BaseException] = None
        for client in self._clients:
            try:
                resp = await client.chat.completions.create(
                    model=self.model, temperature=0.0, max_tokens=max_tokens,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as exc:  # noqa: BLE001
                last = exc
                if classify_exception(exc) is ErrorKind.PERMANENT and len(self._clients) > 1:
                    continue  # a bad key: try the next key
                if getattr(exc, "status_code", None) == 429 or type(exc).__name__ == "RateLimitError":
                    continue  # quota on this key: rotate
                break
        assert last is not None
        raise ProviderError(classify_exception(last), repr(last), provider=self.name) from last

    async def applicability(self, goal, candidates):
        from app.services.applicability_judge import (
            _JUDGE_SYSTEM_PROMPT, judge_user_payload, parse_judgment_dict,
        )

        async def one(candidate):
            async with self._sem:
                text = await self._complete(
                    _JUDGE_SYSTEM_PROMPT,
                    json.dumps(judge_user_payload(goal, candidate), default=str), 500)
            try:
                return parse_judgment_dict(goal, candidate, prompts._loads_object(text), model=self.model)
            except ValueError as exc:
                raise ProviderError(ErrorKind.TRANSIENT, f"invalid judgment: {exc}", provider=self.name) from exc

        return list(await asyncio.gather(*[one(c) for c in candidates]))

    async def retention(self, state, units):
        text = await self._complete(
            prompts.RETENTION_SYSTEM_PROMPT, prompts.build_retention_user(state, units),
            min(4000, 150 * len(units) + 200))
        try:
            return prompts.parse_retention(text, [u["unit_id"] for u in units])
        except ValueError as exc:
            raise ProviderError(ErrorKind.TRANSIENT, f"invalid retention reply: {exc}", provider=self.name) from exc

    async def summarize(self, state, units):
        text = await self._complete(
            prompts.SUMMARY_SYSTEM_PROMPT, prompts.build_summary_user(state, units),
            min(6000, 400 * len(units) + 200))
        try:
            return prompts.parse_summaries(text, [u["unit_id"] for u in units])
        except ValueError as exc:
            raise ProviderError(ErrorKind.TRANSIENT, f"invalid summary reply: {exc}", provider=self.name) from exc

    async def claim_relation(self, statement_a, statement_b):
        from app.services import claim_equivalence as ce

        text = await self._complete(
            ce._CLASSIFY_SYSTEM_PROMPT, f'Claim A: "{statement_a}"\nClaim B: "{statement_b}"', 80)
        try:
            return _validated_relation(prompts._loads_object(text), self.name)
        except ValueError as exc:
            raise ProviderError(ErrorKind.TRANSIENT, f"invalid relation reply: {exc}", provider=self.name) from exc


    async def identity(self, kind, a, b):
        text = await self._complete(
            prompts.IDENTITY_SYSTEM_PROMPTS[kind], prompts.build_identity_user(kind, a, b), 80)
        try:
            return prompts.parse_identity(kind, prompts._loads_object(text))
        except ValueError as exc:
            raise ProviderError(ErrorKind.TRANSIENT, f"invalid identity reply: {exc}", provider=self.name) from exc


def _validated_relation(body: dict, provider: str) -> dict:
    from app.services import claim_equivalence as ce

    relation = body.get("relation")
    if relation not in ce.RELATIONS:
        raise ProviderError(ErrorKind.TRANSIENT, f"invalid relation {relation!r}", provider=provider)
    try:
        confidence = max(0.0, min(1.0, float(body.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"relation": relation, "confidence": confidence}


# --------------------------------------------------------------- factory


def _split(csv: Optional[str]) -> list[str]:
    return [p.strip().lower() for p in (csv or "").split(",") if p.strip()]


def _gemini_keys(settings) -> list[str]:
    keys = _split_keep_case(settings.gemini_api_keys)
    if settings.gemini_api_key and settings.gemini_api_key not in keys:
        keys.insert(0, settings.gemini_api_key)
    return keys


def _split_keep_case(csv: Optional[str]) -> list[str]:
    return [p.strip() for p in (csv or "").split(",") if p.strip()]


def build_provider(name: str, settings, *, timeout_s: float) -> Optional[SemanticProvider]:
    """One configured provider, or None when it has no credentials/endpoint
    (skipped -- an unconfigured provider is not an attempted failure)."""
    if name == "jev":
        if not settings.jev_base_url:
            return None
        from app.services.applicability_judge import RemoteHTTPJudge

        caps = set(_split(settings.jev_capabilities)) & ALL_CAPS
        return JEVProvider(
            RemoteHTTPJudge(settings.jev_base_url, model="jev", timeout_seconds=timeout_s,
                            api_key=settings.jev_api_key),
            caps)
    if name == "gemini":
        keys = _gemini_keys(settings)
        if not keys:
            return None
        from openai import AsyncOpenAI

        clients = [AsyncOpenAI(api_key=k, base_url=settings.semantic_gemini_base_url,
                               max_retries=0, timeout=timeout_s) for k in keys]
        return OpenAICompatProvider("gemini", clients, settings.semantic_gemini_model)
    if name == "gemma":
        model = settings.local_model_name or (settings.local_judge_model if settings.use_local_models else None)
        if not model:
            return None
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key="local", base_url=settings.local_base_url, max_retries=0, timeout=timeout_s)
        return OpenAICompatProvider("gemma", [client], model)
    log.warning("semantic: unknown provider %r in SEMANTIC_PROVIDER_* ignored", name)
    return None


def build_provider_chain(settings=None, *, timeout_s: Optional[float] = None) -> list[SemanticProvider]:
    if settings is None:
        from app.config import settings as _s
        settings = _s
    timeout_s = timeout_s if timeout_s is not None else settings.semantic_provider_timeout_ms / 1000.0
    order: list[str] = []
    for n in _split(settings.semantic_provider_primary) + _split(settings.semantic_provider_fallbacks):
        if n not in order:
            order.append(n)
    providers = []
    for n in order:
        p = build_provider(n, settings, timeout_s=timeout_s)
        if p is None:
            log.info("semantic: provider %s not configured; skipped", n)
        else:
            providers.append(p)
    return providers

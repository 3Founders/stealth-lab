"""
Debate panel agents (MVP plan, Section 7).

The engine talks only to the PanelAgent protocol and never imports a
vendor SDK. That keeps the debate logic testable offline (see
MockAgent) and makes swapping a panel seat a config change.

Heterogeneity is a correctness property here, not a preference: a panel
of three instances of one model shares its blind spots, which is exactly
the failure mode debate is supposed to catch. assert_heterogeneous()
enforces it at construction rather than trusting the roster.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from app.config import settings


# --- OpenRouter survival tuning (WAVE-3) ---------------------------------
#
# Ported from experiments/harness/openrouter_arms.py (board Lane CORE-B
# item 7: read-only reference). The pattern is re-implemented here rather
# than imported because backend/** must not reach into experiments/**.
# These are named module constants consulted at call time -- proven
# monkeypatch-retunable by tests, never inlined literals.
#
# 429 is the headline failure (the upstream shared pool saturates --
# verified live per board RUN #1); the rest are the transient set worth a
# retry. Non-retryable 4xx falls through to the next model in a seat's
# chain immediately.
OPENROUTER_RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})
BACKOFF_BASE_S = 1.5
BACKOFF_CAP_S = 60.0
MAX_ATTEMPTS_PER_MODEL = 6
REQUEST_TIMEOUT_S = 120.0

# Kept deliberately below gather_responses' default 120s outer timeout so an
# exhausted budget raises WITH its attempt trail instead of being cancelled
# mid-backoff by the outer wait_for -- a cancellation would be recorded as a
# failed turn indistinguishable from a network failure, losing the
# diagnostics entirely.
TURN_BUDGET_S = 110.0

# Verified live 2026-08-26 (gated smoke diagnosis): ox-alpha is a REASONING
# model -- under debate-length prompts (~3k tokens of system prompt) its
# chain-of-thought alone consumes ANY plain completion budget and the visible
# content comes back EMPTY (completion_tokens == max_tokens, content='').
# Capping the reasoning window leaves room for the actual JSON reply;
# verified fixing it in one probe. Models without an entry are passed
# through untouched -- the reasoning parameter is normalized away by
# OpenRouter for models that don't support it.
REASONING_BUDGET_CAPS: dict[str, dict] = {
    "ox-alpha": {"reasoning": {"max_tokens": 400}},
}


@runtime_checkable
class PanelAgent(Protocol):
    agent_id: str
    model_id: str
    family: str  # vendor/architecture family -- used for the heterogeneity check

    async def respond(self, system: str, user: str) -> str: ...


def _extract_json(text: str) -> dict[str, Any]:
    """
    Pull a JSON object out of a model response.

    Models wrap JSON in prose or fences despite instructions often enough
    that parsing must be defensive. Raises ValueError on genuine failure so
    the caller can record a malformed turn rather than crashing the debate.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"no parseable JSON object in response: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


@dataclass
class AnthropicAgent:
    agent_id: str
    model_id: str = ""
    family: str = "anthropic"
    max_tokens: int = 2000

    def __post_init__(self) -> None:
        self.model_id = self.model_id or settings.anthropic_model

    async def respond(self, system: str, user: str) -> str:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.require("anthropic_api_key"))
        msg = await client.messages.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in msg.content if block.type == "text")


@dataclass
class OpenAICompatAgent:
    """
    Covers any OpenAI-compatible endpoint. Used for OpenAI proper,
    Fireworks (Kimi K3), Gemini's compat endpoint, and local servers
    (Ollama, LM Studio, vLLM) -- which differ only in base_url and key.

    `api_key_field=None` means no key is required, which is how local
    servers work. They still want *some* string in the header, hence the
    placeholder.
    """

    agent_id: str
    model_id: str
    family: str
    api_key_field: Optional[str] = None
    base_url: Optional[str] = None
    max_tokens: int = 2000

    async def respond(self, system: str, user: str) -> str:
        from openai import AsyncOpenAI

        key = (
            settings.require(self.api_key_field)
            if self.api_key_field
            else "not-needed-for-local"
        )
        client = AsyncOpenAI(api_key=key, base_url=self.base_url)
        resp = await client.chat.completions.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


@dataclass
class MockAgent:
    """Scripted agent for offline tests. Returns queued responses in order."""

    agent_id: str
    responses: list[str]
    model_id: str = "mock"
    family: str = "mock"
    _index: int = 0

    async def respond(self, system: str, user: str) -> str:
        if self._index >= len(self.responses):
            return json.dumps({"action": "pass", "content": "nothing further"})
        out = self.responses[self._index]
        self._index += 1
        return out


def openrouter_headers(api_key: str) -> dict[str, str]:
    """
    OpenRouter attribution headers. Split out for offline proof. The key
    value itself is never logged or echoed anywhere.
    """
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/backend-debate",
        "X-Title": "stealthlab-debate-panel",
    }


def httpx_openrouter_transport(
    base_url: str, api_key: str, timeout_s: float
) -> Callable[[dict], Any]:
    """
    One POST per call, HTTP status visible to the retry loop. Raw status
    codes beat SDK exception hierarchies for retry decisions -- the exact
    reasoning openrouter_arms recorded when the shared pool saturated.
    Lazy httpx import: a missing install should surface at first OpenRouter
    use with a clear ImportError, not at panel import for everyone.
    """
    import httpx

    headers = openrouter_headers(api_key)
    url = base_url.rstrip("/") + "/chat/completions"

    async def _send(payload: dict) -> tuple[int, dict]:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, headers=headers, json=payload)
            try:
                body = resp.json()
            except Exception:
                body = {}
            return resp.status_code, body

    return _send


class AllModelsFailedError(RuntimeError):
    """
    Every model in a seat's chain exhausted its attempts (or the turn
    budget ran out). Carries the per-attempt trail so a lost debate turn
    explains WHY, not just THAT -- same contract as openrouter_arms'
    error of the same name. gather_responses catches this like any other
    agent failure: one exhausted seat becomes a recorded skipped turn,
    never a crashed debate.
    """

    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        tail = "; ".join(
            f"{a['model']}#{a['attempt']}:{a.get('status') or a.get('error')}"
            for a in attempts[-6:]
        )
        super().__init__(
            f"all {len({a['model'] for a in attempts})} chain model(s) "
            f"exhausted ({len(attempts)} attempt(s)): {tail}"
        )


@dataclass
class OpenRouterAgent:
    """
    One debate seat served through OpenRouter on the founder key.

    Ports the survival strategy MEASURE proved against the saturated
    shared pool (openrouter_arms.OpenRouterClient): exponential backoff
    with FULL jitter -- uniform in [0, min(cap, base*2^attempt)), so
    concurrent seats don't re-align their retries into a thundering herd --
    on every retryable status and network error; immediate fallthrough to
    the next model on non-retryable 4xx; exhaustion raises with the whole
    attempt trail.

    `manages_own_retries` tells _call_with_retry to stand down: backoff
    already lives INSIDE this seat, and stacking the generic vendor-shaped
    rate-limit retry on top would multiply worst-case sleeps ~4x while
    hammering an already-saturated pool. Failure isolation in
    gather_responses still applies unchanged.

    `fallback_models` is deliberately left EMPTY by the factories below:
    a seat's declared `family` must stay truthful about whatever actually
    answered, and enforce_independence reasons statically over families.
    A caller who opts into a chain accepts that a served fallback may not
    belong to the family this seat declared.

    transport/sleep/rng are injectables so tests prove every timing
    behavior offline with zero network and zero wall-clock cost.
    """

    agent_id: str
    model_id: str  # primary; head of the fallback chain
    family: str
    fallback_models: tuple[str, ...] = ()
    max_tokens: int = 2000
    temperature: float = 0.2
    # OpenRouter-normalized JSON mode ("response_format": {"type":
    # "json_object"}). The debate engine parses EVERY seat reply as JSON;
    # without this, real frontier models ramble past their completion
    # budget in prose and get truncated before the object ever starts --
    # confirmed live in the first gated smoke (all three seats lost their
    # turns to truncation). The factories enable it; direct constructors
    # get raw passthrough behavior unless they opt in.
    json_mode: bool = False
    # Escape hatch for provider-specific params (e.g. reasoning limits from
    # REASONING_BUDGET_CAPS, or provider quirks discovered later).
    # Shallow-merged OVER the base payload, so it can override anything
    # except the per-model fields set inside the chain loop.
    extra_payload: dict = field(default_factory=dict)
    # None -> consult module constant TURN_BUDGET_S at call time (retunable).
    turn_budget_s: Optional[float] = None
    manages_own_retries: bool = True  # read by _call_with_retry
    transport: Optional[Callable[[dict], Any]] = None
    sleep: Optional[Callable[[float], Any]] = None
    rng: Optional[Callable[[], float]] = None

    def _effective_budget(self) -> float:
        return self.turn_budget_s if self.turn_budget_s is not None else TURN_BUDGET_S

    def _backoff_delay(self, attempt: int, rng: Callable[[], float]) -> float:
        ceiling = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** attempt))
        return rng() * ceiling

    def _chain(self) -> tuple[str, ...]:
        return (self.model_id, *self.fallback_models)

    async def respond(self, system: str, user: str) -> str:
        api_key = settings.require("openrouter_api_key")
        rng = self.rng or random.random
        sleep = self.sleep or asyncio.sleep
        if self.transport is not None:
            transport = self.transport
        else:
            transport = httpx_openrouter_transport(
                settings.openrouter_base_url, api_key, REQUEST_TIMEOUT_S
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        chain = self._chain()
        deadline = asyncio.get_running_loop().time() + self._effective_budget()
        attempts: list[dict] = []

        for chain_pos, model in enumerate(chain):
            # Past the turn budget, later chain models don't get probed at
            # all -- the only overrun left is the one already-in-flight
            # request, so the AllModelsFailedError trail survives well
            # inside gather_responses' outer timeout instead of being cut
            # off by cancellation.
            if chain_pos > 0 and asyncio.get_running_loop().time() >= deadline:
                break
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            }
            if self.json_mode:
                payload["response_format"] = {"type": "json_object"}
            if self.extra_payload:
                payload.update(self.extra_payload)
            more_models_left = chain_pos < len(chain) - 1
            for attempt in range(MAX_ATTEMPTS_PER_MODEL):
                try:
                    status, body = await transport(payload)
                except Exception as exc:  # noqa: BLE001 -- network layer;
                    # raised errors are the retryable signal, arms parity.
                    attempts.append({
                        "model": model, "attempt": attempt, "status": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    if not (more_models_left
                            or attempt < MAX_ATTEMPTS_PER_MODEL - 1):
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        break
                    await sleep(self._backoff_delay(attempt, rng))
                    continue
                if status == 200:
                    choices = body.get("choices") or [{}]
                    return (choices[0].get("message") or {}).get("content") or ""
                attempts.append({"model": model, "attempt": attempt, "status": status})
                if status in OPENROUTER_RETRYABLE_STATUSES:
                    if not (more_models_left
                            or attempt < MAX_ATTEMPTS_PER_MODEL - 1):
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        break
                    await sleep(self._backoff_delay(attempt, rng))
                    continue
                break  # non-retryable for THIS model -> next in chain
        raise AllModelsFailedError(attempts)


def local_panel() -> list[PanelAgent]:
    """
    Panel backed by a local OpenAI-compatible server.

    Model families are treated as distinct because they genuinely are --
    Llama, Qwen, and Mistral come from different labs with different
    pretraining corpora, so the heterogeneity argument (independent blind
    spots) holds here in the same way it does for the paid roster, even
    though each individual model is weaker.
    """
    models = [m.strip() for m in settings.local_panel_models.split(",") if m.strip()]
    return [
        OpenAICompatAgent(
            agent_id=f"panelist_{chr(97 + i)}",
            model_id=model,
            # Family derived from the model name's first token, so
            # llama3.2 and llama3.1 correctly count as the same family.
            family=model.split(":")[0].rstrip("0123456789.") or model,
            api_key_field=None,
            base_url=settings.local_base_url,
        )
        for i, model in enumerate(models)
    ]


def local_judge() -> PanelAgent:
    return OpenAICompatAgent(
        agent_id="judge",
        model_id=settings.local_judge_model,
        family=settings.local_judge_model.split(":")[0].rstrip("0123456789.")
        or settings.local_judge_model,
        api_key_field=None,
        base_url=settings.local_base_url,
    )


def _derive_family(model_name: str) -> str:
    """
    Best-effort family from a model name: everything before the first
    token that contains a digit. 'deepseek-v4-pro' and 'deepseek-v4-flash'
    both resolve to 'deepseek' (the 'v4' token stops collection before the
    tier name is ever considered), 'gpt-oss-120b' resolves to 'gpt-oss'
    (neither 'gpt' nor 'oss' contains a digit, '120b' does).

    Checking every token up to the first digit, rather than only the
    last, is the fix for the actual failure mode: a naive "does the last
    token have a digit" check misses tier names like 'pro' or 'flash'
    that follow a version token, which would otherwise make two releases
    of the same model look like different families.
    """
    name = model_name.split("/")[-1].lower()
    for cut in ("-instruct", "-chat", "-it"):
        if name.endswith(cut):
            name = name[: -len(cut)]

    tokens = name.split("-")
    family_tokens: list[str] = []
    for tok in tokens:
        if any(c.isdigit() for c in tok):
            break
        family_tokens.append(tok)

    # The whole name was version-like (e.g. a bare "qwen3.6" with no
    # separate family token) -- fall back to the first token rather than
    # returning an empty family.
    return "-".join(family_tokens) if family_tokens else tokens[0]


def general_compute_panel() -> list[PanelAgent]:
    """
    Panel backed by General Compute (hosted, open-weight, OpenAI-compatible).

    Model names come entirely from config -- there is no sensible hardcoded
    default, since General Compute's catalog and what a given account has
    access to both change. `assert_heterogeneous` still runs on whatever
    is configured, so a misconfiguration (three models that are actually
    the same family under different names) is caught at construction, not
    discovered later in a debate transcript.
    """
    models = [m.strip() for m in settings.general_compute_panel_models.split(",") if m.strip()]
    if len(models) < 3:
        raise ValueError(
            f"general_compute_panel_models must list at least 3 models, got {models!r}. "
            "Check https://docs.generalcompute.com for the current catalog."
        )
    return [
        OpenAICompatAgent(
            agent_id=f"panelist_{chr(97 + i)}",
            model_id=model,
            family=_derive_family(model),
            api_key_field="general_compute_api_key",
            base_url=settings.general_compute_base_url,
        )
        for i, model in enumerate(models)
    ]


def general_compute_judge() -> PanelAgent:
    model = settings.general_compute_judge_model
    if not model:
        raise ValueError(
            "general_compute_judge_model is not set. Pick a model whose family "
            "differs from all three panel models."
        )
    return OpenAICompatAgent(
        agent_id="judge",
        model_id=model,
        family=_derive_family(model),
        api_key_field="general_compute_api_key",
        base_url=settings.general_compute_base_url,
    )


def openrouter_panel() -> list[PanelAgent]:
    """
    Panel seated entirely on one OpenRouter account (founder key).

    This is the posture that makes scan -> debate -> approve runnable
    locally: four seats, four distinct model families, one credential.
    Slugs come entirely from config because availability is account-
    dependent; assert_heterogeneous still runs on whatever is configured,
    so a same-family misconfiguration fails at construction, before any
    spend. A slug the account doesn't serve degrades to recorded skipped
    turns for that seat -- visible in the debate transcript's failures,
    never silent.
    """
    models = [m.strip() for m in settings.openrouter_panel_models.split(",") if m.strip()]
    if len(models) < 3:
        raise ValueError(
            f"openrouter_panel_models must list at least 3 models, got {models!r}. "
            "Set OPENROUTER_PANEL_MODELS to slugs from https://openrouter.ai/models "
            "that your account serves."
        )
    return [
        OpenRouterAgent(
            agent_id=f"panelist_{chr(97 + i)}",
            model_id=model,
            family=_derive_family(model),
            json_mode=True,
            extra_payload=REASONING_BUDGET_CAPS.get(model, {}),
        )
        for i, model in enumerate(models)
    ]


def openrouter_judge() -> PanelAgent:
    model = settings.openrouter_judge_model
    if not model:
        raise ValueError(
            "openrouter_judge_model is not set. Pick a model whose family "
            "differs from all three panel models."
        )
    return OpenRouterAgent(
        agent_id="judge",
        model_id=model,
        family=_derive_family(model),
        json_mode=True,
        extra_payload=REASONING_BUDGET_CAPS.get(model, {}),
    )


def default_panel() -> list[PanelAgent]:
    """
    The v0 fixed roster (Section 7): three distinct model families.

    Four mutually exclusive sources, checked in order: local (free,
    weakest, for structural smoke tests), General Compute (hosted,
    open-weight, cheap, real reasoning quality), OpenRouter (four seats
    off one founder key -- the local-loop default), the paid closed
    roster (the actually-designed default). Setting two flags at once is
    refused rather than silently preferring one, so a stray leftover flag
    doesn't quietly route traffic somewhere unintended.
    """
    enabled = [
        name for name, on in (
            ("use_local_models", settings.use_local_models),
            ("use_general_compute", settings.use_general_compute),
            ("use_openrouter", settings.use_openrouter),
        ) if on
    ]
    if len(enabled) > 1:
        raise ValueError(
            f"multiple provider flags set ({', '.join(enabled)}); pick one"
        )
    if settings.use_local_models:
        return local_panel()
    if settings.use_general_compute:
        return general_compute_panel()
    if settings.use_openrouter:
        return openrouter_panel()
    return [
        AnthropicAgent(agent_id="panelist_a"),
        OpenAICompatAgent(
            agent_id="panelist_b",
            model_id=settings.fireworks_model,
            family="moonshot",
            api_key_field="fireworks_api_key",
            base_url="https://api.fireworks.ai/inference/v1",
        ),
        OpenAICompatAgent(
            agent_id="panelist_c",
            model_id=settings.openai_model,
            family="openai",
            api_key_field="openai_api_key",
        ),
    ]


def default_judge() -> PanelAgent:
    """
    The independent adjudicator (Section 7/8.1). Must not share a model
    family with any panelist -- the panel already uses all three other
    configured providers, so this is deliberately the fourth.
    """
    if settings.use_local_models:
        return local_judge()
    if settings.use_general_compute:
        return general_compute_judge()
    if settings.use_openrouter:
        return openrouter_judge()
    return OpenAICompatAgent(
        agent_id="judge",
        model_id=settings.gemini_model,
        family="google",
        api_key_field="google_api_key",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )


def default_chat_agent() -> PanelAgent:
    """
    The model that answers grounded questions in the Archive/chat surface.

    Not a debate role, so none of the heterogeneity or independence
    requirements apply here -- it's a single informational call, not an
    adversarial or adjudicative one. Reuses whichever provider is
    configured as the judge, for the same reason `default_layer2_agent`
    does: no need for a fifth distinct provider just for this.

    This exists because chat.py previously hardcoded AnthropicAgent
    directly, built before local/General Compute provider selection
    existed and never updated when it was added -- the same class of gap
    Layer 2's wiring had earlier in this project.
    """
    if settings.use_local_models:
        agent = local_judge()
    elif settings.use_general_compute:
        agent = general_compute_judge()
    elif settings.use_openrouter:
        agent = openrouter_judge()
    else:
        agent = AnthropicAgent(agent_id="chat")
        return agent
    agent.agent_id = "chat"
    return agent


def default_layer2_agent() -> PanelAgent:
    """
    The model that estimates counterfactual outcomes for Layer 2.

    Reuses the judge's provider rather than adding a fifth: the
    independence requirement that matters is between the *adjudicator* and
    the *debaters*, and Layer 2 is neither -- it's estimating what would
    have happened, not arguing for or judging a position. Adding another
    provider here would be cost and configuration for no integrity gain.
    """
    if settings.use_local_models:
        agent = local_judge()
    elif settings.use_general_compute:
        agent = general_compute_judge()
    elif settings.use_openrouter:
        agent = openrouter_judge()
    else:
        agent = OpenAICompatAgent(
            agent_id="layer2",
            model_id=settings.gemini_model,
            family="google",
            api_key_field="google_api_key",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    agent.agent_id = "layer2"
    return agent


def assert_heterogeneous(agents: list[PanelAgent]) -> None:
    """
    Enforce Section 7's heterogeneity requirement at construction.

    Checked on `family` rather than `model_id` because two models from the
    same lab share pretraining lineage and therefore correlated blind
    spots, even at different sizes.
    """
    families = [a.family for a in agents]
    if len(set(families)) < len(families):
        dupes = sorted({f for f in families if families.count(f) > 1})
        raise ValueError(
            f"panel is not heterogeneous -- repeated model families: {dupes}. "
            "A panel that shares a lineage shares its blind spots."
        )


def _is_rate_limit_error(exc: Exception) -> bool:
    """
    Provider-agnostic rate-limit detection. Deliberately string-matching
    on "429" / "rate limit" rather than importing anthropic.RateLimitError
    and openai.RateLimitError specifically -- OpenAICompatAgent covers
    several different providers (OpenAI, Fireworks, Gemini's compat
    endpoint, local servers) behind one client class, and a provider-
    specific exception type check would miss whichever ones don't map
    cleanly onto the openai SDK's own error hierarchy. Confirmed real
    against an actual failure: 'Error code: 429 - {"error": {"message":
    "Provider request failed with status 429" ...}}' from a real panel
    run against General Compute, with zero retry previously -- every
    panelist hitting this lost its entire turn for that round, silently,
    with no distinction from a genuine model failure.

    KNOWN FRAGILITY, not fixed: plain substring matching means an error
    message that happens to CONTAIN the phrase "rate limit" for an
    unrelated reason (e.g. explaining a different endpoint's limits) would
    be misclassified as retryable. Caught this exact failure mode while
    writing this function's own test (a deliberately-worded non-rate-limit
    error accidentally matched the substring). "429" is the more reliable
    of the two signals in practice -- real provider errors reliably
    include it, genuine unrelated errors essentially never do by
    coincidence.
    """
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


async def _call_with_retry(
    agent: "PanelAgent", system: str, user: str, timeout: float,
    max_retries: int = 3, base_delay: float = 2.0,
) -> str:
    """
    Retries ONLY rate-limit-shaped failures, with exponential backoff --
    a genuine model/parsing error should still surface immediately as a
    failed turn (existing behavior), not be masked behind retries that
    can't fix it. On the last attempt, whatever exception occurs
    propagates to the caller unchanged, same as before this wrapper
    existed.

    Agents carrying `manages_own_retries` (OpenRouterAgent) are passed
    straight through under the same wait_for ceiling: they retry
    status-aware internally with full-jitter backoff, and wrapping them
    in this generic vendor-shaped retry would stack two backoff ladders
    (worst case ~4x the sleeps) against an already-saturated shared pool.
    """
    if getattr(agent, "manages_own_retries", False):
        return await asyncio.wait_for(agent.respond(system, user), timeout=timeout)
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(agent.respond(system, user), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, see _is_rate_limit_error
            last_exc = exc
            if attempt >= max_retries or not _is_rate_limit_error(exc):
                raise
            delay = base_delay * (2 ** attempt)
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, satisfies type checkers


async def gather_responses(
    agents: list[PanelAgent], system: str, user: str, timeout: float = 120.0,
    on_call: Optional[Callable[[PanelAgent, str, str], "asyncio.Future"]] = None,
) -> dict[str, str | Exception]:
    """
    Query agents concurrently. One agent failing (rate limit, timeout,
    outage) must not abort the round -- the exception is returned in place
    of that agent's turn and recorded as a skipped turn. Rate-limit
    failures specifically are retried with backoff first (see
    _call_with_retry) before being allowed to count as a failed turn --
    confirmed necessary against a real run where a panelist lost most of
    its rounds to 429s with no retry at all.

    `on_call`, if given, fires once per agent immediately after its
    response, with (agent, prompt_text, response_text) -- the actual
    call site for cost recording, since this is the only place that sees
    every real request/response pair a debate round produces.
    """

    async def one(agent: PanelAgent) -> str | Exception:
        try:
            result = await _call_with_retry(agent, system, user, timeout)
            if on_call:
                await on_call(agent, system + user, result)
            return result
        except Exception as exc:  # noqa: BLE001 -- deliberately broad
            return exc

    results = await asyncio.gather(*(one(a) for a in agents))
    return dict(zip([a.agent_id for a in agents], results))

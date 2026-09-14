"""
Model-independent LLM call abstraction.

Mirrors the pattern backend/app/debate/panel.py already uses (a narrow
Protocol, vendor SDKs imported lazily inside the call) but is standalone --
this harness must not import backend/app/config.py's Settings (Postgres/
asyncpg-coupled, much larger required surface than a single API key).

Every call returns a Usage record with REAL token counts read back from the
provider response. If a provider doesn't report usage, the fields stay
None -- STEP 29 forbids fabricating token/cost numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from pokemon_stealth.config import LLM_CONFIG

# Published per-1M-token prices, USD, as of this writing. Used only to
# compute an *estimated* cost -- never presented as an authoritative bill.
_PRICE_PER_1M: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


@dataclass
class Usage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    def estimated_cost_usd(self, model_id: str) -> Optional[float]:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        prices = _PRICE_PER_1M.get(model_id)
        if prices is None:
            return None
        in_price, out_price = prices
        return (self.input_tokens / 1_000_000) * in_price + (
            self.output_tokens / 1_000_000
        ) * out_price


@dataclass
class LLMResponse:
    text: str
    usage: Usage
    model_id: str


@runtime_checkable
class Agent(Protocol):
    model_id: str

    async def respond(self, system: str, user: str, max_tokens: int) -> LLMResponse: ...


@dataclass
class AnthropicAgent:
    model_id: str = "claude-sonnet-5"

    async def respond(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        from anthropic import AsyncAnthropic

        if not LLM_CONFIG.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set -- required for AnthropicAgent."
            )
        client = AsyncAnthropic(api_key=LLM_CONFIG.anthropic_api_key)
        msg = await client.messages.create(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        usage = Usage(
            input_tokens=getattr(msg.usage, "input_tokens", None),
            output_tokens=getattr(msg.usage, "output_tokens", None),
        )
        return LLMResponse(text=text, usage=usage, model_id=self.model_id)


@dataclass
class OpenAICompatAgent:
    """OpenAI proper, or any OpenAI-compatible endpoint (local server, router)."""

    model_id: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None

    async def respond(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        from openai import AsyncOpenAI

        key = self.api_key or LLM_CONFIG.openai_api_key or "not-needed-for-local"
        client = AsyncOpenAI(api_key=key, base_url=self.base_url)
        resp = await client.chat.completions.create(
            model=self.model_id,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        usage_obj = getattr(resp, "usage", None)
        usage = Usage(
            input_tokens=getattr(usage_obj, "prompt_tokens", None) if usage_obj else None,
            output_tokens=getattr(usage_obj, "completion_tokens", None) if usage_obj else None,
        )
        return LLMResponse(text=text, usage=usage, model_id=self.model_id)


@dataclass
class MockAgent:
    """Scripted agent for offline tests. No network calls, no fabricated usage."""

    model_id: str = "mock"
    responses: list[str] = None  # type: ignore[assignment]
    _index: int = 0

    def __post_init__(self) -> None:
        if self.responses is None:
            self.responses = []

    async def respond(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        if self._index >= len(self.responses):
            raise RuntimeError("MockAgent exhausted its scripted responses")
        text = self.responses[self._index]
        self._index += 1
        return LLMResponse(text=text, usage=Usage(), model_id=self.model_id)


def make_agent(model_id: str) -> Agent:
    """
    Small factory so CLI --model flags map to a concrete Agent without the
    caller needing to know vendor wiring. "mock:<canned>" is not routed here
    -- tests construct MockAgent directly.
    """
    if model_id.startswith("claude-"):
        return AnthropicAgent(model_id=model_id)
    if model_id.startswith("gpt-") or model_id.startswith("o1") or model_id.startswith("o3"):
        return OpenAICompatAgent(model_id=model_id)
    # Anything else assumed to be an OpenAI-compatible endpoint (local model,
    # router) -- the caller is expected to have set STUDENT_MODEL_BASE_URL.
    return OpenAICompatAgent(
        model_id=model_id,
        base_url=LLM_CONFIG.student_base_url,
        api_key=LLM_CONFIG.student_api_key,
    )

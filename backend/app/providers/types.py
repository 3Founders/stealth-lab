"""Shapes shared by the provider layer (docs/provider_connections.md).

A *connection* is where calls go and under whose credential; a *unit* is one thing a caller can
ask to run, named like the recommender names it ("model|scaffold"):

  * a plain model on an OpenAI-compatible endpoint   -> unit "deepseek-v3.2|direct"
  * an AI agent with its own harness (A2A endpoint)  -> unit "gemma-4|my-coding-agent"

so the routing layer needs no special case for agents: an agent is just a unit with a scaffold.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

DIRECT = "direct"                       # the scaffold of a bare model call
KINDS = ("openai_compatible", "a2a")
CREDENTIAL_OWNERS = ("customer", "platform")


class ProviderError(Exception):
    """Base for everything the provider layer raises; the message is safe to show a caller
    (never contains a credential)."""


class ProviderCallDenied(ProviderError):
    """A policy said no before anything was sent."""


class ProviderCallFailed(ProviderError):
    """The endpoint was reached (or should have been) and the call did not succeed.

    `status` is the HTTP status when there was one; `transport` is True when the endpoint could not be reached at
    all (connection error, timeout). Together they decide whether another endpoint is worth trying
    (providers/health.py `is_outage`). `attempts` lists what was tried when several endpoints failed."""

    def __init__(self, message: str, *, status: Optional[int] = None, transport: bool = False,
                 attempts: Sequence[Mapping[str, Any]] = (), budget_timeout: bool = False):
        super().__init__(message)
        self.status = status
        self.transport = transport
        self.attempts = list(attempts)
        # the caller's own latency budget (max_latency_ms) ran out: try elsewhere, but it is not an outage of the
        # endpoint, so the endpoint is not rested for it
        self.budget_timeout = budget_timeout

    @property
    def outage(self) -> bool:
        from app.providers.health import is_outage

        return is_outage(self.status, self.transport)


@dataclass(frozen=True)
class UnitSpec:
    model: str                                  # the name routing/prices use
    scaffold: str = DIRECT
    provider_model: Optional[str] = None        # the id the endpoint expects, when it differs
    input_per_mtok: Optional[float] = None      # USD per million tokens (feeds routing_prices)
    output_per_mtok: Optional[float] = None
    cached_input_per_mtok: Optional[float] = None   # price of tokens read from the provider's prompt cache
    cache_write_input_per_mtok: Optional[float] = None   # price of tokens written to the prompt cache
    per_call_usd: Optional[float] = None        # agents that bill per task rather than per token
    path: Optional[str] = None                  # appended to the connection's base_url
    tier: Optional[str] = None                  # admin label for comparisons: "light", "standard", "flagship", ...
    max_output_tokens: Optional[int] = None

    @property
    def unit(self) -> str:
        return f"{self.model}|{self.scaffold}"

    @property
    def sent_model(self) -> str:
        return self.provider_model or self.model


@dataclass(frozen=True)
class Connection:
    connection_id: str
    kind: str                                   # one of KINDS
    base_url: str
    units: tuple[UnitSpec, ...]
    owner: str = "platform"                     # "platform" | "org:<id>" | "user:<subject>"
    provider: str = "custom"                    # the name the platform egress policy is keyed on
    credential_ref: Optional[str] = None        # "env:NAME", later "gsm:..." -- never the secret itself
    credential_owner: str = "customer"          # customer = BYOK; platform = our key (platform policy applies)
    allowed_data_classes: tuple[str, ...] = ()
    enabled: bool = True
    allow_http_loopback: bool = False           # local vLLM / dev only; never for a hosted tenant
    # Several keys for the same endpoint ("env:KEY_A", "env:KEY_B"): a key that is rate-limited (429) or refused
    # (401/402/403) is rested and the next one is used for the same call (providers/health.py). With none, the
    # single `credential_ref` is used as before.
    credential_refs: tuple[str, ...] = ()
    timeout_s: Optional[float] = None           # per-call limit for this endpoint (default adapters.TIMEOUT_S)
    slow_ms: Optional[int] = None               # typical latency above this marks the endpoint slow (tried last)

    def spec_for(self, unit: str) -> Optional[UnitSpec]:
        return next((u for u in self.units if u.unit == unit), None)

    @property
    def keys(self) -> tuple[Optional[str], ...]:
        """The credential references to try, in order; (None,) for an endpoint that needs no credential."""
        if self.credential_refs:
            return tuple(self.credential_refs)
        return (self.credential_ref,)


@dataclass(frozen=True)
class CallRequest:
    prompt: str
    system: Optional[str] = None
    max_tokens: int = 1024
    temperature: Optional[float] = None
    data_class: str = "USER_PRIVATE"            # the most conservative private class, unless the caller says more


@dataclass(frozen=True)
class CallResult:
    unit: str
    connection_id: str
    text: str
    tokens_in: Optional[int] = None             # FRESH input only: never includes cache reads or writes
    tokens_out: Optional[int] = None
    tokens_cache_read: Optional[int] = None
    tokens_cache_write: Optional[int] = None
    cost_usd: Optional[float] = None
    cost_source: Optional[str] = None           # "provider" (reported by the endpoint) | "declared" (tokens x declared price)
    latency_ms: Optional[int] = None
    finish_reason: Optional[str] = None
    state: Optional[str] = None                 # agents: the task state when it is not simply "completed"
    extra: Mapping[str, Any] = field(default_factory=dict)


def unit_of(model: str, scaffold: Optional[str]) -> str:
    return model if "|" in model else f"{model}|{scaffold or DIRECT}"


def estimate_tokens(*texts: Optional[str]) -> int:
    """A deliberately rough input estimate (about 3.5 characters a token) used only to refuse a
    call whose worst case already exceeds the caller's cost cap."""
    return int(sum(len(t or "") for t in texts) / 3.5) + 1


def worst_case_cost(spec: UnitSpec, request: CallRequest) -> Optional[float]:
    """Upper bound in USD, or None when the unit has no price (then no cap can be enforced)."""
    if spec.per_call_usd is not None:
        return float(spec.per_call_usd)
    if spec.input_per_mtok is None or spec.output_per_mtok is None:
        return None
    cap = min(request.max_tokens, spec.max_output_tokens or request.max_tokens)
    return (estimate_tokens(request.prompt, request.system) * spec.input_per_mtok + cap * spec.output_per_mtok) / 1e6


def tokens_cost(spec: UnitSpec, tokens_in: Optional[int], tokens_out: Optional[int],
                tokens_cache_read: Optional[int] = None) -> Optional[float]:
    """Dollars at the unit's DECLARED prices. Cache reads cost the declared cached price; with none declared they are
    charged at the full input price (an over-estimate, never an under-estimate)."""
    if spec.per_call_usd is not None:
        return float(spec.per_call_usd)
    if spec.input_per_mtok is None or spec.output_per_mtok is None or tokens_in is None or tokens_out is None:
        return None
    cached = tokens_cache_read or 0
    cached_price = spec.cached_input_per_mtok if spec.cached_input_per_mtok is not None else spec.input_per_mtok
    return (tokens_in * spec.input_per_mtok + cached * cached_price + tokens_out * spec.output_per_mtok) / 1e6


def as_tuple(value: Optional[Sequence[str]]) -> tuple[str, ...]:
    return tuple(value or ())

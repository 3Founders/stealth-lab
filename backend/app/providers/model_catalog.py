"""The open-model catalog: what each model IS (licence, origin, context) and, once an operator has verified it, where it is
served and at what price.

Why it exists: connections (registry.py) say where calls go; this says which models are worth routing to and under
what terms. Keeping both in one reviewed place stops prices and licences from being copied by hand into every
connection record.

Honest scope limits:
  * The seed entries below carry model METADATA only. No provider offering, provider-side model id or price is seeded:
    those differ per provider and per account, change often, and a wrong one either breaks a call or misreports cost.
    An operator supplies offerings through the file named by STEALTH_MODEL_CATALOG_FILE after reading the provider's
    own page; an offering without both prices produces no unit (the recommender cannot rank an unpriced unit, and an
    org budget refuses one).
  * `licence` and `origin` come from vendor or secondary reports and are flagged `licence_verified=False` until a
    person has read the model card. The loader refuses an entry with no licence text at all.
  * `tool_calling_verified` is False until our own harness has measured the model in a tool loop; leaderboard numbers do
    not set it.
Nothing here makes a savings or quality claim.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from app.providers.types import DIRECT

FILE_ENV = "STEALTH_MODEL_CATALOG_FILE"
ORIGINS = ("US", "EU", "IN", "CN", "OTHER", "UNKNOWN")


@dataclass(frozen=True)
class Offering:
    """One provider serving one model."""
    connection_id: str                          # the connection (registry.py) that reaches this provider
    provider_model: str                         # the id THAT provider expects
    input_per_mtok: Optional[float] = None      # USD per million tokens, as listed by the provider
    output_per_mtok: Optional[float] = None
    cached_input_per_mtok: Optional[float] = None
    max_output_tokens: Optional[int] = None
    price_source: str = "unverified"            # where the price was read, and when

    @property
    def priced(self) -> bool:
        return self.input_per_mtok is not None and self.output_per_mtok is not None


@dataclass(frozen=True)
class ModelEntry:
    model: str                                  # the name routing and prices use
    family: str
    licence: str                                # as stated by the vendor, or "unverified"
    origin: str                                 # one of ORIGINS
    licence_verified: bool = False
    context_tokens: Optional[int] = None
    tier: Optional[str] = None                  # "light" | "standard" | "flagship" (admin label)
    tool_calling_verified: bool = False         # measured in OUR harness, not taken from a leaderboard
    notes: str = ""
    offerings: tuple[Offering, ...] = field(default_factory=tuple)


# Metadata only; see the module docstring for why no offerings are seeded.
SEED: tuple[ModelEntry, ...] = (
    ModelEntry("glm-5.3", "glm", "unverified", "CN", tier="flagship", context_tokens=1_000_000,
               notes="GLM-5.2 was MIT; 5.3 reportedly dropped it. Read the Hugging Face model card first."),
    ModelEntry("deepseek-v4-pro", "deepseek", "MIT (reported)", "CN", tier="flagship", context_tokens=1_000_000),
    ModelEntry("deepseek-v3.2", "deepseek", "MIT (reported)", "CN", tier="standard"),
    ModelEntry("kimi-k2", "kimi", "unverified", "CN", tier="flagship",
               notes="Later K2.x releases are reported; confirm the exact id and licence before offering."),
    ModelEntry("minimax-m2.5", "minimax", "unverified", "CN", tier="standard"),
    ModelEntry("qwen3-coder", "qwen", "Apache-2.0 (reported)", "CN", tier="standard"),
    ModelEntry("gpt-oss-120b", "gpt-oss", "Apache-2.0 (reported)", "US", tier="standard"),
)


def _number(model: str, name: str, value: Any, *, integer: bool = False) -> Optional[float]:
    if value is None:
        return None
    ok = isinstance(value, int) if integer else isinstance(value, (int, float))
    if isinstance(value, bool) or not ok or value < 0:
        raise ValueError(f"{model}: {name} must be a non-negative {'integer' if integer else 'number'}")
    return value if integer else float(value)


def offering_from_dict(model: str, raw: Mapping[str, Any]) -> Offering:
    connection_id, provider_model = str(raw.get("connection_id") or "").strip(), str(raw.get("provider_model") or "").strip()
    if not connection_id or not provider_model:
        raise ValueError(f"{model}: an offering needs connection_id and provider_model")
    prices = {k: _number(model, k, raw.get(k)) for k in ("input_per_mtok", "output_per_mtok", "cached_input_per_mtok")}
    if (prices["input_per_mtok"] is None) != (prices["output_per_mtok"] is None):
        raise ValueError(f"{model}: give both input_per_mtok and output_per_mtok, or neither")
    return Offering(connection_id=connection_id, provider_model=provider_model, **prices,
                    max_output_tokens=_number(model, "max_output_tokens", raw.get("max_output_tokens"), integer=True),
                    price_source=str(raw.get("price_source") or "unverified"))


def entry_from_dict(raw: Mapping[str, Any]) -> ModelEntry:
    """Validate one catalog record. Raises ValueError with a message an operator can act on."""
    model = str(raw.get("model") or "").strip()
    if not model or "|" in model:
        raise ValueError("a catalog entry needs a model name without '|'")
    licence = str(raw.get("licence") or "").strip()
    if not licence:
        raise ValueError(f"{model}: licence is required (write \"unverified\" if you have not read the model card)")
    origin = str(raw.get("origin") or "UNKNOWN")
    if origin not in ORIGINS:
        raise ValueError(f"{model}: origin must be one of {ORIGINS}")
    offerings = tuple(offering_from_dict(model, o) for o in raw.get("offerings") or ())
    if len({(o.connection_id, o.provider_model) for o in offerings}) != len(offerings):
        raise ValueError(f"{model}: duplicate offerings")
    return ModelEntry(model=model, family=str(raw.get("family") or model), licence=licence, origin=origin,
                      licence_verified=bool(raw.get("licence_verified", False)),
                      context_tokens=_number(model, "context_tokens", raw.get("context_tokens"), integer=True),
                      tier=raw.get("tier"), tool_calling_verified=bool(raw.get("tool_calling_verified", False)),
                      notes=str(raw.get("notes") or ""), offerings=offerings)


def load_catalog(path: Optional[str] = None) -> list[ModelEntry]:
    """The seed metadata, with any operator-supplied entries layered on top (same model name replaces the seed)."""
    entries = {e.model: e for e in SEED}
    path = path or os.environ.get(FILE_ENV)
    if path:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        for raw in (doc.get("models") if isinstance(doc, dict) else doc) or ():
            entry = entry_from_dict(raw)
            entries[entry.model] = entry
    return list(entries.values())


def units_for(connection_id: str, entries: Sequence[ModelEntry]) -> tuple[list[dict[str, Any]], list[str]]:
    """The `units` list for one connection record, from the catalog, and the models skipped because their offering has
    no price. Pasting the result into a connection keeps prices in one place."""
    units: list[dict[str, Any]] = []
    skipped: list[str] = []
    for entry in entries:
        for off in entry.offerings:
            if off.connection_id != connection_id:
                continue
            if not off.priced:
                skipped.append(entry.model)
                continue
            unit: dict[str, Any] = {"model": entry.model, "scaffold": DIRECT, "provider_model": off.provider_model,
                                    "input_per_mtok": off.input_per_mtok, "output_per_mtok": off.output_per_mtok}
            for key, value in (("cached_input_per_mtok", off.cached_input_per_mtok), ("tier", entry.tier),
                               ("max_output_tokens", off.max_output_tokens)):
                if value is not None:
                    unit[key] = value
            units.append(unit)
    return units, skipped


def default_models_value(entries: Sequence[ModelEntry]) -> str:
    """STEALTH_DEFAULT_MODELS for every model that has at least one priced offering (routing/plan.py reads it)."""
    return ",".join(f"{e.model}|{DIRECT}" for e in entries if any(o.priced for o in e.offerings))

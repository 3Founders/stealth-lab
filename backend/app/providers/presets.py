"""Provider presets: what the Providers page offers so an admin does not have to type a base URL from memory.

A preset is a starting point, not a connection and not a claim. It carries NO price (a provider's listed price is
the admin's to enter: without one the budget check blocks every call, and a price we ship would go stale) and NO
data-handling declaration (zdr, no_training, region and dpa_signed stay unknown until the admin declares them from the
provider's own terms and a signed agreement; the `notes` only say what the provider's pages said when they were read).

`verified` is False where the base URL was not read from the provider's own docs in this repo. docs/provider_catalog.md
holds the dated reading for the four it covers; the others are the providers' commonly documented OpenAI-compatible
bases and must be confirmed (one test call) before an org depends on them.
"""
from __future__ import annotations

from typing import Any

from app.providers.model_catalog import SEED

PRESETS: tuple[dict[str, Any], ...] = (
    {"provider": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "verified": True,
     "notes": "Zero data retention is a per-request setting (request_extras provider.zdr), not a property of the account."},
    {"provider": "general-compute", "name": "General Compute", "base_url": "https://api.generalcompute.com/v1",
     "verified": True, "notes": "Request a data processing agreement."},
    {"provider": "deepinfra", "name": "DeepInfra", "base_url": "https://api.deepinfra.com/v1/openai", "verified": True,
     "notes": "Docs say memory-only inference with limited debug storage; no explicit zero-retention promise. US "
              "datacenters, no region pinning on serverless."},
    {"provider": "novita", "name": "Novita", "base_url": "https://api.novita.ai/openai/v1", "verified": True,
     "notes": "Its docs also show an older /v3/openai base. Datacenter countries are not disclosed."},
    {"provider": "together", "name": "Together AI", "base_url": "https://api.together.xyz/v1", "verified": False,
     "notes": "Confirm the base URL and its data-handling terms on the provider's own pages."},
    {"provider": "fireworks", "name": "Fireworks AI", "base_url": "https://api.fireworks.ai/inference/v1",
     "verified": False, "notes": "Confirm the base URL and its data-handling terms on the provider's own pages."},
    {"provider": "groq", "name": "Groq", "base_url": "https://api.groq.com/openai/v1", "verified": False,
     "notes": "Confirm the base URL and its data-handling terms on the provider's own pages."},
    {"provider": "cerebras", "name": "Cerebras", "base_url": "https://api.cerebras.ai/v1", "verified": False,
     "notes": "Confirm the base URL and its data-handling terms on the provider's own pages."},
)


def presets() -> dict[str, Any]:
    """The preset providers and the catalog's model names, for a form's dropdowns. No prices, no declarations."""
    return {
        "providers": [dict(p) for p in PRESETS],
        "models": [{"model": e.model, "family": e.family, "tier": e.tier, "origin": e.origin,
                    "licence": e.licence, "licence_verified": e.licence_verified, "notes": e.notes} for e in SEED],
        "declarations": ["zdr", "no_training", "region", "dpa_signed"],
        "note": "Prices and data-handling declarations are not preset: enter them from the provider's own pages. "
                "A declaration left out means unknown.",
    }

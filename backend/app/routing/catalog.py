"""Model cards for models nobody has told routing about yet, so a model added tomorrow is ranked from day one.

A candidate's starting ability comes from its card (cards.prior_mean): release date, prices, size, reasoning,
context and family. The bundle and the database hold cards only for the models of the last import, so a model
released after it had none: no list price, and recommend() excluded it ("no model card with a list price").
For each candidate still without a card, in this order:

  1. OpenRouter's live catalogue (`/api/v1/models`, public, no key), fetched at most once a day per process;
  2. the caller's own description of it (`model_constraints.cards`, sent by kell from its exec.json profile:
     the prices it pays, and what it knows of the model), with the size read off the name and the family from it,
     so `glm-6-flash` starts near the GLM flash models the fit has seen.

A model that is removed simply stops being a candidate: every model's terms are its own, so nothing else moves.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import replace
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from app.routing import cards as cardlib

log = logging.getLogger(__name__)

URL = os.environ.get("STEALTH_ROUTING_CATALOG_URL", "https://openrouter.ai/api/v1/models")
TTL_S = 24 * 3600
RETRY_S = 3600                   # after a failed fetch: try again in an hour, route without it meanwhile
TIMEOUT_S = 5.0
_state: dict[str, Any] = {"at": 0.0, "ok": False, "cards": {}}
_lock = asyncio.Lock()


def _enabled() -> bool:
    return os.environ.get("STEALTH_ROUTING_CATALOG", "on").lower() not in ("off", "0", "false")


async def _fetch(fetch: Any = None) -> list[dict]:
    if fetch is not None:
        return await fetch(URL)
    import httpx

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        return resp.json()["data"]


async def live_cards(*, fetch: Any = None, now: Optional[float] = None) -> dict[str, cardlib.ModelCard]:
    """OpenRouter's catalogue as cards, cached for a day ({} while it cannot be reached)."""
    from app.routing import public_evidence as pe

    now = time.time() if now is None else now
    age = now - _state["at"]
    if _state["at"] and age < (TTL_S if _state["ok"] else RETRY_S):
        return _state["cards"]
    async with _lock:
        if _state["at"] and now - _state["at"] < (TTL_S if _state["ok"] else RETRY_S):
            return _state["cards"]
        try:
            cards = {c.model_key: c for c in pe.openrouter_cards_from(await _fetch(fetch))}
            _state.update(at=now, ok=True, cards=cards)
        except Exception as exc:  # noqa: BLE001 -- the catalogue is an addition; routing works without it
            log.warning("routing catalogue fetch failed: %s", exc)
            _state.update(at=now, ok=False)
        return _state["cards"]


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def hint_card(model: str, hint: Mapping[str, Any]) -> Optional[cardlib.ModelCard]:
    """A card from the caller's description of a model it runs (prices per million tokens; optional
    release_date, context_k, reasoning, open_weights, params_b, family). None without both prices: the
    plan cannot cost it."""
    from app.routing import public_evidence as pe

    price_in, price_out = _num(hint.get("price_in")), _num(hint.get("price_out"))
    if price_in is None or price_out is None or price_in < 0 or price_out < 0:
        return None
    total, active = cardlib.params_from_name(model)
    released = None
    if hint.get("release_date"):
        try:
            released = date.fromisoformat(str(hint["release_date"])[:10])
        except ValueError:
            released = None
    reasoning = hint.get("reasoning")
    if isinstance(reasoning, str):
        reasoning = cardlib.EFFORT.get(reasoning.lower())
    return cardlib.ModelCard(
        model_key=cardlib.canonical_key(model), family=str(hint.get("family") or pe._family(model)),
        release_date=released, open_weights=None if hint.get("open_weights") is None else bool(hint["open_weights"]),
        params_b=_num(hint.get("params_b")) or total, active_params_b=_num(hint.get("active_params_b")) or active,
        reasoning=_num(reasoning), context_k=_num(hint.get("context_k")),
        price_in=price_in, price_out=price_out, price_cached=_num(hint.get("price_cached")),
        aliases=(model,), source="caller")


async def fill_missing(models: Sequence[str], cards: Mapping[str, cardlib.ModelCard],
                       hints: Optional[Mapping[str, Any]] = None, *,
                       fetch: Any = None) -> tuple[dict[str, cardlib.ModelCard], dict[str, str]]:
    """(cards with a card added for every model in `models` that had none and can get one, {model: where
    its card came from}). Known models are untouched: the import's card always wins."""
    out = dict(cards)
    missing = [m for m in dict.fromkeys(models) if cardlib.resolve(m, out) is None]
    sources: dict[str, str] = {}
    if not missing:
        return out, sources
    live = await live_cards(fetch=fetch) if fetch is not None or _enabled() else {}
    for m in missing:
        card = cardlib.resolve(m, live)
        if card is not None and card.price_in is not None and card.price_out is not None:
            out[card.model_key] = cardlib.with_aliases(card, m)
            sources[m] = "openrouter_live"
            continue
        hint = (hints or {}).get(m)
        made = hint_card(m, hint) if isinstance(hint, Mapping) else None
        if made is not None:
            if card is not None:                  # the catalogue knows it but has no price: keep what it knows
                made = replace(card, price_in=made.price_in, price_out=made.price_out, price_cached=made.price_cached,
                               aliases=card.aliases + (m,), source="openrouter_live+caller")
            out[made.model_key] = made
            sources[m] = made.source
    return out, sources

"""Where connections come from.

The seam is `ConnectionStore`: anything that can list the connections one caller may use. Three
sources are meant to plug in over time; only the first exists today.

  * a JSON file named by STEALTH_PROVIDER_CONNECTIONS_FILE (one operator, or a trial);
  * a database table the Providers page writes (org admins add a connection, members inherit it);
  * an agent registry (A2A agent cards) that turns discovered agents into units.

`visible_connections(scope)` merges every registered store; the first store to list a
connection_id wins, so an org's own connection can shadow a platform default. Visibility rules
live in `visible()` and are the only place that decides who may use a connection:

  platform      every caller
  org:<id>      members of that org
  user:<sub>    that user only
  unrestricted  internal / stdio callers (no network principal exists to check)
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping, Optional, Protocol, Sequence
from urllib.parse import urlparse

from app.providers import adapters
from app.providers.types import (CREDENTIAL_OWNERS, DIRECT, Connection, ProviderCallDenied, UnitSpec, as_tuple)
from app.providers.url_guard import validate_url_shape
from app.services.access import AccessScope

FILE_ENV = "STEALTH_PROVIDER_CONNECTIONS_FILE"


class ConnectionStore(Protocol):
    async def list_connections(self, scope: AccessScope) -> Sequence[Connection]: ...


def visible(conn: Connection, scope: AccessScope) -> bool:
    if not conn.enabled:
        return False
    if scope.is_unrestricted or conn.owner == "platform":
        return True
    kind, _, ident = conn.owner.partition(":")
    if kind == "org":
        return ident in scope.org_ids
    if kind == "user":
        return scope.viewer_id is not None and ident == scope.viewer_id
    return False


class StaticConnectionStore:
    def __init__(self, connections: Sequence[Connection]):
        self._connections = tuple(connections)

    async def list_connections(self, scope: AccessScope) -> Sequence[Connection]:
        return [c for c in self._connections if visible(c, scope)]


# ------------------------------------------------------------------ loading

def _unit(raw: Mapping[str, Any]) -> UnitSpec:
    model, scaffold = str(raw.get("model") or ""), str(raw.get("scaffold") or DIRECT)
    if not model or "|" in model or "|" in scaffold:
        raise ValueError("a unit needs a model (and optional scaffold) without '|'")
    prices = [raw.get(k) for k in ("input_per_mtok", "output_per_mtok", "per_call_usd", "cached_input_per_mtok",
                                         "cache_write_input_per_mtok")]
    if any(p is not None and float(p) < 0 for p in prices):
        raise ValueError(f"unit {model}: prices cannot be negative")
    if (raw.get("input_per_mtok") is None) != (raw.get("output_per_mtok") is None):
        raise ValueError(f"unit {model}: give both input_per_mtok and output_per_mtok, or neither")
    tier = raw.get("tier")
    if tier is not None and not (isinstance(tier, str) and re.fullmatch(r"[a-z0-9_-]{1,32}", tier)):
        raise ValueError(f"unit {model}: tier must be 1-32 characters of a-z, 0-9, '_' or '-'")
    return UnitSpec(
        tier=tier, model=model, scaffold=scaffold, provider_model=raw.get("provider_model"),
        input_per_mtok=None if raw.get("input_per_mtok") is None else float(raw["input_per_mtok"]),
        output_per_mtok=None if raw.get("output_per_mtok") is None else float(raw["output_per_mtok"]),
        per_call_usd=None if raw.get("per_call_usd") is None else float(raw["per_call_usd"]),
        cached_input_per_mtok=None if raw.get("cached_input_per_mtok") is None else float(raw["cached_input_per_mtok"]),
        cache_write_input_per_mtok=(None if raw.get("cache_write_input_per_mtok") is None
                                    else float(raw["cache_write_input_per_mtok"])),
        path=raw.get("path"), max_output_tokens=raw.get("max_output_tokens"))


# Body fields a connection record may not set: the adapter owns them, and an override could silently change what is
# sent (the model that runs, the prompt, streaming, tools, or the caps the cost check relied on).
PROTECTED_BODY_KEYS = frozenset({"model", "messages", "stream", "stream_options", "tools", "tool_choice",
                                 "functions", "function_call", "max_tokens", "max_completion_tokens", "temperature"})
_REGION = re.compile(r"[a-z]{2,8}(-[a-z0-9]{1,8})?")


def _tri_state(cid: str, name: str, value: Any) -> Optional[bool]:
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{cid}: {name} must be true, false or absent (unknown)")
    return value


def _region(cid: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _REGION.fullmatch(value):
        raise ValueError(f"{cid}: region must be lowercase like \"us\", \"eu\", \"in\" or \"unknown\"")
    return value


def _is_openrouter(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def _request_extras(cid: str, kind: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if kind != "openai_compatible":
        raise ValueError(f"{cid}: request_extras only applies to openai_compatible connections")
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"{cid}: request_extras must be an object")
    clash = sorted(PROTECTED_BODY_KEYS & set(value))
    if clash:
        raise ValueError(f"{cid}: request_extras cannot set {', '.join(clash)} (the adapter owns those fields)")
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{cid}: request_extras must be plain JSON") from exc
    return json.loads(json.dumps(value))


def connection_from_dict(raw: Mapping[str, Any]) -> Connection:
    """Validate one connection record. Raises ValueError with a message an admin can act on."""
    cid = str(raw.get("connection_id") or "").strip()
    if not cid:
        raise ValueError("connection_id is required")
    kind = str(raw.get("kind") or "")
    if kind not in adapters.ADAPTERS:
        raise ValueError(f"{cid}: kind must be one of {sorted(adapters.ADAPTERS)}")
    owner = str(raw.get("owner") or "platform")
    if owner != "platform" and owner.partition(":")[0] not in ("org", "user"):
        raise ValueError(f"{cid}: owner must be 'platform', 'org:<id>' or 'user:<subject>'")
    credential_owner = str(raw.get("credential_owner") or "customer")
    if credential_owner not in CREDENTIAL_OWNERS:
        raise ValueError(f"{cid}: credential_owner must be one of {CREDENTIAL_OWNERS}")
    units = tuple(_unit(u) for u in raw.get("units") or ())
    if not units:
        raise ValueError(f"{cid}: at least one unit is required")
    if len({u.unit for u in units}) != len(units):
        raise ValueError(f"{cid}: duplicate units")
    allow_http = bool(raw.get("allow_http_loopback", False))
    try:
        validate_url_shape(str(raw.get("base_url") or ""), allow_http_loopback=allow_http)
    except ProviderCallDenied as exc:
        raise ValueError(f"{cid}: base_url: {exc}") from exc
    refs = raw.get("credential_refs")
    if refs is not None and (not isinstance(refs, (list, tuple)) or not all(isinstance(r, str) and ":" in r for r in refs)):
        raise ValueError(f"{cid}: credential_refs must be a list of references like \"env:NAME\"")
    if refs and raw.get("credential_ref"):
        raise ValueError(f"{cid}: give credential_ref or credential_refs, not both")
    timeout_s, slow_ms = raw.get("timeout_s"), raw.get("slow_ms")
    if timeout_s is not None and not (isinstance(timeout_s, (int, float)) and 1 <= float(timeout_s) <= 600):
        raise ValueError(f"{cid}: timeout_s must be between 1 and 600 seconds")
    if slow_ms is not None and not (isinstance(slow_ms, int) and slow_ms > 0):
        raise ValueError(f"{cid}: slow_ms must be a positive integer (milliseconds)")
    flags = {name: _tri_state(cid, name, raw.get(name)) for name in ("zdr", "no_training")}
    region = _region(cid, raw.get("region"))
    dpa = raw.get("dpa_signed", False)
    if not isinstance(dpa, bool):
        raise ValueError(f"{cid}: dpa_signed must be true or false")
    extras = _request_extras(cid, kind, raw.get("request_extras"))
    routing = extras.get("provider")
    if (flags["zdr"] is True and _is_openrouter(str(raw["base_url"]))
            and not (isinstance(routing, dict) and routing.get("zdr") is True)):
        raise ValueError(f"{cid}: zdr is true on OpenRouter, so request_extras.provider.zdr must be true as well "
                         "(otherwise OpenRouter may route to an endpoint that keeps data)")
    return Connection(
        connection_id=cid, kind=kind, base_url=str(raw["base_url"]), units=units, owner=owner,
        provider=str(raw.get("provider") or cid), credential_ref=raw.get("credential_ref"),
        credential_owner=credential_owner, allowed_data_classes=as_tuple(raw.get("allowed_data_classes")),
        enabled=bool(raw.get("enabled", True)), allow_http_loopback=allow_http,
        credential_refs=tuple(refs or ()), timeout_s=None if timeout_s is None else float(timeout_s),
        slow_ms=slow_ms, zdr=flags["zdr"], no_training=flags["no_training"], region=region, dpa_signed=dpa,
        request_extras=extras)


def load_connections_file(path: str) -> list[Connection]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return [connection_from_dict(c) for c in (doc.get("connections") if isinstance(doc, dict) else doc) or ()]


# ------------------------------------------------------------------ registered stores

_STORES: dict[str, ConnectionStore] = {}
_file_cache: tuple[Optional[tuple[str, float]], list[Connection]] = (None, [])


def register_connection_store(name: str, store: ConnectionStore) -> None:
    _STORES[name] = store


def unregister_connection_store(name: str) -> None:
    _STORES.pop(name, None)


def file_configured() -> bool:
    return bool(os.environ.get(FILE_ENV))


def _file_connections() -> list[Connection]:
    """Reload only when the file changed, so an operator edit takes effect without a restart. A broken
    file raises: failing loudly beats serving a half-applied configuration."""
    global _file_cache
    path = os.environ.get(FILE_ENV)
    if not path:
        return []
    stamp = (path, os.path.getmtime(path))
    if _file_cache[0] != stamp:
        _file_cache = (stamp, load_connections_file(path))
    return _file_cache[1]


def configured() -> bool:
    return file_configured() or bool(_STORES)


async def visible_connections(scope: AccessScope) -> list[Connection]:
    seen: dict[str, Connection] = {}
    stores: list[ConnectionStore] = list(_STORES.values())
    if file_configured():
        stores.append(StaticConnectionStore(_file_connections()))
    for store in stores:
        for conn in await store.list_connections(scope):
            seen.setdefault(conn.connection_id, conn)
    return list(seen.values())

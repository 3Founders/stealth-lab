"""An organisation's own provider keys, entered once by its admins and served to its members through the governed gateway.

Why: with developer-held keys each of 2,000 people sets an environment variable, and the company can neither see nor
limit what is spent. Here an admin stores the company's key (encrypted, app/services/secret_box.py); a developer's agent
sends its kell token to `/v1/chat/completions`; the gateway resolves the org's key at call time, applies the org's
policy and budget, and writes the ledger row. The key never reaches a developer's machine.

Three pieces:
  * put / list / delete  the admin operations (owner or admin; audited on the same transaction as the change);
  * OrgKeyStore          a providers.registry ConnectionStore: the enabled rows of the organisations a caller belongs
                         to, as Connections owned by `org:<id>` (so only that org's members can use them);
  * OrgKeyResolver       the `orgkey:` secret scheme: decrypts one row, at call time, for one request.

Credential ownership is `customer`: the key is the organisation's own, so the connection's `allowed_data_classes` is the
organisation's own risk decision and the platform egress policy (for platform-owned keys) does not apply. The key is never
returned by any route and never logged; the dashboard sees a hint (last four characters).

Needs DEPLOYMENT_MODE=shared (see OrgKeyStore): the key is never served ungoverned.

Limits: no key rotation job and no test call on save (the join self-test is the first real call); see secret_box.py.
"""
from __future__ import annotations

import json
import re
from typing import Any, Collection, Mapping, Optional, Sequence

from app.config import settings
from app.providers import registry
from app.providers.secrets import register_secret_resolver
from app.providers.types import Connection, ProviderCallDenied
from app.services import org_governance as gov
from app.services import secret_box
from app.services.access import AccessScope, tenant_transaction

SCHEME = "orgkey"
DEFAULT_DATA_CLASSES = ("USER_PRIVATE",)
_CID = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
_DECLARATIONS = ("zdr", "no_training", "region", "dpa_signed")
# per-model hints the developer's agent needs (see packaging/npm/lib/executors/profile.mjs); the gateway ignores them
CLIENT_FLAGS = ("no_system_role", "vision", "strong", "first")


def credential_ref(org_id: str, connection_id: str) -> str:
    return f"{SCHEME}:{org_id}:{connection_id}"


def _record(org_id: str, connection_id: str, *, provider: str, base_url: str, units: Any,
            data_classes: Sequence[str], declarations: Mapping[str, Any]) -> dict[str, Any]:
    return {"connection_id": connection_id, "kind": "openai_compatible", "base_url": base_url, "provider": provider,
            "owner": f"org:{org_id}", "credential_ref": credential_ref(org_id, connection_id),
            "credential_owner": "customer", "allowed_data_classes": list(data_classes), "units": units,
            **{k: declarations[k] for k in _DECLARATIONS if k in declarations}}


def _clean_units(units: Any) -> list[dict[str, Any]]:
    if not isinstance(units, list) or not units:
        raise gov.Invalid("units must be a non-empty list of {model, provider_model?, input_per_mtok, output_per_mtok}")
    out = []
    for u in units:
        if not isinstance(u, Mapping):
            raise gov.Invalid("each unit must be an object")
        keep = {k: u[k] for k in ("model", "provider_model", "input_per_mtok", "output_per_mtok", *CLIENT_FLAGS)
                if u.get(k) is not None}
        if any(not isinstance(keep[k], bool) for k in CLIENT_FLAGS if k in keep):
            raise gov.Invalid(f"unit {keep.get('model', '?')}: {', '.join(CLIENT_FLAGS)} must be true or false")
        if "input_per_mtok" not in keep or "output_per_mtok" not in keep:
            raise gov.Invalid(f"unit {keep.get('model', '?')}: give input_per_mtok and output_per_mtok (without a price "
                              "the budget check blocks every call)")
        out.append(keep)
    return out


def validate(org_id: str, connection_id: str, *, provider: str, base_url: str, units: Any,
             data_classes: Sequence[str], declarations: Mapping[str, Any]) -> dict[str, Any]:
    """The same validation the file-configured connections get (https only, public address shape, priced units).
    Returns the cleaned units. Raises gov.Invalid with a message an admin can act on."""
    if not _CID.fullmatch(connection_id or ""):
        raise gov.Invalid("connection_id must be 1-63 characters of a-z, 0-9, '-' or '_', starting with a letter or digit")
    if not isinstance(provider, str) or not provider.strip():
        raise gov.Invalid("provider is required (the name policy lists, e.g. general-compute)")
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        raise gov.Invalid("base_url must be an https:// address")
    if not data_classes or not all(isinstance(d, str) and d.strip() and d != "*" for d in data_classes):
        raise gov.Invalid("allowed_data_classes must name each class explicitly (no wildcard)")
    cleaned = _clean_units(units)
    try:
        registry.connection_from_dict(_record(org_id, connection_id, provider=provider.strip(), base_url=base_url,
                                              units=cleaned, data_classes=data_classes, declarations=declarations))
    except ValueError as exc:
        raise gov.Invalid(str(exc)) from exc
    return cleaned


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    units = row["units"] if isinstance(row["units"], list) else json.loads(row["units"])
    return {"connection_id": row["connection_id"], "provider": row["provider"], "base_url": row["base_url"],
            "units": units, "allowed_data_classes": list(row["allowed_data_classes"]), "key_hint": row["key_hint"],
            "enabled": bool(row["enabled"]), "updated_by": row["updated_by"],
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None}


async def put_provider_key(pool: Any, *, actor_subject: str, actor_user_id: Optional[str],
                           actor_roles: Collection[str], org_id: str, connection_id: str, provider: str,
                           base_url: str, units: Any, api_key: Optional[str],
                           data_classes: Sequence[str] = DEFAULT_DATA_CLASSES,
                           declarations: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Create a connection, or update one. On update `api_key` may be omitted to keep the stored key."""
    gov._require(actor_roles, gov.ADMIN_ROLES, "connecting a provider key")
    declarations = dict(declarations or {})
    cleaned = validate(org_id, connection_id, provider=provider, base_url=base_url, units=units,
                       data_classes=data_classes, declarations=declarations)
    secret = api_key.strip() if isinstance(api_key, str) else None
    if api_key is not None and not secret:
        raise gov.Invalid("api_key is empty")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM org_provider_keys WHERE organization_id = $1::uuid AND connection_id = $2 FOR UPDATE",
            str(org_id), connection_id)
        if existing is None:
            if not secret:
                raise gov.Invalid("api_key is required for a new connection")
            await conn.execute(
                "INSERT INTO org_provider_keys (organization_id, connection_id, provider, base_url, units, "
                "allowed_data_classes, secret_ciphertext, key_hint, created_by, updated_by) "
                "VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $9)",
                str(org_id), connection_id, provider.strip(), base_url, cleaned, list(data_classes),
                secret_box.seal(secret, org_id=org_id, connection_id=connection_id), secret_box.hint(secret),
                actor_subject)
            action = "org_provider_key.create"
        else:
            if secret:
                await conn.execute(
                    "UPDATE org_provider_keys SET provider=$3, base_url=$4, units=$5::jsonb, allowed_data_classes=$6, "
                    "secret_ciphertext=$7, key_hint=$8, enabled=TRUE, updated_by=$9, updated_at=now() "
                    "WHERE organization_id=$1::uuid AND connection_id=$2",
                    str(org_id), connection_id, provider.strip(), base_url, cleaned, list(data_classes),
                    secret_box.seal(secret, org_id=org_id, connection_id=connection_id), secret_box.hint(secret),
                    actor_subject)
            else:
                await conn.execute(
                    "UPDATE org_provider_keys SET provider=$3, base_url=$4, units=$5::jsonb, allowed_data_classes=$6, "
                    "enabled=TRUE, updated_by=$7, updated_at=now() WHERE organization_id=$1::uuid AND connection_id=$2",
                    str(org_id), connection_id, provider.strip(), base_url, cleaned, list(data_classes),
                    actor_subject)
            action = "org_provider_key.update"
        # the audit row names the connection and whether the key changed, never the key
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action=action,
                         object_type="org_provider_key", object_id=connection_id, org_id=org_id,
                         details={"provider": provider.strip(), "base_url": base_url,
                                  "models": [u["model"] for u in cleaned], "key_replaced": bool(secret)})
        row = await conn.fetchrow(
            "SELECT * FROM org_provider_keys WHERE organization_id = $1::uuid AND connection_id = $2",
            str(org_id), connection_id)
    return _public(row)


async def list_provider_keys(pool: Any, *, actor_roles: Collection[str], org_id: str) -> list[dict[str, Any]]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "listing provider keys")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        rows = await conn.fetch("SELECT * FROM org_provider_keys WHERE organization_id = $1::uuid "
                                "ORDER BY connection_id", str(org_id))
    return [_public(r) for r in rows]


async def delete_provider_key(pool: Any, *, actor_subject: str, actor_user_id: Optional[str],
                              actor_roles: Collection[str], org_id: str, connection_id: str) -> None:
    """Removes the row, ciphertext included: a revoked key should not linger in the database or its backups' next copy."""
    gov._require(actor_roles, gov.ADMIN_ROLES, "removing a provider key")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        gone = await conn.fetchrow(
            "DELETE FROM org_provider_keys WHERE organization_id = $1::uuid AND connection_id = $2 RETURNING provider",
            str(org_id), connection_id)
        if gone is None:
            raise gov.NotFound("no such connection")
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id,
                         action="org_provider_key.delete", object_type="org_provider_key", object_id=connection_id,
                         org_id=org_id, details={"provider": gone["provider"]})


# ------------------------------------------------------------------ serving the keys to the gateway

class OrgKeyStore:
    """A providers.registry ConnectionStore over org_provider_keys. A caller sees only the connections of the
    organisations they belong to (their scope's org_ids); an unrestricted internal scope has no organisation to
    name, so it sees none of them."""

    def __init__(self, pool_getter):
        self._pool = pool_getter

    async def list_connections(self, scope: AccessScope) -> Sequence[Connection]:
        pool = self._pool()
        # An organization's key is only ever served through the governed path (policy, budget, ledger). The gateway governs
        # calls only in shared mode (chat_completions.py), so in any other mode the key is not served at all rather than
        # spent ungoverned. Found against a real database: in single_user mode the call succeeded with no ledger row.
        if settings.deployment_mode != "shared" or pool is None or scope.is_unrestricted or not scope.org_ids:
            return []
        out: list[Connection] = []
        for org in scope.org_ids:
            async with tenant_transaction(pool, gov._scope(org)) as conn:
                rows = await conn.fetch("SELECT * FROM org_provider_keys WHERE organization_id = $1::uuid AND enabled",
                                        str(org))
            for r in rows:
                units = r["units"] if isinstance(r["units"], list) else json.loads(r["units"])
                out.append(registry.connection_from_dict(_record(
                    str(org), r["connection_id"], provider=r["provider"], base_url=r["base_url"], units=units,
                    data_classes=list(r["allowed_data_classes"]), declarations={})))
        return out


class OrgKeyResolver:
    """`orgkey:<org id>:<connection id>` -> the decrypted key, for one request. The reference comes from a Connection the
    caller was already allowed to see, never from a request field."""

    def __init__(self, pool_getter):
        self._pool = pool_getter

    async def resolve(self, ref: str) -> str:
        _, _, rest = ref.partition(":")
        org_id, _, connection_id = rest.partition(":")
        pool = self._pool()
        if settings.deployment_mode != "shared" or pool is None or not org_id or not connection_id:
            raise ProviderCallDenied(f"credential {ref!r} cannot be resolved")
        async with tenant_transaction(pool, gov._scope(org_id)) as conn:
            row = await conn.fetchrow("SELECT secret_ciphertext FROM org_provider_keys WHERE organization_id = $1::uuid "
                                      "AND connection_id = $2 AND enabled", org_id, connection_id)
        if row is None:
            raise ProviderCallDenied(f"credential {ref!r} is not stored")
        try:
            return secret_box.open_sealed(row["secret_ciphertext"], org_id=org_id, connection_id=connection_id)
        except secret_box.SecretBoxError as exc:
            raise ProviderCallDenied(f"credential {ref!r}: {exc}") from exc


def install(pool_getter) -> None:
    """Register the store and the resolver. Called once at startup, after the pool exists."""
    registry.register_connection_store("org_keys", OrgKeyStore(pool_getter))
    register_secret_resolver(SCHEME, OrgKeyResolver(pool_getter))

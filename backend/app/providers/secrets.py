"""Credential references -> secrets, at call time only.

A connection stores a *reference* ("env:TOGETHER_API_KEY"), never the secret. Schemes are pluggable so
the real store (Google Secret Manager, an encrypted column, a customer KMS) can be added without
touching the adapters: `register_secret_resolver("gsm", MyResolver())`. An unknown scheme fails
closed. Resolved values are returned to the adapter and used for one request; nothing here logs them,
and errors name the reference, never the value.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol

from app.providers.types import ProviderCallDenied


class SecretResolver(Protocol):
    async def resolve(self, ref: str) -> str: ...


class EnvSecretResolver:
    """`env:NAME` -> os.environ[NAME]. Good for a single-operator or CI deployment, not for tenants'
    keys (those need per-tenant storage)."""

    async def resolve(self, ref: str) -> str:
        name = ref.partition(":")[2]
        value = os.environ.get(name) if name else None
        if not value:
            raise ProviderCallDenied(f"credential {ref!r} is not set")
        return value


_RESOLVERS: dict[str, SecretResolver] = {"env": EnvSecretResolver()}


def register_secret_resolver(scheme: str, resolver: SecretResolver) -> None:
    _RESOLVERS[scheme] = resolver


def unregister_secret_resolver(scheme: str) -> None:
    _RESOLVERS.pop(scheme, None)


async def resolve_secret(ref: Optional[str]) -> Optional[str]:
    """None for a connection that needs no credential (a local endpoint)."""
    if not ref:
        return None
    scheme = ref.partition(":")[0]
    resolver = _RESOLVERS.get(scheme)
    if resolver is None:
        raise ProviderCallDenied(f"no secret resolver for scheme {scheme!r} (credential {ref!r})")
    return await resolver.resolve(ref)

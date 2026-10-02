"""One TLS context per process for outbound HTTPS.

Every new httpx client (and so every new OpenAI client) builds an SSL context and loads the CA bundle from disk. On
Windows that took 0.4-2.6 s of blocking CPU each time (measured 2026-10-02: 13.7 s of a 26.7 s find_ways was
`load_verify_locations`, from clients built per call). An `ssl.SSLContext` is safe to share across clients, threads
and event loops, so build it once and hand it to every client via `verify=`.
"""
from __future__ import annotations

import functools
import os
import ssl


@functools.lru_cache(maxsize=1)
def shared_ssl_context() -> ssl.SSLContext:
    """The same trust store httpx would load by default (SSL_CERT_FILE / SSL_CERT_DIR, else certifi), built once."""
    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
    if os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context(capath=os.environ["SSL_CERT_DIR"])
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def async_http_client(**kwargs):
    """An httpx.AsyncClient on the shared TLS context (for OpenAI(http_client=...) and direct use)."""
    import httpx

    kwargs.setdefault("verify", shared_ssl_context())
    return httpx.AsyncClient(**kwargs)


def sync_http_client(**kwargs):
    import httpx

    kwargs.setdefault("verify", shared_ssl_context())
    return httpx.Client(**kwargs)

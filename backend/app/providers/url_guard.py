"""Where a user-supplied endpoint may point.

An admin types a base URL and the server then calls it with a credential attached, so the field is an
SSRF primitive: `http://169.254.169.254/` would hand cloud credentials to whoever typed it. Rules follow
the MCP security best-practices page and RFC 9728 s7.7:

  * https only (http only for loopback, and only when the connection opts in -- local vLLM / dev);
  * every address the name resolves to must be globally routable (no private, loopback, link-local,
    multicast, reserved, or IPv4-mapped forms of those -- `ipaddress` does the parsing; hand-rolled
    checks miss octal/hex/mapped encodings);
  * credentials in the URL, and redirects, are refused (callers also pass follow_redirects=False).

Honest limit: the check resolves the name, then httpx resolves it again to connect, so a hostile DNS
server could answer differently the second time (DNS rebinding). Closing that needs connection
pinning or an egress proxy (Smokescreen); until then, hosted deployments should also restrict egress
at the network layer.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.providers.types import ProviderCallDenied


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not ip.is_global or ip.is_multicast


def validate_url_shape(url: str, *, allow_http_loopback: bool = False) -> tuple[str, str, int]:
    """(scheme, host, port) or ProviderCallDenied. No network access."""
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ProviderCallDenied("endpoint has no host")
    if parts.username or parts.password:
        raise ProviderCallDenied("credentials in the endpoint URL are not allowed")
    scheme = parts.scheme.lower()
    if scheme == "https":
        pass
    elif scheme == "http":
        if not (allow_http_loopback and _is_loopback_name(host)):
            raise ProviderCallDenied("endpoint must be https (http is only allowed for loopback in local mode)")
    else:
        raise ProviderCallDenied(f"endpoint scheme {scheme!r} is not allowed")
    return scheme, host, parts.port or (443 if scheme == "https" else 80)


def _is_loopback_name(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def check_endpoint(url: str, *, allow_http_loopback: bool = False) -> None:
    """Raise ProviderCallDenied unless `url` is safe to call. Run right before each call."""
    scheme, host, port = validate_url_shape(url, allow_http_loopback=allow_http_loopback)
    if scheme == "http":                                   # loopback by construction (checked above)
        return
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ProviderCallDenied(f"endpoint host {host!r} does not resolve ({exc.__class__.__name__})") from exc
        addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not addresses:
        raise ProviderCallDenied(f"endpoint host {host!r} does not resolve")
    for ip in addresses:
        if _blocked(ip):
            raise ProviderCallDenied(f"endpoint host {host!r} resolves to a non-public address")

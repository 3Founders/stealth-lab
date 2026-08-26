#!/usr/bin/env python3
"""
Docker HEALTHCHECK for the MCP server container.

A bare, unauthenticated POST to /mcp is expected to return 401
(StaticTokenVerifier rejecting a missing token) -- that response IS
"healthy": it proves uvicorn is actually serving AND the auth gate is
actually wired, the same signal packaging/README.md's own smoke test
uses (`curl -X POST .../mcp` -> expect 401). Anything else -- connection
refused, a 5xx, no response at all -- means the process isn't really up.

A 2xx with no token would mean the auth gate isn't enforced at all, which
this treats as UNHEALTHY on purpose, not as success -- a passing
healthcheck should never paper over a real security regression.
"""
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8765/mcp"


def main() -> int:
    req = urllib.request.Request(URL, method="POST")
    try:
        urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as exc:
        return 0 if exc.code == 401 else 1
    except Exception:
        return 1
    return 1  # reached only on an unexpected 2xx -- see docstring


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Docker HEALTHCHECK for the MCP server container.

GET /healthz (liveness, app/mcp_server/health.py) on the served port must answer
200 with {"status": "alive"}: the event loop answers. It checks no dependency on
purpose, so a database blip never restarts the container; readiness (/readyz) is
for the host's traffic routing, not for this probe. (The old probe
expected a bare POST /mcp to be refused with 401; since anonymous reads were
added, an unauthenticated request is let through as a read-only caller and a
bare POST is answered 400, so that probe marked every healthy container
unhealthy.) Auth enforcement for writes is covered by the offline tests, not
by a liveness probe.
"""
import json
import os
import sys
import urllib.request

URL = f"http://127.0.0.1:{os.environ.get('PORT', '8765')}/healthz"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=3) as response:
            if response.status != 200:
                return 1
            body = json.loads(response.read() or b"{}")
    except Exception:
        return 1
    return 0 if body.get("status") == "alive" else 1


if __name__ == "__main__":
    sys.exit(main())

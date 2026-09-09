"""Controlled, offline probe: does openai.OpenAI(...).chat.completions.create(
timeout=X) actually bound a call against a server that accepts the TCP
connection but never sends any response (the exact failure shape a
non-responding provider produces)? Uses the SAME client construction as
backend/app/local_agent/runner.py's real code (max_retries=0, base_url
override), just pointed at a local socket instead of GENERAL_COMPUTE.

No real network call, no API key spend -- pure localhost socket.
"""
from __future__ import annotations

import socket
import threading
import time


def _hanging_server(sock: socket.socket, stop: threading.Event) -> None:
    sock.listen(5)
    sock.settimeout(1.0)
    while not stop.is_set():
        try:
            conn, _ = sock.accept()
        except socket.timeout:
            continue
        # Accept the TCP connection, read nothing, send nothing, just hold
        # it open -- exactly "provider stopped responding", not "connection
        # refused" (which would fail fast and prove nothing about a timeout
        # mechanism).
        while not stop.is_set():
            time.sleep(0.2)


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    stop = threading.Event()
    t = threading.Thread(target=_hanging_server, args=(srv, stop), daemon=True)
    t.start()

    import socket as socket_mod
    from openai import OpenAI

    probe_timeout = 5.0  # short on purpose -- this is a mechanism probe, not a real budget

    # Candidate fix 2: the most fundamental Python-level timeout -- a real
    # OS socket timeout via socket.setdefaulttimeout(), which every new
    # socket created after this call inherits automatically, BELOW both
    # httpx's and openai's own retry/timeout-interpretation logic. Neither
    # library's own timeout= knobs (bare float per-call, nor an explicit
    # client-level httpx.Timeout with retries=0) bounded the hang in this
    # probe's first two attempts -- both measured well over their configured
    # budget (the float form: ~2x; the explicit-client form: killed after
    # >150s against a 5s budget, never returned on its own).
    socket_mod.setdefaulttimeout(probe_timeout)

    client = OpenAI(
        max_retries=0,
        api_key="probe-key-not-real",
        base_url=f"http://127.0.0.1:{port}/v1",
    )

    t0 = time.monotonic()
    try:
        client.chat.completions.create(
            model="probe-model",
            messages=[{"role": "user", "content": "hi"}],
            timeout=probe_timeout,
        )
        print(f"UNEXPECTED: call returned normally after {time.monotonic()-t0:.2f}s")
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        print(f"RESULT elapsed={elapsed:.2f}s probe_timeout={probe_timeout} "
              f"exc_type={type(exc).__name__} exc={exc}")
        if elapsed <= probe_timeout + 2.0:
            print("MECHANISM_BOUNDED=true")
        else:
            print("MECHANISM_BOUNDED=false")
    finally:
        stop.set()
        srv.close()


if __name__ == "__main__":
    main()

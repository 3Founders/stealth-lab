"""Final pre-score remediation: deterministic, offline regression test
proving `_bounded_socket_timeout` (agent.py) actually bounds a call against
a non-responding server -- no real network dependency, a local socket that
accepts the TCP connection and then sends nothing.

Reproduces the exact real defect this fix closes: `Agent._complete()`'s
pre-existing `timeout=REQUEST_TIMEOUT` (a bare float passed to openai's
`.create()`) was measured NOT to reliably bound a hung provider call --
elapsed time roughly doubled the configured budget in a controlled probe,
and an explicit client-level httpx.Timeout was worse (still hadn't returned
after 30x its configured budget). Only a real OS socket timeout via
socket.setdefaulttimeout() reliably bounded it.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import Agent, RepoSandbox, _bounded_socket_timeout  # noqa: E402


def _start_hanging_server() -> tuple[socket.socket, int, threading.Event]:
    """A real TCP listener that accepts the connection and sends nothing --
    exactly "provider stopped responding", not "connection refused" (which
    fails fast and proves nothing about a timeout mechanism)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    stop = threading.Event()

    def _serve():
        srv.listen(5)
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            while not stop.is_set():
                time.sleep(0.1)

    threading.Thread(target=_serve, daemon=True).start()
    return srv, port, stop


def test_bounded_socket_timeout_actually_bounds_a_nonresponding_server():
    srv, port, stop = _start_hanging_server()
    try:
        from openai import OpenAI

        client = OpenAI(max_retries=0, api_key="test-key-not-real",
                         base_url=f"http://127.0.0.1:{port}/v1")
        budget = 2.0
        t0 = time.monotonic()
        raised = None
        with _bounded_socket_timeout(budget):
            try:
                client.chat.completions.create(
                    model="test-model", messages=[{"role": "user", "content": "hi"}],
                    timeout=budget,
                )
            except Exception as exc:  # noqa: BLE001
                raised = exc
        elapsed = time.monotonic() - t0

        assert raised is not None, "call against a non-responding server must raise, not hang forever"
        # Generous margin (3x budget, not 1.5x) so this test isn't flaky on a
        # loaded CI box -- the real defect this guards against was ~2x-30x
        # overrun, not a marginal few-hundred-ms slip.
        assert elapsed <= budget * 3, (
            f"call took {elapsed:.2f}s against a {budget}s budget -- "
            f"the bounded-timeout mechanism regressed"
        )
    finally:
        stop.set()
        srv.close()


def test_scoped_timeout_restores_the_previous_default_on_exit():
    """The fix must not leak a lowered global socket default past its own
    scope -- confirmed by checking socket.getdefaulttimeout() before,
    during, and after the context manager, both on the success path and
    the exception path."""
    before = socket.getdefaulttimeout()

    with _bounded_socket_timeout(5.0):
        assert socket.getdefaulttimeout() == 5.0

    assert socket.getdefaulttimeout() == before

    try:
        with _bounded_socket_timeout(5.0):
            assert socket.getdefaulttimeout() == 5.0
            raise RuntimeError("simulated failure inside the scope")
    except RuntimeError:
        pass

    assert socket.getdefaulttimeout() == before, (
        "the previous socket default must be restored even when the "
        "wrapped call raises"
    )


def test_bounded_timeout_does_not_affect_a_call_that_completes_normally():
    """A real, fast-responding server must be entirely unaffected -- the
    fix must not slow down or interfere with normal successful calls."""
    import http.server
    import json as json_mod
    import threading as threading_mod

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json_mod.dumps({
                "id": "test", "object": "chat.completion", "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: D401 -- silence test server logging
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading_mod.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        from openai import OpenAI

        client = OpenAI(max_retries=0, api_key="test-key-not-real",
                         base_url=f"http://127.0.0.1:{port}/v1")
        with _bounded_socket_timeout(5.0):
            resp = client.chat.completions.create(
                model="test-model", messages=[{"role": "user", "content": "hi"}],
                timeout=5.0,
            )
        assert resp.choices[0].message.content == "ok"
    finally:
        srv.shutdown()

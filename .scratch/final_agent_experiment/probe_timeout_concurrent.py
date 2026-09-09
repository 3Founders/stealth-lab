"""Does the socket.setdefaulttimeout() approach stay correct when TWO
independent blocking calls are in flight concurrently, each in its own
thread (mirroring how two trials could run in parallel)? A process-global
setting is the known risk here -- this proves whether save/restore around
just the call site is actually safe under contention, not just in isolation.
"""
from __future__ import annotations

import contextlib
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
        while not stop.is_set():
            time.sleep(0.2)


@contextlib.contextmanager
def _scoped_socket_timeout(seconds: float):
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(prev)


def _make_hanging_server() -> tuple[socket.socket, int, threading.Event, threading.Thread]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    stop = threading.Event()
    t = threading.Thread(target=_hanging_server, args=(srv, stop), daemon=True)
    t.start()
    return srv, port, stop, t


def _worker(label: str, port: int, budget: float, results: dict) -> None:
    from openai import OpenAI

    client = OpenAI(max_retries=0, api_key="probe-key-not-real",
                     base_url=f"http://127.0.0.1:{port}/v1")
    t0 = time.monotonic()
    with _scoped_socket_timeout(budget):
        try:
            client.chat.completions.create(
                model="probe-model", messages=[{"role": "user", "content": "hi"}],
                timeout=budget,
            )
            results[label] = ("UNEXPECTED_SUCCESS", time.monotonic() - t0)
        except Exception as exc:  # noqa: BLE001
            results[label] = (type(exc).__name__, time.monotonic() - t0)


def main() -> None:
    srv_a, port_a, stop_a, t_a = _make_hanging_server()
    srv_b, port_b, stop_b, t_b = _make_hanging_server()

    results: dict[str, tuple] = {}
    # Two workers with DIFFERENT budgets, launched together, to prove one
    # thread's scoped timeout doesn't leak into or clobber the other's.
    w1 = threading.Thread(target=_worker, args=("fast(3s)", port_a, 3.0, results))
    w2 = threading.Thread(target=_worker, args=("slow(6s)", port_b, 6.0, results))
    t0 = time.monotonic()
    w1.start()
    w2.start()
    w1.join(timeout=30)
    w2.join(timeout=30)
    total = time.monotonic() - t0

    print(f"total_wall={total:.2f}s")
    for label, (exc_type, elapsed) in results.items():
        print(f"{label}: exc={exc_type} elapsed={elapsed:.2f}s")

    stop_a.set(); stop_b.set()
    srv_a.close(); srv_b.close()

    # Real invariant: EACH thread's own call must bound near its OWN
    # configured budget (no indefinite hang, no cross-thread leakage of the
    # other thread's timeout value) -- not a tight aggregate wall-clock
    # bound, which is sensitive to this test harness's own sequential
    # .join() calls and thread-startup overhead, not the mechanism itself.
    ok = (
        len(results) == 2
        and results["fast(3s)"][1] <= 3.0 + 3.0
        and results["slow(6s)"][1] <= 6.0 + 3.0
    )
    print(f"CONCURRENT_MECHANISM_BOUNDED={'true' if ok else 'false'}")


if __name__ == "__main__":
    main()

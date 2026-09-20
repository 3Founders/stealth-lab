"""`ingest` (thin orchestrator over enqueue + workers) and `watch` (live operational view).

No queue or worker semantics live here: jobs are enqueued by `app.ingestion.enqueue` (idempotent), drained by
`app.ingestion.worker` wherever it runs, and observed through `admin metrics`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Optional

from . import checks, core, shardops
from .core import BACKEND, FAIL, OpsError, admin, backend, load_env, load_state, run

TERMINAL_ALERTS = ("cost.budget_exceeded", "cost.ledger_unavailable")


def count_manifest(path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def metrics() -> dict[str, Any]:
    p = admin("metrics", timeout=120)
    if not p.ok:
        raise OpsError(f"metrics failed: {(p.err or p.out)[-300:]}")
    return p.json()


def fmt_line(m: dict[str, Any]) -> str:
    j, p, pr, c = m["jobs"], m["providers"], m["projection"], m["cost"]
    sh = " ".join(f"{s['shard_id']}:{'%.1fGB' % (s['size_bytes'] / 1e9) if s.get('reachable') else 'DOWN'}" for s in m["shards"])
    return (f"queued={j['queued']} running={j['running']} done={j['completed']} retry={j['retryable_failed']} "
            f"FAILED={j['permanent_failed']} expired={j['expired_leases']} | {j['throughput_per_min']}/min "
            f"p95={j['p95_duration_s']}s | proj lag={pr['pending']}/{pr['oldest_pending_s']:.0f}s failed={pr['failed']} | "
            f"fallback={p['fallback_rate_60m']:.0%} 429s/15m={p['rate_limited_15m']} | "
            f"${c['spent_24h_usd']}/{c['daily_cap_usd']} ({c['state']}) | {sh}")


# ------------------------------------------------------------------ workers


class Runner:
    """Starts workers for the chosen platform. Workers are the ONE existing worker program; only the launcher differs."""

    def __init__(self, kind: str, workers: int, lanes: int):
        self.kind, self.workers, self.lanes = kind, workers, lanes
        self.procs: list[subprocess.Popen] = []

    def start(self) -> str:
        if self.kind == "none":
            return "no runner configured: start workers yourself (deploy-workers / worker --once)"
        if self.kind == "local":
            self.procs = [subprocess.Popen(
                [sys.executable, "-m", "app.ingestion.worker", "--once", "--concurrency", str(self.lanes)],
                cwd=str(BACKEND), env=load_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(self.workers)]
            return f"started {len(self.procs)} local worker process(es), {self.lanes} lanes each"
        if self.kind == "cloudrun":
            env = load_env()
            p = run(["gcloud", "run", "jobs", "execute", env.get("CLOUD_RUN_JOB", "stealth-ingest-worker"), "--region", env.get("GCP_REGION", ""),
                     "--project", env.get("GCP_PROJECT", ""), "--tasks", str(self.workers)])
            if not p.ok:
                raise OpsError(f"gcloud run jobs execute failed: {p.err.strip()[-300:]}")
            return f"Cloud Run job execution started with {self.workers} tasks"
        if self.kind == "github":
            p = run(["gh", "workflow", "run", "ingest-worker-batch.yml", "-f", f"workers={self.workers}", "-f", f"concurrency={self.lanes}"])
            if not p.ok:
                raise OpsError(f"gh workflow run failed: {p.err.strip()[-300:]}")
            return f"GitHub Actions batch dispatched ({self.workers} runners)"
        raise OpsError(f"unknown runner {self.kind!r}")

    def alive(self) -> bool:
        return any(p.poll() is None for p in self.procs)

    def stop(self) -> str:
        for p in self.procs:
            if p.poll() is None:
                p.terminate()      # SIGTERM: the worker hands its job back (release), spending no attempt
        if self.kind == "cloudrun":
            return "Cloud Run: cancel with `gcloud run jobs executions list` + `gcloud run jobs executions cancel <name>`"
        if self.kind == "github":
            return "GitHub: `gh run cancel <id>` (leases expire; other workers retry)"
        return f"terminated {len(self.procs)} local worker(s)"


# ------------------------------------------------------------------ ingest


def ingest(manifest: str, *, runner: str, workers: int, lanes: int, interval: float, max_wait_min: float,
           max_permanent_failures: int, skip_preflight: bool, skip_snapshot: bool, dry_run: bool) -> int:
    if not os.path.exists(manifest):
        raise OpsError(f"manifest not found: {manifest}")
    n = count_manifest(manifest)
    print(f"manifest {manifest}: {n} package line(s)")
    if not skip_preflight:
        print("== preflight ==")
        if checks.preflight() != 0:
            print("\nrefusing to ingest: preflight has blockers", file=sys.stderr)
            return 1
    if dry_run:
        print("dry run: nothing enqueued")
        return 0
    if not skip_snapshot and "pre-first-ingest" not in load_state().get("snapshots", {}):
        print("== snapshot before the first bulk ingestion ==")
        try:
            for line in shardops.snapshot_prod("pre-first-ingest"):
                print("  " + line)
        except OpsError as exc:
            print(f"cannot snapshot ({exc}); fix NEON_API_KEY or pass --skip-snapshot to accept the risk", file=sys.stderr)
            return 1
    before = metrics()["jobs"]
    p = backend(["app.ingestion.enqueue", "skill-package", "--manifest", manifest], timeout=1800)
    if not p.ok:
        raise OpsError(f"enqueue failed: {(p.err or p.out)[-400:]}")
    res = p.json()
    print(f"enqueue: created={res['created']} already-present={res['duplicate']} (idempotent) | expected new work: {res['created']} job(s); "
          f"queue now: pending={before['queued'] + res['created']}")
    r = Runner(runner, workers, lanes)
    print(r.start())
    deadline = time.monotonic() + max_wait_min * 60
    rc = 0
    try:
        while True:
            time.sleep(interval)
            m = metrics()
            print(time.strftime("%H:%M:%S ") + fmt_line(m), flush=True)
            ap = admin("alerts")     # evaluates + de-duplicates + notifies (webhook) in one call
            firing = ap.json().get("firing", []) if ap.ok else []
            unsafe = [k for k in firing if k in TERMINAL_ALERTS]
            j = m["jobs"]
            new_failures = j["permanent_failed"] - before["permanent_failed"]     # failures since THIS run started
            if unsafe or new_failures >= max_permanent_failures:
                print(f"SAFETY STOP: {unsafe or str(new_failures) + ' permanent failures'}. {r.stop()}", file=sys.stderr)
                rc = 1
                break
            open_work = j["queued"] + j["running"] + j["retryable_failed"]
            if r.kind == "local" and not r.alive() and open_work:
                print("  " + r.start())     # workers exit when the queue is momentarily empty; retryable jobs come due later
            if not open_work:
                break
            if time.monotonic() > deadline:
                print(f"--max-wait reached with {open_work} job(s) still open; they stay queued. {r.stop()}", file=sys.stderr)
                rc = 1
                break
    except KeyboardInterrupt:
        print("\ninterrupted: " + r.stop())
        return 130
    print("== post-batch verification ==")
    return max(rc, checks.post_batch_verify())


def retry_failed(job_types: Optional[str] = None) -> int:
    args = ["retry"] + (["--job-types", job_types] if job_types else [])
    p = admin(*args)
    print(p.out.strip() or p.err.strip())
    return p.rc


# ------------------------------------------------------------------ watch


def watch(*, interval: float, once: bool, notify: bool, as_json: bool) -> int:
    while True:
        try:
            m = metrics()
        except OpsError as exc:
            print(f"{time.strftime('%H:%M:%S')} metrics unavailable: {exc}", flush=True)
            m = None
        if m:
            if as_json:
                print(json.dumps(m, default=str))
            else:
                print(time.strftime("%H:%M:%S ") + fmt_line(m), flush=True)
            if notify:
                ap = admin("alerts")
                a = ap.json() if ap.ok else {}
                for k, msg in (a.get("messages") or {}).items():
                    print(f"   ! {k}: {msg}")
        if once:
            return 0
        time.sleep(interval)

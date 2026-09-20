"""doctor/preflight, smoke test, post-batch verification, promotion gate.

Everything is a composition of existing commands: `worker --validate-config`, `admin verify-*`, `admin metrics`,
`admin probe-providers`, `admin count-source`, `enqueue raw`, `worker --once`, `migrate.py --status`, and the real REST
endpoint POST /v1/search/recommend. The ONLY logic here is ordering, assertions and exit codes.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlparse

from . import core, objstore, shardops
from .core import BACKEND, DEGRADED, FAIL, OK, SKIP, WARN, OpsError, Report, admin, backend, load_env, load_state, run

SMOKE_KEY = "ops-smoke:v1"
SMOKE_GOAL = "stealth-ops smoke test: verify the ingestion pipeline round trip"
SMOKE_PROC = "stealth-ops smoke procedure (safe to ignore)"
SMOKE_PAYLOAD = {
    "source_key": SMOKE_KEY, "source_uri": "https://example.invalid/stealth-ops/smoke", "goal": SMOKE_GOAL,
    "procedure": {"name": SMOKE_PROC, "steps": [{"description": "run the ingestion smoke validator"}]}, "claims": [],
}


# ------------------------------------------------------------------ individual checks (return (status, detail[, data]))


def control_db() -> tuple:
    dsn = load_env().get("CONTROL_DATABASE_URL") or load_env().get("DATABASE_URL")
    if not dsn:
        raise OpsError("CONTROL_DATABASE_URL is not set (stealth-ops provision-control)")
    if "-pooler" in dsn:
        raise OpsError("CONTROL_DATABASE_URL is a POOLED Neon url; use the direct one (advisory locks)")
    t0 = time.perf_counter()
    shardops._probe(dsn, "SELECT 1")
    missing = shardops.check_extensions(dsn)
    if missing:
        raise OpsError(f"extensions missing: {missing}")
    return OK, f"reachable ({(time.perf_counter() - t0) * 1000:.0f} ms), direct url, extensions ok"


def migrations() -> tuple:
    dsn = load_env().get("CONTROL_DATABASE_URL") or load_env().get("DATABASE_URL", "")
    pend = shardops.pending_migrations(dsn)
    if pend:
        raise OpsError(f"{len(pend)} pending migration(s) (first: {pend[0]}): stealth-ops provision-control")
    return OK, "control DB fully migrated"


def shards() -> tuple:
    ok, problems = shardops.verify_shards(log=lambda *_: None)
    reg = shardops.registry()
    if not ok:
        raise OpsError("; ".join(problems))
    return OK, f"{len(reg)} shard(s) verified: " + ", ".join(f"{s['shard_id']}={s['status']}" for s in reg)


def object_storage() -> tuple:
    return OK, objstore.health()


def providers() -> dict[str, Any]:
    p = admin("probe-providers", timeout=180)
    try:
        return p.json()
    except OpsError:
        raise OpsError(f"provider probe crashed: {(p.err or p.out)[-300:]}")


def embeddings(probe: dict) -> tuple:
    e = probe["embedding"]
    if not e["ok"]:
        raise OpsError(e.get("error", "embedding call failed"))
    return OK, f"{e['dim']}-dim vector in {e['ms']} ms"


def judge(probe: dict) -> tuple:
    j = probe["judge"]
    if not j["ok"]:
        raise OpsError(j.get("error") or j.get("reason") or "no judge provider answered")
    if j["fallback"]:
        return DEGRADED, f"served by fallback {j['provider']} (primary judge unavailable)"
    return OK, f"{j['provider']}:{j['model']} in {j['ms']} ms"


def verify(cmd: str) -> tuple:
    p = admin(cmd)
    if not p.ok:
        raise OpsError(f"admin {cmd} exit {p.rc}: {(p.out or p.err)[-500:]}")
    return OK, "consistent"


def budget() -> tuple:
    m = admin("metrics")
    if not m.ok:
        raise OpsError(f"metrics failed: {(m.err or m.out)[-300:]}")
    c = m.json()["cost"]
    if c["state"] in ("exceeded", "unavailable"):
        raise OpsError(f"budget {c['state']}: ${c['spent_24h_usd']} of ${c['daily_cap_usd']}")
    if not load_env().get("DAILY_LLM_BUDGET_USD"):
        return WARN, f"DAILY_LLM_BUDGET_USD not set explicitly: using the default ${c['daily_cap_usd']} (workers ARE enforcing it)"
    return OK, f"${c['spent_24h_usd']} of ${c['daily_cap_usd']} used in 24h; workers stop leasing at 100%, warn at {c['warn_fraction']:.0%}"


def worker_config() -> tuple:
    env = load_env()
    p = backend(["app.ingestion.worker", "--validate-config"], timeout=120)
    if p.rc != 0:
        raise OpsError(core.redact((p.err or p.out).strip()[-500:], env))
    missing = [k for k in ("INGEST_SERVICE_TOKEN", "SERVICE_TOKEN_ISSUER", "SERVICE_TOKEN_AUDIENCE", "SERVICE_TOKEN_KEYS") if not env.get(k)]
    if missing and env.get("STEALTHLAB_ENV", "PRODUCTION").upper() != "TEST":
        raise OpsError(f"workers need a service credential; missing {missing} (stealth-ops mint-worker-token)")
    return OK, "worker config valid; service credential present"


def _reachable(url: str) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)), timeout=5):
            return True
    except OSError:
        return False


def observability() -> tuple:
    env = load_env()
    mandatory = env.get("OPS_OBSERVABILITY_REQUIRED") == "1"
    ep, on = env.get("OTEL_EXPORTER_OTLP_ENDPOINT"), env.get("OBSERVABILITY_ENABLED", "").lower() in ("1", "true", "yes")
    if not (on and ep):
        if mandatory:
            raise OpsError("OBSERVABILITY_ENABLED + OTEL_EXPORTER_OTLP_ENDPOINT required (OPS_OBSERVABILITY_REQUIRED=1)")
        return WARN, "traces not exported (set OBSERVABILITY_ENABLED=true, OBSERVABILITY_BACKEND=otlp, OTEL_EXPORTER_OTLP_ENDPOINT)"
    if not _reachable(ep):
        if mandatory:
            raise OpsError(f"OTLP endpoint {ep} unreachable")
        return WARN, f"OTLP endpoint {urlparse(ep).hostname} unreachable from here (telemetry never blocks ingestion)"
    return OK, f"OTLP -> {urlparse(ep).hostname}, env={env.get('STEALTHLAB_ENV', 'PRODUCTION')}, release={env.get('RELEASE', 'unset')}"


def sentry() -> tuple:
    env = load_env()
    if not env.get("SENTRY_DSN"):
        if env.get("OPS_SENTRY_REQUIRED") == "1":
            raise OpsError("SENTRY_DSN required (OPS_SENTRY_REQUIRED=1)")
        return WARN, "SENTRY_DSN not set: backend errors are not reported"
    return OK, "SENTRY_DSN configured (worker, api and mcp call observability.init)"


# ------------------------------------------------------------------ smoke


def _enqueue_smoke() -> bool:
    p = backend(["app.ingestion.enqueue", "raw", "--job-type", "ingest_candidate_bundle", "--idempotency-key", SMOKE_KEY,
                 "--payload", json.dumps(SMOKE_PAYLOAD), "--scope-type", "global", "--visibility", "public", "--source-id", "ops-smoke"])
    if not p.ok:
        raise OpsError(f"enqueue failed: {(p.err or p.out)[-400:]}")
    return bool(p.json()["created"])


def _run_worker_once() -> None:
    p = backend(["app.ingestion.worker", "--once", "--concurrency", "1", "--job-types", "ingest_candidate_bundle"], timeout=900)
    if p.rc == 3:
        raise OpsError("model budget reached: smoke ingestion would not run")
    if p.rc != 0:
        raise OpsError(f"worker exit {p.rc}: {core.redact((p.err or p.out)[-500:])}")


def _counts() -> dict:
    p = admin("count-source", "--source-key", SMOKE_KEY, "--goal-name", SMOKE_GOAL)
    if not p.ok:
        raise OpsError(f"count-source failed: {(p.err or p.out)[-300:]}")
    return p.json()


def smoke_ingestion() -> tuple:
    created = _enqueue_smoke()
    _run_worker_once()
    first = _counts()
    if first["live_procedures"] != 1 or first["live_goals"] != 1:
        raise OpsError(f"after first ingest expected 1 procedure / 1 goal, got {first}")
    return OK, f"{'ingested' if created else 'fixture already present (re-verified)'}: {first}"


def smoke_idempotency() -> tuple:
    before = _counts()
    created = _enqueue_smoke()
    _run_worker_once()
    after = _counts()
    if created:
        raise OpsError("re-enqueueing the same idempotency key created a NEW job")
    if after != before or after["jobs_with_key"] != 1:
        raise OpsError(f"duplicate canonical rows or jobs after replay: before={before} after={after}")
    return OK, f"replay created no jobs / goals / procedures ({after})"


class _LocalApi:
    """Start the real FastAPI app on a free port for the smoke test when STEALTH_API_URL is not given."""

    def __enter__(self) -> str:
        url = load_env().get("STEALTH_API_URL")
        self.proc = None
        if url:
            return url.rstrip("/")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                                     cwd=str(BACKEND), env=load_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        for _ in range(90):
            try:
                urllib.request.urlopen(base + "/health", timeout=2).read()
                return base
            except Exception:  # noqa: BLE001
                if self.proc.poll() is not None:
                    raise OpsError("local API exited during start-up (run `uvicorn app.main:app` by hand to see why)")
                time.sleep(1)
        raise OpsError("local API did not become healthy in 90s")

    def __exit__(self, *_: Any) -> None:
        if self.proc:
            self.proc.terminate()


def retrieval_smoke() -> tuple:
    """Real production path: POST /v1/search/recommend (projections -> goal resolution -> judge -> hydration)."""
    with _LocalApi() as base:
        req = urllib.request.Request(base + "/v1/search/recommend", data=json.dumps({"goal": SMOKE_GOAL}).encode(),
                                     headers={"content-type": "application/json", **(
                                         {"authorization": "Bearer " + load_env()["STEALTH_API_TOKEN"]} if load_env().get("STEALTH_API_TOKEN") else {})})
        try:
            body = json.loads(urllib.request.urlopen(req, timeout=120).read())
        except urllib.error.HTTPError as e:
            raise OpsError(f"recommend -> HTTP {e.code}: {e.read()[:300]!r}")
    text = json.dumps(body)
    ret = body.get("retrieval") or {}
    if SMOKE_PROC not in text:
        raise OpsError(f"smoke procedure not retrieved (goal_resolution={body.get('goal_resolution')}, reason={body.get('reason')}); "
                       "run `stealth-ops repair projections`")
    mode = ret.get("mode")
    if mode == "candidates_only" or ret.get("degraded") or ret.get("unavailable_shards"):
        return DEGRADED, f"found, but retrieval degraded: mode={mode} reasons={ret.get('degraded_reasons') or ret.get('reasons')} unavailable={ret.get('unavailable_shards')}"
    return OK, f"found via mode={mode}, shards={ret.get('shards_touched') or ret.get('shards')}"


# ------------------------------------------------------------------ composite commands


def preflight(*, skip_smoke: bool = False, json_out: bool = False, title: str = "PREFLIGHT") -> int:
    """Can we safely start real ingestion right now? Ordered so a cheap root cause stops the expensive checks."""
    r = Report(title, json_out=json_out)
    db = r.stage("CONTROL DB", control_db)
    mig = r.stage("MIGRATIONS", migrations) if db.status == OK else r.add("MIGRATIONS", SKIP, "needs control DB")
    ready = db.status == OK and mig.status == OK
    if ready:
        r.stage("SHARDS", shards)
    else:
        r.add("SHARDS", SKIP, "needs migrated control DB")
    r.stage("OBJECT STORAGE", object_storage)
    probe: dict = {}
    try:
        probe = providers() if ready else {}
    except OpsError as exc:
        r.add("EMBEDDINGS", FAIL, str(exc))
        r.add("JEV/JUDGE", FAIL, str(exc))
    else:
        if probe:
            r.stage("EMBEDDINGS", lambda: embeddings(probe))
            r.stage("JEV/JUDGE", lambda: judge(probe))
        else:
            r.add("EMBEDDINGS", SKIP, "needs migrated control DB")
            r.add("JEV/JUDGE", SKIP, "needs migrated control DB")
    for name, cmd in (("PROJECTIONS", "verify-projections"), ("DEDUP", "verify-dedup"), ("REFERENCES", "verify-refs")):
        r.stage(name, (lambda c=cmd: verify(c)) if ready else (lambda: (SKIP, "needs migrated control DB")))
    r.stage("BUDGET", budget if ready else (lambda: (SKIP, "needs migrated control DB")))
    r.stage("WORKER CONFIG", worker_config)
    r.stage("OBSERVABILITY", observability, blocking=False)
    r.stage("SENTRY", sentry, blocking=False)
    if skip_smoke or r.failed:
        r.add("SMOKE INGESTION", SKIP, "skipped (--skip-smoke)" if skip_smoke else "skipped: fix blockers above first")
        r.add("RETRIEVAL", SKIP, "skipped")
    else:
        s = r.stage("SMOKE INGESTION", lambda: _both_smokes())
        if s.status == OK:
            r.stage("RETRIEVAL", retrieval_smoke)
        else:
            r.add("RETRIEVAL", SKIP, "needs smoke ingestion")
    return r.finish()


def _both_smokes() -> tuple:
    a = smoke_ingestion()
    b = smoke_idempotency()
    return OK, f"{a[1]} | {b[1]}"


def smoke_test(*, json_out: bool = False) -> int:
    r = Report("SMOKE TEST", json_out=json_out)
    if r.stage("INGESTION", smoke_ingestion).status == OK:
        r.stage("IDEMPOTENCY", smoke_idempotency)
        r.stage("PROJECTIONS", lambda: (admin("drain-projections") and verify("verify-projections")))
        r.stage("RETRIEVAL", retrieval_smoke)
    return r.finish()


def post_batch_verify(*, reconcile: bool = True, json_out: bool = False) -> int:
    """Everything that should be true after a batch. drain -> verify projections -> dedup -> reconcile -> refs -> re-verify."""
    r = Report("POST-BATCH VERIFY", json_out=json_out)
    r.stage("PROJECTION DRAIN", lambda: (OK, admin("drain-projections").out.strip()[:120] or "drained"))
    r.stage("PROJECTIONS", lambda: verify("verify-projections"))
    if reconcile:
        r.stage("GOAL RECONCILE", lambda: (OK, admin("reconcile-goals", timeout=3600).out.strip()[-160:]), blocking=False)
        r.stage("CLAIM RECONCILE", lambda: (OK, admin("reconcile-claims", timeout=3600).out.strip()[-160:]), blocking=False)
        r.stage("PROJECTION DRAIN 2", lambda: (OK, admin("drain-projections").out.strip()[:120] or "drained"))
    r.stage("DEDUP", lambda: verify("verify-dedup"))
    r.stage("REFERENCES", lambda: verify("verify-refs"))

    def failures() -> tuple:
        m = admin("metrics").json()["jobs"]
        if m["permanent_failed"]:
            raise OpsError(f"{m['permanent_failed']} permanently failed job(s): admin failures; then `stealth-ops retry-failed`")
        if m["retryable_failed"] or m["queued"] or m["running"]:
            return WARN, f"not finished: queued={m['queued']} running={m['running']} retryable_failed={m['retryable_failed']}"
        return OK, f"{m['completed']} completed, 0 failed, 0 expired leases" if not m["expired_leases"] else (WARN, f"{m['expired_leases']} expired leases")
    r.stage("JOBS", failures)
    r.stage("ALERTS", lambda: _alerts_status(), blocking=False)
    return r.finish()


def _alerts_status() -> tuple:
    p = admin("alerts", "--no-notify")
    rep = p.json()
    if rep["critical"]:
        raise OpsError("critical alerts firing: " + "; ".join(rep["messages"][k] for k in rep["critical"]))
    return (WARN, "; ".join(rep["messages"].values())) if rep["firing"] else (OK, "no alerts firing")


def promote(source: str, target: str, *, json_out: bool = False) -> int:
    """Gate, in the spec's order. Runs the write-capable smokes against SOURCE (staging), read-only checks against TARGET.
    Records a passing gate (git sha + time) that deploy-* commands require for production. It deploys nothing itself."""
    if source == target:
        raise OpsError("source and target environments must differ")
    r = Report(f"PROMOTE {source} -> {target}", json_out=json_out)
    prev = os.environ.get("STEALTH_OPS_ENV")
    try:
        for env_name, writes in ((source, True), (target, False)):
            os.environ["STEALTH_OPS_ENV"] = env_name
            tag = f"[{env_name}] "
            r.stage(tag + "config", worker_config)
            r.stage(tag + "migrations", migrations)
            probe: dict = {}
            try:
                probe = providers()
            except OpsError as exc:
                r.add(tag + "provider health", FAIL, str(exc))
            if probe:
                r.stage(tag + "embeddings", lambda: embeddings(probe))
                r.stage(tag + "judge", lambda: judge(probe))
            r.stage(tag + "object storage", object_storage)
            r.stage(tag + "shards", shards)
            for name, cmd in (("projections", "verify-projections"), ("dedup", "verify-dedup"), ("references", "verify-refs")):
                r.stage(tag + name, lambda c=cmd: verify(c))
            if writes and not r.failed:
                r.stage(tag + "smoke ingestion", smoke_ingestion)
                r.stage(tag + "idempotency smoke", smoke_idempotency)
                r.stage(tag + "retrieval smoke", retrieval_smoke)
            r.stage(tag + "observability", observability, blocking=os.environ.get("OPS_OBSERVABILITY_REQUIRED") == "1")
            r.stage(tag + "budget", budget)
    finally:
        if prev is None:
            os.environ.pop("STEALTH_OPS_ENV", None)
        else:
            os.environ["STEALTH_OPS_ENV"] = prev
    code = r.finish()
    if code == 0:
        sha = run(["git", "rev-parse", "HEAD"]).out.strip()
        st = load_state()
        st.setdefault("gates", {})[target] = {"sha": sha, "at": time.time(), "from": source}
        core.save_state(st)
        print(f"gate recorded for {target} at {sha[:9]}: `stealth-ops deploy-workers --env {target}` will now proceed")
    return code


def gate_ok(target: str, *, max_age_hours: float = 24.0) -> Optional[str]:
    """None if the current git sha has a fresh passing gate for `target`, else the reason it does not."""
    g = load_state().get("gates", {}).get(target)
    sha = run(["git", "rev-parse", "HEAD"]).out.strip()
    if not g:
        return f"no promotion gate recorded for {target}: run `stealth-ops promote <source> {target}`"
    if g["sha"] != sha:
        return f"gate was for {g['sha'][:9]}, HEAD is {sha[:9]}: re-run promote"
    if time.time() - g["at"] > max_age_hours * 3600:
        return f"gate older than {max_age_hours:.0f}h: re-run promote"
    return None

"""stealth-ops: one operator entry point over the existing ingestion primitives. See docs/OPERATIONS_AUTOMATION.md.

Exit codes: 0 ok, 1 a check/step failed (blocker), 2 usage/config error, 130 interrupted.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from . import checks, core, dashboards, deploy, ingest, objstore, secrets_ops, shardops
from .core import OpsError, admin, confirm, load_env


def _p(text: str) -> None:
    print(text, flush=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="stealth-ops", description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", default=None, help="environment name (staging|production); selects ~/.stealth-ops/secrets.<env>.env")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, help: str, *aliases: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help, aliases=list(aliases))

    add("bootstrap-prod", "control DB + worker credential + object storage + preflight, in order").add_argument("--yes", action="store_true")
    add("provision-control", "Neon project for the control DB (K000): create-or-find, migrate, verify extensions")
    s = add("provision-shard", "one shard: Neon project -> migrate -> register -> verify")
    s.add_argument("shard_id")
    s.add_argument("--weight", type=int, default=100)
    s = add("provision-shards", "shards K001..KNNN (idempotent: existing shards are verified, never re-created)")
    s.add_argument("--count", type=int, required=True)
    s.add_argument("--weight", type=int, default=100)
    add("verify-shards", "connectivity, schema, migrations, extensions and routing references for every registered shard")
    s = add("snapshot-prod", "Neon branch (copy-on-write) of the control DB and every shard; never restores or deletes")
    s.add_argument("--name", required=True)
    s = add("capacity", "shard size vs OPS_SHARD_WARN_BYTES / OPS_SHARD_ROLLOVER_BYTES; --apply rolls over")
    s.add_argument("--apply", action="store_true")
    s = add("configure-secrets", "validate (default) or distribute secrets to GCP Secret Manager / GitHub / Oracle / local")
    s.add_argument("--target", action="append", choices=["gcp", "github", "oracle", "local"], help="repeatable; default gcp+github+local")
    s.add_argument("--project", default=None)
    s.add_argument("--repo", default=None)
    s.add_argument("--apply", action="store_true", help="push values (asks for confirmation unless --yes)")
    s.add_argument("--yes", action="store_true")
    s = add("setup-object-storage", "R2/S3 bucket: create if missing, keep private, lifecycle, put/get/delete test")
    s.add_argument("--lifecycle-days", type=int, default=7)
    s = add("mint-worker-token", "register the ingest-worker service and mint its credential (workers refuse to start without one)")
    s.add_argument("--ttl-hours", type=int, default=168)
    s = add("configure-observability", "OTLP endpoint + Sentry DSN + release/environment tags; verifies the exporter contract")
    s.add_argument("--otlp-endpoint")
    s.add_argument("--otlp-headers", help='e.g. "Authorization=Basic ..." (or set OTEL_EXPORTER_OTLP_HEADERS)')
    s.add_argument("--sentry-dsn")
    s.add_argument("--environment", help="STEALTHLAB_ENV tag: PRODUCTION | STAGING")
    s = add("deploy-workers", "Cloud Run job / GitHub Actions / Oracle using the existing deploy assets")
    s.add_argument("--target", choices=["cloudrun", "github", "oracle", "local"], default="cloudrun")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--skip-gate", action="store_true")
    s = add("deploy-api", "API/MCP via the existing Railway config")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--skip-gate", action="store_true")
    s = add("preflight", "can we safely start real ingestion right now? (alias: doctor)", "doctor")
    s.add_argument("--skip-smoke", action="store_true")
    s.add_argument("--json", action="store_true")
    s = add("smoke-test", "ingest a fixture, replay it, assert no duplicates, retrieve it via /v1/search/recommend")
    s.add_argument("--json", action="store_true")
    s = add("ingest", "preflight -> snapshot -> enqueue -> run workers -> watch -> post-batch verification")
    s.add_argument("manifest")
    s.add_argument("--runner", choices=["none", "local", "cloudrun", "github"], default="none")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--lanes", type=int, default=4)
    s.add_argument("--interval", type=float, default=30.0)
    s.add_argument("--max-wait-min", type=float, default=720.0)
    s.add_argument("--max-permanent-failures", type=int, default=int(os.environ.get("OPS_INGEST_MAX_PERMANENT_FAILURES", "10")))
    s.add_argument("--skip-preflight", action="store_true")
    s.add_argument("--skip-snapshot", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s = add("watch", "live queue / throughput / failures / leases / projections / shards / providers / spend")
    s.add_argument("--interval", type=float, default=15.0)
    s.add_argument("--once", action="store_true")
    s.add_argument("--notify", action="store_true", help="also evaluate + send alerts each cycle")
    s.add_argument("--json", action="store_true")
    s = add("post-batch-verify", "drain, verify projections/dedup/refs, reconcile goals+claims, job health, alerts")
    s.add_argument("--no-reconcile", action="store_true")
    s.add_argument("--json", action="store_true")
    add("verify-all", "verify-projections + verify-dedup + verify-refs (no reconciliation)")
    s = add("alerts", "evaluate thresholds now (de-duplicated; sends webhook unless --no-notify)")
    s.add_argument("--no-notify", action="store_true")
    s = add("retry", "retry failed-ingestion: requeue failed/retryable jobs")
    s.add_argument("what", choices=["failed-ingestion"])
    s.add_argument("--job-types")
    s = add("repair", "safe repair wrappers: projections | shard <ID>")
    s.add_argument("what", choices=["projections", "shard"])
    s.add_argument("shard_id", nargs="?")
    s.add_argument("--rebuild", action="store_true", help="projections: snapshot, then admin reindex all")
    s.add_argument("--reactivate", action="store_true", help="shard: accept new placements again if healthy")
    s = add("disable-shard", "mark read-only/unhealthy (no new placement, routing intact); optionally provision a replacement")
    s.add_argument("shard_id")
    s.add_argument("--replace", action="store_true")
    s = add("promote", "production gate: source runs the full check + smoke suite, target runs read-only checks; deploys nothing")
    s.add_argument("source")
    s.add_argument("target")
    s.add_argument("--json", action="store_true")
    s = add("dashboards", "write the Grafana dashboard JSON (SQL over existing tables)")
    s.add_argument("--out")
    return ap


def dispatch(a: argparse.Namespace) -> int:
    c = a.cmd
    if c == "bootstrap-prod":
        _p("== 1/5 control database ==")
        shardops.provision("K000", log=_p)
        _p("== 2/5 worker credential ==")
        deploy.mint_worker_token(ttl_seconds=168 * 3600, log=_p)
        _p("== 3/5 object storage ==")
        if load_env().get("OBJECT_STORAGE_URL") and load_env().get("AWS_ACCESS_KEY_ID"):
            _p(objstore.setup(log=_p))
        else:
            _p("skipped: set OBJECT_STORAGE_URL + AWS_* in ~/.stealth-ops/secrets.env, then `stealth-ops setup-object-storage`")
        _p("== 4/5 secrets inventory (local) ==")
        ok, lines = secrets_ops.check(["local"], project=None, repo=None)
        _p("\n".join(l for l in lines if "MISSING" in l or "no-local-value" in l and " req " in l) or "all required names present locally")
        _p("== 5/5 preflight (no smoke: providers/keys may still be missing) ==")
        return checks.preflight(skip_smoke=True)
    if c == "provision-control":
        shardops.provision("K000", log=_p)
        return 0
    if c == "provision-shard":
        shardops.provision(a.shard_id.upper(), weight=a.weight, log=_p)
        return 0
    if c == "provision-shards":
        shardops.provision_shards(a.count, weight=a.weight, log=_p)
        return 0
    if c == "verify-shards":
        ok, problems = shardops.verify_shards(log=_p)
        for pr in problems:
            _p("PROBLEM " + pr)
        return 0 if ok else 1
    if c == "snapshot-prod":
        for line in shardops.snapshot_prod(a.name):
            _p(line)
        return 0
    if c == "capacity":
        env = load_env()
        shardops.capacity(apply=a.apply, warn_bytes=float(env.get("OPS_SHARD_WARN_BYTES") or 0),
                          rollover_bytes=float(env.get("OPS_SHARD_ROLLOVER_BYTES") or 0), log=_p)
        return 0
    if c == "configure-secrets":
        targets = a.target or ["gcp", "github", "local"]
        env = load_env()
        project, repo = a.project or env.get("GCP_PROJECT"), a.repo or env.get("GITHUB_REPOSITORY")
        if "oracle" in targets:
            _p(f"wrote {secrets_ops.oracle_template(secrets_ops.shard_vars())} (names only)")
        real = [t for t in targets if t in ("gcp", "github", "local")]
        if a.apply:
            push = [t for t in real if t in ("gcp", "github")]
            _p("\n".join(secrets_ops.apply(push, project=project, repo=repo, dry_run=True)))
            if not confirm(f"Push the values above to {', '.join(push)}?", assume_yes=a.yes):
                return 1
            _p("\n".join(secrets_ops.apply(push, project=project, repo=repo, dry_run=False)))
        ok, lines = secrets_ops.check(real, project=project, repo=repo)
        _p("\n".join(lines))
        _p("\n" + "\n".join(secrets_ops.commands_for_humans(project)))
        return 0 if ok else 1
    if c == "setup-object-storage":
        _p(objstore.setup(lifecycle_days=a.lifecycle_days, log=_p))
        return 0
    if c == "mint-worker-token":
        deploy.mint_worker_token(ttl_seconds=a.ttl_hours * 3600, log=_p)
        return 0
    if c == "configure-observability":
        deploy.configure_observability(otlp_endpoint=a.otlp_endpoint, otlp_headers=a.otlp_headers, sentry_dsn=a.sentry_dsn,
                                       environment=a.environment, log=_p)
        return 0
    if c == "deploy-workers":
        deploy.deploy_workers(a.target, env_name=a.env or "production", dry_run=a.dry_run, skip_gate=a.skip_gate, log=_p)
        return 0
    if c == "deploy-api":
        deploy.deploy_api(env_name=a.env or "production", dry_run=a.dry_run, skip_gate=a.skip_gate, log=_p)
        return 0
    if c in ("preflight", "doctor"):
        return checks.preflight(skip_smoke=a.skip_smoke, json_out=a.json)
    if c == "smoke-test":
        return checks.smoke_test(json_out=a.json)
    if c == "ingest":
        return ingest.ingest(a.manifest, runner=a.runner, workers=a.workers, lanes=a.lanes, interval=a.interval,
                             max_wait_min=a.max_wait_min, max_permanent_failures=a.max_permanent_failures,
                             skip_preflight=a.skip_preflight, skip_snapshot=a.skip_snapshot, dry_run=a.dry_run)
    if c == "watch":
        return ingest.watch(interval=a.interval, once=a.once, notify=a.notify, as_json=a.json)
    if c == "post-batch-verify":
        return checks.post_batch_verify(reconcile=not a.no_reconcile, json_out=a.json)
    if c == "verify-all":
        rc = 0
        for cmd in ("verify-projections", "verify-dedup", "verify-refs"):
            p = admin(cmd)
            _p(f"{cmd:<20} {'OK' if p.ok else 'FAIL'}")
            if not p.ok:
                _p((p.out or p.err)[-600:])
                rc = 1
        return rc
    if c == "alerts":
        p = admin("alerts", *(["--no-notify"] if a.no_notify else []))
        _p(p.out or p.err)
        return p.rc
    if c == "retry":
        return ingest.retry_failed(a.job_types)
    if c == "repair":
        if a.what == "projections":
            return 0 if shardops.repair_projections(rebuild=a.rebuild, log=_p) else 1
        if not a.shard_id:
            raise OpsError("repair shard needs a shard id")
        return 0 if shardops.repair_shard(a.shard_id.upper(), reactivate=a.reactivate, log=_p) else 1
    if c == "disable-shard":
        shardops.disable_shard(a.shard_id.upper(), replace=a.replace, log=_p)
        return 0
    if c == "promote":
        return checks.promote(a.source, a.target, json_out=a.json)
    if c == "dashboards":
        from pathlib import Path
        _p(f"wrote {dashboards.write(Path(a.out)) if a.out else dashboards.write()}")
        return 0
    return 2


def main(argv: Optional[list[str]] = None) -> None:
    a = build_parser().parse_args(argv)
    if a.env:
        os.environ["STEALTH_OPS_ENV"] = a.env
    try:
        sys.exit(dispatch(a))
    except OpsError as exc:
        print(f"ERROR: {core.redact(str(exc), load_env())}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

"""Deployment wrappers over the EXISTING assets (deploy/ingestion/*, .github/workflows/ingest-worker-batch.yml,
backend/railway.json) plus worker-credential minting and observability wiring.

Production deploys require a fresh passing promotion gate for the current git sha (`stealth-ops promote`); nothing is
deployed through a failed check. Rendered Cloud Run YAML is written to ~/.stealth-ops, never the repo.
"""
from __future__ import annotations

import os
import re
import secrets as pysecrets
import sys
from pathlib import Path
from typing import Optional

from . import checks, core, secrets_ops, shardops
from .core import OPS_HOME, REPO, OpsError, backend, load_env, run, write_secret_env

JOB_TEMPLATE = REPO / "deploy" / "ingestion" / "cloudrun" / "job.yaml"


def git_sha() -> str:
    return run(["git", "rev-parse", "HEAD"]).out.strip() or "dev"


def require_gate(env_name: str, skip: bool) -> None:
    if skip or env_name != "production":
        return
    why = checks.gate_ok(env_name)
    if why:
        raise OpsError(f"promotion gate not satisfied: {why} (or --skip-gate, which you own)")


# ------------------------------------------------------------------ Cloud Run


def render_job(*, image: str, env: dict[str, str], shard_names: list[tuple[str, str]]) -> str:
    text = JOB_TEMPLATE.read_text(encoding="utf-8")
    text = text.split("\n---\n", 1)[0]                                  # the Job document only (not the scheduler notes)
    text = text.replace("REGION-docker.pkg.dev/PROJECT/stealth/ingest-worker:latest", image)
    defaults = {"RELEASE": git_sha(), "OBSERVABILITY_ENABLED": "false", "OBSERVABILITY_BACKEND": "otlp", "DAILY_LLM_BUDGET_USD": "10",
                "SERVICE_TOKEN_ISSUER": "stealthlab", "SERVICE_TOKEN_AUDIENCE": "stealthlab-services",
                "SERVICE_TOKEN_MAX_TTL_SECONDS": "604800", "OBJECT_STORAGE_URL": "", "OBJECT_STORAGE_ENDPOINT_URL": ""}
    for k, dv in defaults.items():
        text = text.replace(f"@{k}@", env.get(k, dv) if k != "RELEASE" else dv)
    lines = [f"                - {{ name: {var}, valueFrom: {{ secretKeyRef: {{ name: {secret}, key: latest }} }} }}" for var, secret in shard_names]
    text = text.replace("                # @SHARD_ENV@", "\n".join(lines) if lines else "                # (no remote shards registered)")
    left = re.findall(r"@[A-Z_]+@", text)
    if left:
        raise OpsError(f"unrendered template tokens: {left}")
    return text


def deploy_cloudrun(*, dry_run: bool, log=print) -> None:
    env = load_env()
    project, region = env.get("GCP_PROJECT"), env.get("GCP_REGION")
    if not (project and region):
        raise OpsError("set GCP_PROJECT and GCP_REGION (e.g. in ~/.stealth-ops/secrets.env)")
    repo_name = env.get("GCP_ARTIFACT_REPO", "stealth")
    sha = git_sha()
    image = f"{region}-docker.pkg.dev/{project}/{repo_name}/ingest-worker:{sha[:12]}"
    shard_names = [(s["dsn_env"], f"stealth-{s['shard_id'].lower()}-db-url") for s in shardops.registry() if s.get("dsn_env")]
    rendered = render_job(image=image, env=env, shard_names=shard_names)
    out = OPS_HOME / "rendered-job.yaml"
    OPS_HOME.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    cmds = [
        ["gcloud", "builds", "submit", "--project", project, "--config", "deploy/ingestion/cloudbuild.yaml",
         f"--substitutions=_IMAGE={image},_RELEASE={sha[:12]}", "."],
        ["gcloud", "run", "jobs", "replace", str(out), "--region", region, "--project", project],
    ]
    for c in cmds:
        log(("DRY " if dry_run else "RUN ") + " ".join(c))
        if not dry_run:
            p = run(c, cwd=REPO, timeout=1800)
            if not p.ok:
                raise OpsError(f"{c[1]} {c[2]} failed: {(p.err or p.out).strip()[-500:]}")
    log(f"rendered job: {out}")


def deploy_workers(target: str, *, env_name: str, dry_run: bool, skip_gate: bool, log=print) -> None:
    require_gate(env_name, skip_gate)
    if target == "cloudrun":
        deploy_cloudrun(dry_run=dry_run, log=log)
    elif target == "github":
        p = run(["gh", "workflow", "list"])
        if "Ingestion worker batch" not in p.out:
            raise OpsError("workflow 'Ingestion worker batch' not found on the default branch: push .github/workflows/ingest-worker-batch.yml first")
        log("GitHub Actions workers are compute-on-demand: nothing to deploy. Secrets: `stealth-ops configure-secrets --target github`.\n"
            "Start a batch with `stealth-ops ingest <manifest> --runner github`.")
    elif target == "oracle":
        tpl = secrets_ops.oracle_template(secrets_ops.shard_vars())
        host = load_env().get("ORACLE_HOST")
        log(f"wrote {tpl.relative_to(REPO)} (names only). On the VM: fill /etc/stealth/ingest.env (chmod 600), pull the image, "
            "install the systemd unit from deploy/ingestion/oracle/run-worker.sh.")
        if host and not dry_run:
            p = run(["ssh", host, "sudo systemctl restart stealth-ingest"])
            if not p.ok:
                raise OpsError(f"ssh restart failed: {p.err.strip()[-300:]}")
            log(f"restarted stealth-ingest on {host}")
    elif target == "local":
        log("local workers: `stealth-ops ingest <manifest> --runner local` (or deploy/ingestion/local/run-worker.ps1 -Mode --loop)")
    else:
        raise OpsError(f"unknown worker target {target!r}")


def deploy_api(*, env_name: str, dry_run: bool, skip_gate: bool, log=print) -> None:
    """Railway is the existing API deploy path (backend/railway.json, Procfile)."""
    require_gate(env_name, skip_gate)
    cmd = ["railway", "up", "--service", load_env().get("RAILWAY_SERVICE", "backend"), "--detach"]
    log(("DRY " if dry_run else "RUN ") + " ".join(cmd))
    if not dry_run:
        p = run(cmd, cwd=REPO / "backend", timeout=1800)
        if not p.ok:
            raise OpsError(f"railway up failed ({(p.err or p.out).strip()[-300:]}). Alternative: the MCP image backend/Dockerfile on any container host.")


# ------------------------------------------------------------------ worker credential


def mint_worker_token(*, ttl_seconds: int, service_id: str = "ingest-worker", log=print) -> None:
    """Workers REFUSE to start outside TEST without a registered, revocable service credential (auth hardening).
    Generates signing keys if none exist, registers the service via the existing CLI, mints a token, stores it privately."""
    env = load_env()
    if not env.get("SERVICE_TOKEN_KEYS"):
        write_secret_env("SERVICE_TOKEN_KEYS", f"k1:{pysecrets.token_urlsafe(48)}")
        log("generated SERVICE_TOKEN_KEYS (private secrets file)")
    write_secret_env("SERVICE_TOKEN_ISSUER", env.get("SERVICE_TOKEN_ISSUER") or "stealthlab")
    write_secret_env("SERVICE_TOKEN_AUDIENCE", env.get("SERVICE_TOKEN_AUDIENCE") or "stealthlab-services")
    write_secret_env("SERVICE_TOKEN_MAX_TTL_SECONDS", str(max(ttl_seconds, int(env.get("SERVICE_TOKEN_MAX_TTL_SECONDS") or 0))))
    p = backend(["app.services.service_identity", "register", service_id, "--scope", "ingestion:process"])
    if not p.ok:
        raise OpsError(f"register failed: {(p.err or p.out)[-400:]}")
    m = backend(["app.services.service_identity", "mint", service_id, "--scope", "ingestion:process", "--ttl", str(ttl_seconds)])
    token = m.out.strip().splitlines()[-1] if m.ok and m.out.strip() else ""
    if not token:
        raise OpsError(f"mint failed: {(m.err or m.out)[-400:]}")
    write_secret_env("INGEST_SERVICE_TOKEN", token)
    log(f"registered {service_id} (scope ingestion:process); token valid {ttl_seconds // 3600}h stored privately. "
        "Distribute with `stealth-ops configure-secrets --apply`. Re-run this command before it expires; revoke with the service_identity CLI.")


# ------------------------------------------------------------------ observability


def configure_observability(*, otlp_endpoint: Optional[str], otlp_headers: Optional[str], sentry_dsn: Optional[str],
                            environment: Optional[str], log=print) -> None:
    """Writes the settings the EXISTING exporter contract reads (app/config.py + app/telemetry.py). Headers are read by the
    OTLP exporter from OTEL_EXPORTER_OTLP_HEADERS. Then proves a span can be created and flushed without raising."""
    if otlp_endpoint:
        write_secret_env("OBSERVABILITY_ENABLED", "true")
        write_secret_env("OBSERVABILITY_BACKEND", "otlp")
        write_secret_env("OTEL_EXPORTER_OTLP_ENDPOINT", otlp_endpoint)
        write_secret_env("OTEL_SERVICE_NAME", "stealthlab")
    if otlp_headers:
        write_secret_env("OTEL_EXPORTER_OTLP_HEADERS", otlp_headers)
    if sentry_dsn:
        write_secret_env("SENTRY_DSN", sentry_dsn)
    if environment:
        write_secret_env("STEALTHLAB_ENV", environment.upper())
    write_secret_env("RELEASE", git_sha()[:12])
    log("observability settings written (private file). Verifying the exporter contract...")
    code = ("from app import telemetry\n"
            "ok = telemetry.configure('ops-check', batch=False)\n"
            "with telemetry.span('stealth.ops_check'):\n    pass\n"
            "telemetry.shutdown()\nprint('exporter-ok' if ok else 'otel-not-installed')\n")
    p = run([sys.executable, "-c", code], cwd=REPO / "backend")
    log(("exporter contract OK (span created + flushed; delivery errors never propagate)" if "exporter-ok" in p.out
         else "could not exercise the exporter: " + (p.err.strip()[-200:] or p.out.strip())))
    log(checks.observability()[1])
    log(checks.sentry()[1])

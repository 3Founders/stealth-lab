"""Secret / configuration distribution and validation.

One manifest (below) says which names each deployment target needs. `configure-secrets --check` verifies presence in
every target WITHOUT reading values back; `--apply` pushes values from the local private sources (process env,
backend/.env, ~/.stealth-ops/secrets.env) to Google Secret Manager and GitHub Actions. Values travel over stdin /
environment, never argv, and are never printed. Anything a provider cannot safely do automatically is printed as the
exact command and left for a human.

Targets: gcp (Secret Manager -> Cloud Run job), github (Actions secrets), oracle (env-file template + optional scp),
local (backend/.env keys).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import core
from .core import BACKEND, REPO, OpsError, load_env, run


@dataclass(frozen=True)
class Var:
    env: str                 # environment variable the worker/API reads
    gcp: str                 # Secret Manager secret id
    required: bool = True    # blocks ingestion when absent
    secret: bool = True      # False = plain config (still validated, may go to Cloud Run env instead of Secret Manager)
    scope: str = "worker"    # worker | api | both
    note: str = ""


MANIFEST: list[Var] = [
    Var("CONTROL_DATABASE_URL", "stealth-control-db-url", note="Neon DIRECT url of the control DB (provision-control)"),
    Var("GEMINI_API_KEY", "stealth-gemini-key", note="embeddings + judge fallback"),
    Var("GEMINI_API_KEYS", "stealth-gemini-keys", required=False, note="extra keys spread rate limits"),
    Var("VOYAGE_API_KEY", "stealth-voyage-key", required=False, note="fallback embedder"),
    Var("JEV_BASE_URL", "stealth-jev-url", required=False, note="preferred judge (optional)"),
    Var("JEV_API_KEY", "stealth-jev-key", required=False),
    Var("OBJECT_STORAGE_URL", "stealth-object-storage-url", secret=False, note="s3://bucket/prefix"),
    Var("OBJECT_STORAGE_ENDPOINT_URL", "stealth-object-storage-endpoint", secret=False, required=False, note="R2/MinIO endpoint"),
    Var("AWS_ACCESS_KEY_ID", "stealth-object-storage-key-id"),
    Var("AWS_SECRET_ACCESS_KEY", "stealth-object-storage-secret"),
    Var("INGEST_SERVICE_TOKEN", "stealth-ingest-service-token", note="stealth-ops mint-worker-token; workers REFUSE to start without it"),
    Var("SERVICE_TOKEN_ISSUER", "stealth-service-token-issuer", secret=False),
    Var("SERVICE_TOKEN_AUDIENCE", "stealth-service-token-audience", secret=False),
    Var("SERVICE_TOKEN_KEYS", "stealth-service-token-keys", scope="both", note="kid:secret signing keys"),
    Var("SERVICE_TOKEN_MAX_TTL_SECONDS", "stealth-service-token-max-ttl", secret=False, required=False),
    Var("DAILY_LLM_BUDGET_USD", "stealth-daily-llm-budget", secret=False, scope="both", note="one cap for API and workers"),
    # observability (optional: never blocks ingestion)
    Var("OBSERVABILITY_ENABLED", "stealth-obs-enabled", secret=False, required=False, scope="both"),
    Var("OBSERVABILITY_BACKEND", "stealth-obs-backend", secret=False, required=False, scope="both"),
    Var("OTEL_EXPORTER_OTLP_ENDPOINT", "stealth-otlp-endpoint", secret=False, required=False, scope="both"),
    Var("OTEL_EXPORTER_OTLP_HEADERS", "stealth-otlp-headers", required=False, scope="both", note="auth header for the OTLP host"),
    Var("SENTRY_DSN", "stealth-sentry-dsn", required=False, scope="both"),
    Var("OPS_ALERT_WEBHOOK_URL", "stealth-alert-webhook", required=False, scope="both", note="Slack/Discord webhook for ops alerts"),
]


def shard_vars() -> list[Var]:
    """One DSN per registered shard (dynamic: read from the registry, not hardcoded)."""
    from .shardops import registry

    try:
        return [Var(s["dsn_env"], f"stealth-{s['shard_id'].lower()}-db-url", note=f"shard {s['shard_id']}")
                for s in registry() if s.get("dsn_env")]
    except OpsError:
        return []


def _have(env: dict[str, str], v: Var) -> bool:
    return bool(env.get(v.env))


# ------------------------------------------------------------------ inventories (names only)


def gcp_names(project: str) -> set[str]:
    p = run(["gcloud", "secrets", "list", "--project", project, "--format=json"])
    if not p.ok:
        raise OpsError(f"gcloud secrets list failed: {p.err.strip()[-300:]}")
    return {s["name"].rsplit("/", 1)[-1] for s in json.loads(p.out or "[]")}


def github_names(repo: Optional[str]) -> set[str]:
    cmd = ["gh", "secret", "list", "--json", "name"] + (["--repo", repo] if repo else [])
    p = run(cmd)
    if not p.ok:
        raise OpsError(f"gh secret list failed: {p.err.strip()[-300:]}")
    return {s["name"] for s in json.loads(p.out or "[]")}


def local_names() -> set[str]:
    return set(core.parse_env_file(BACKEND / ".env")) | set(core.parse_env_file(core.secrets_path()))


def check(targets: list[str], *, project: Optional[str], repo: Optional[str]) -> tuple[bool, list[str]]:
    """(ok, lines). ok=False iff a REQUIRED name is missing from a requested target."""
    env = load_env()
    shard_vars_ = shard_vars()
    vars_ = MANIFEST + shard_vars_
    lines: list[str] = []
    ok = True
    inventories: dict[str, set[str]] = {}
    for t in targets:
        try:
            if t == "gcp":
                if not project:
                    raise OpsError("set GCP_PROJECT (or --project)")
                inventories[t] = gcp_names(project)
            elif t == "github":
                inventories[t] = github_names(repo)
            elif t == "local":
                inventories[t] = local_names() | {k for k in env if k in {v.env for v in vars_}}
        except OpsError as exc:
            lines.append(f"[{t}] cannot inspect: {exc}")
            ok = False
    for v in vars_:
        row = []
        for t, names in inventories.items():
            key = {"gcp": v.gcp, "github": v.env, "local": v.env}[t]
            present = key in names
            row.append(f"{t}:{'ok' if present else 'MISSING'}")
            if not present and v.required and not (t == "local" and _have(env, v)):
                ok = False
        src = "have-locally" if _have(env, v) else "no-local-value"
        lines.append(f"{v.env:<32} {'req' if v.required else 'opt'} {src:<15} " + " ".join(row))
    if "github" in inventories and shard_vars_ and "SHARD_ENV" not in inventories["github"]:
        lines.append("SHARD_ENV                        req  (github)        MISSING  (workers on Actions cannot reach the shards)")
        ok = False
    return ok, lines


# ------------------------------------------------------------------ apply


def push_gcp(v: Var, value: str, project: str) -> str:
    exists = v.gcp in gcp_names(project)
    cmd = (["gcloud", "secrets", "versions", "add", v.gcp] if exists else
           ["gcloud", "secrets", "create", v.gcp, "--replication-policy=automatic"]) + ["--data-file=-", "--project", project]
    p = run(cmd, input=value)
    if not p.ok:
        raise OpsError(f"{v.gcp}: {p.err.strip()[-300:]}")
    return f"gcp {v.gcp}: {'new version' if exists else 'created'}"


def push_github(v: Var, value: str, repo: Optional[str]) -> str:
    p = run(["gh", "secret", "set", v.env] + (["--repo", repo] if repo else []), input=value)
    if not p.ok:
        raise OpsError(f"github {v.env}: {p.err.strip()[-300:]}")
    return f"github {v.env}: set"


def shard_env_blob(env: dict[str, str], shards: list[Var]) -> str:
    """K00N_DATABASE_URL=... lines, pushed as ONE GitHub secret (Actions cannot enumerate secrets dynamically);
    the workflow re-exports them, masked, before running the worker."""
    return "\n".join(f"{v.env}={env[v.env]}" for v in shards if env.get(v.env))


def apply(targets: list[str], *, project: Optional[str], repo: Optional[str], dry_run: bool) -> list[str]:
    env = load_env()
    out: list[str] = []
    shards = shard_vars()
    if "github" in targets and (blob := shard_env_blob(env, shards)):
        out.append("DRY gh secret set SHARD_ENV" if dry_run else push_github(Var("SHARD_ENV", "-"), blob, repo))
    for v in MANIFEST + shards:
        val = env.get(v.env)
        if not val:
            out.append(f"skip {v.env}: no local value" + (" (REQUIRED)" if v.required else ""))
            continue
        for t in targets:
            if t == "gcp":
                out.append(f"DRY gcloud secrets ... {v.gcp}" if dry_run else push_gcp(v, val, project or ""))
            elif t == "github":
                out.append(f"DRY gh secret set {v.env}" if dry_run else push_github(v, val, repo))
    return out


def oracle_template(shards: list[Var]) -> Path:
    """Names-only env template for /etc/stealth/ingest.env (no values). scp/edit it on the VM yourself."""
    path = REPO / "deploy" / "ingestion" / "oracle" / "ingest.env.template"
    lines = ["# generated by `stealth-ops configure-secrets --target oracle`; fill on the VM (chmod 600), never commit values",
             "STEALTHLAB_ENV=PRODUCTION", "INGEST_WORKER_CONCURRENCY=4"]
    for v in MANIFEST + shards:
        if v.scope in ("worker", "both"):
            lines.append(f"{v.env}=" + ("   # optional" if not v.required else ""))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def commands_for_humans(project: Optional[str]) -> list[str]:
    """Things no API should do silently."""
    return [
        "# Cloud Run job service account needs Secret Manager access (one-time, needs your IAM approval):",
        f"gcloud projects add-iam-policy-binding {project or '<PROJECT>'} --member=serviceAccount:<JOB_SA> --role=roles/secretmanager.secretAccessor",
        "# Hosted OTLP + Sentry accounts are created in their dashboards; paste the endpoint/headers/DSN into ~/.stealth-ops/secrets.env",
        "# (see docs/OPERATIONS_AUTOMATION.md, 'Observability'), then re-run `stealth-ops configure-secrets --apply`.",
    ]


def env_names_in(text: str) -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]+)=", text, flags=re.M))

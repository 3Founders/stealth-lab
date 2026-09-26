#!/usr/bin/env python3
"""Create knowledge-shard databases as Neon projects, migrate them, register them.

    export NEON_API_KEY=...            # Neon console -> Account settings -> API keys
    export DATABASE_URL=...            # the CONTROL database (K000), for registration

    python scripts/provision_neon_shards.py --count 100 --dry-run      # show the plan, change nothing
    python scripts/provision_neon_shards.py --count 100                # create + migrate + register

What it does, per shard K001..K<count> (all steps idempotent -- safe to re-run
after a failure, it resumes):

  1. create   a Neon project named "<prefix>-k001" (skipped if it already exists)
  2. record   its connection string as K001_DATABASE_URL=... in --env-file
              (default backend/.neon_shards.env, git-ignored; NEVER commit it)
  3. migrate  `scripts/migrate.py --dsn <shard>` (the normal control migrations;
              skipped with --skip-migrate)
  4. register the shard in the control database's knowledge_shards with
              dsn_env=K001_DATABASE_URL and --capacity-bytes (skipped with
              --skip-register). The registry stores the env var NAME, never the DSN.

After it finishes, every process that reads shards (API, MCP server, workers)
needs the K0xx_DATABASE_URL variables from the env file in its environment.
New public Goals start landing on the new shards immediately (weight 100); use
`--weight 0` to register them without traffic, then raise weights when ready.

Check your Neon plan's project limit before creating 100 projects.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
NEON_API = "https://console.neon.tech/api/v2"
DEFAULT_CAPACITY_BYTES = 500 * 1024 * 1024          # a 500 MB project


def shard_id(index: int) -> str:
    return f"K{index:03d}"


def project_name(prefix: str, index: int) -> str:
    return f"{prefix}-{shard_id(index).lower()}"


def dsn_env(index: int) -> str:
    return f"{shard_id(index)}_DATABASE_URL"


# ------------------------------------------------------------------ Neon API
class Neon:
    """Minimal Neon API v2 client with retry on rate limits / transient errors."""

    def __init__(self, api_key: str, *, org_id: Optional[str] = None, timeout: float = 60.0,
                 transport: Optional[httpx.BaseTransport] = None):
        self.org_id = org_id
        self.http = httpx.Client(base_url=NEON_API, timeout=timeout, transport=transport, headers={
            "Authorization": f"Bearer {api_key}", "Accept": "application/json", "Content-Type": "application/json"})

    def _call(self, method: str, path: str, **kwargs: Any) -> dict:
        for attempt in range(8):
            resp = self.http.request(method, path, **kwargs)
            if resp.status_code in (423, 429, 500, 502, 503, 504):   # locked / rate limited / transient
                time.sleep(min(60.0, 2.0 * 2 ** attempt))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Neon {method} {path} -> {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content else {}
        raise RuntimeError(f"Neon {method} {path}: still failing after retries")

    def projects_by_name(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {"limit": 400}
            if cursor:
                params["cursor"] = cursor
            if self.org_id:
                params["org_id"] = self.org_id
            page = self._call("GET", "/projects", params=params)
            for project in page.get("projects", []):
                out[project["name"]] = project
            cursor = (page.get("pagination") or {}).get("cursor")
            if not cursor or not page.get("projects"):
                return out

    def create_project(self, name: str, *, region: str, pg_version: int) -> tuple[dict, Optional[str]]:
        body: dict[str, Any] = {"project": {"name": name, "region_id": region, "pg_version": pg_version}}
        if self.org_id:
            body["project"]["org_id"] = self.org_id
        created = self._call("POST", "/projects", json=body)
        uris = created.get("connection_uris") or []
        return created["project"], (uris[0]["connection_uri"] if uris else None)

    def connection_uri(self, project: dict, *, database: str, role: str) -> str:
        # direct (non-pooled) endpoint: migrations need it, and the app's shard pools
        # use prepared statements
        result = self._call("GET", f"/projects/{project['id']}/connection_uri",
                            params={"database_name": database, "role_name": role, "pooled": "false"})
        return result["uri"]


# ------------------------------------------------------------------ env file
_LINE = re.compile(r"^([A-Z0-9_]+)=(.*)$")


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line.strip())
        if match:
            out[match.group(1)] = match.group(2)
    return out


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = ["# Knowledge-shard connection strings (scripts/provision_neon_shards.py).",
             "# SECRET: never commit. Load these into every API / MCP / worker process."]
    lines += [f"{key}={values[key]}" for key in sorted(values)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ------------------------------------------------------------------ steps
def migrate(dsn: str) -> None:
    result = subprocess.run([sys.executable, str(BACKEND_ROOT / "scripts" / "migrate.py"), "--dsn", dsn],
                            capture_output=True, text=True, cwd=BACKEND_ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"migrate failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")


async def register(control_dsn: str, index: int, dsn: str, *, weight: int, capacity_bytes: int) -> None:
    sys.path.insert(0, str(BACKEND_ROOT))
    import asyncpg

    from app.services import shards as sh

    problems = await sh.check_shard_schema(dsn)
    if problems:
        raise RuntimeError(f"{shard_id(index)} is not a usable shard: {'; '.join(problems)}")
    pool = await asyncpg.create_pool(control_dsn, min_size=1, max_size=2)
    try:
        await sh.register_shard(pool, shard_id(index), dsn_env=dsn_env(index), weight=weight,
                                capacity_bytes=capacity_bytes, notes="neon project (provision_neon_shards.py)")
    finally:
        await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, required=True, help="number of shards (K001..K<count>)")
    ap.add_argument("--start", type=int, default=1, help="first shard number (default 1 -> K001)")
    ap.add_argument("--prefix", default="stealthlab", help="Neon project name prefix (default: stealthlab)")
    ap.add_argument("--region", default="aws-us-east-2", help="Neon region id -- use the SAME region as the app")
    ap.add_argument("--pg-version", type=int, default=16)
    ap.add_argument("--org-id", default=os.environ.get("NEON_ORG_ID"), help="Neon organization id (org accounts)")
    ap.add_argument("--database", default="neondb")
    ap.add_argument("--role", default="neondb_owner")
    ap.add_argument("--env-file", default=str(BACKEND_ROOT / ".neon_shards.env"))
    ap.add_argument("--capacity-bytes", type=int, default=DEFAULT_CAPACITY_BYTES)
    ap.add_argument("--weight", type=int, default=100, help="placement weight on registration (0 = no traffic yet)")
    ap.add_argument("--skip-migrate", action="store_true")
    ap.add_argument("--skip-register", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="show the plan; create/change nothing")
    args = ap.parse_args(argv)

    if args.count < 1 or args.start < 1 or args.start + args.count - 1 > 999:
        print("ERROR: shard numbers must stay within K001..K999", file=sys.stderr)
        return 2
    api_key = os.environ.get("NEON_API_KEY")
    if not api_key:
        print("ERROR: set NEON_API_KEY (Neon console -> Account settings -> API keys)", file=sys.stderr)
        return 2
    control_dsn = os.environ.get("DATABASE_URL")
    if not args.skip_register and not control_dsn and not args.dry_run:
        print("ERROR: set DATABASE_URL to the CONTROL database (needed to register shards), "
              "or pass --skip-register", file=sys.stderr)
        return 2

    neon = Neon(api_key, org_id=args.org_id)
    env_path = Path(args.env_file)
    env_values = read_env_file(env_path)
    existing = neon.projects_by_name()
    indexes = range(args.start, args.start + args.count)

    print(f"{len(indexes)} shards {shard_id(indexes[0])}..{shard_id(indexes[-1])} "
          f"in {args.region}, pg{args.pg_version}; env file {env_path}")
    for index in indexes:
        name = project_name(args.prefix, index)
        state = "exists" if name in existing else "create"
        print(f"  {shard_id(index)}  {name:<28} {state:<7} {'dsn known' if dsn_env(index) in env_values else ''}")
    if args.dry_run:
        print("dry run: nothing created")
        return 0

    failures: list[str] = []
    for index in indexes:
        sid, name = shard_id(index), project_name(args.prefix, index)
        try:
            dsn = env_values.get(dsn_env(index))
            if dsn is None:
                if name in existing:
                    dsn = neon.connection_uri(existing[name], database=args.database, role=args.role)
                else:
                    project, uri = neon.create_project(name, region=args.region, pg_version=args.pg_version)
                    existing[name] = project
                    dsn = uri or neon.connection_uri(project, database=args.database, role=args.role)
                    print(f"{sid}: created {name}")
                env_values[dsn_env(index)] = dsn
                write_env_file(env_path, env_values)          # saved immediately: a crash never loses a DSN
            if not args.skip_migrate:
                migrate(dsn)
                print(f"{sid}: migrated")
            if not args.skip_register:
                os.environ[dsn_env(index)] = dsn
                asyncio.run(register(control_dsn, index, dsn, weight=args.weight,
                                     capacity_bytes=args.capacity_bytes))
                print(f"{sid}: registered (weight {args.weight}, capacity {args.capacity_bytes} bytes)")
        except Exception as exc:  # noqa: BLE001 -- keep going; re-run to resume
            failures.append(f"{sid}: {exc}")
            print(f"{sid}: FAILED -- {exc}", file=sys.stderr)

    print(f"done: {len(indexes) - len(failures)} ok, {len(failures)} failed. "
          f"Connection strings: {env_path} (load into every API / MCP / worker process).")
    if failures:
        print("re-run the same command to resume the failed shards.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

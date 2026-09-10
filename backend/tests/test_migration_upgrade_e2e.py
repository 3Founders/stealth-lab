"""
FINAL-V1 §4 -- migration UPGRADE-path proof, against a real, disposable Postgres.

A clean 01->37 run on an empty database is already proven elsewhere. That is
NOT what this file does. This simulates the real thing that happens to an
operator: a populated pre-hardening V1 database (migrations 01..34) that then
takes the hardening migrations 35 (product model), 36 (durable execution
runs) and 37 (a CHECK relax) on top of live data.

Flow:
  1. Provision a disposable Postgres (own cluster via initdb / a
     pgvector docker container -- whichever this host offers). The DB is
     created AND torn down by this test; nothing is left running.
  2. Apply migrations 01..34 ONLY -- the pre-hardening baseline. Assert the
     ledger holds exactly those rows.
  3. Populate a realistic pre-hardening dataset through the REAL service
     write paths where practical (capture_procedure, capture_claim,
     implementation_registry.register) plus direct INSERTs matching the
     real shapes for the plan/graph/executions/evidence lineage. Record
     ids + column values.
  4. Apply migrations 35, 36, 37 via the REAL runner (scripts/migrate.py).
  5. Assert, on the upgraded DB:
       - no checksum drift; every migration applied; ledger count == the
         full on-disk migration count;
       - every pre-existing row is still readable and byte-for-byte
         unchanged (re-SELECT by id, compare recorded values);
       - scope/visibility semantics still hold -- a visibility='private'
         procedure is still filtered out for an anonymous AccessScope by
         the real scope_predicates() SQL;
       - the NEW product-model tables work ON THE UPGRADED DB and can
         reference the pre-existing rows: Problem -> Benchmark -> Solution
         bound to the pre-existing procedure -> request + complete an
         Evaluation whose lineage is the pre-existing executions ->
         problem_leaderboard returns;
       - the NEW durable-run tables work: start_run + execute_run a 2-node
         graph against the pre-existing plan -> 'succeeded';
       - migrations 35/36/37 contain no DROP TABLE / DROP COLUMN / TRUNCATE
         (asserted by grepping the files, mirroring the acceptance note).
  6. Tear the disposable DB down.

Skips cleanly (never fails) when no disposable Postgres can be provisioned,
with a message naming every mechanism it tried.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DB_DIR = _BACKEND_ROOT / "db"
_MIGRATE_SCRIPT = _BACKEND_ROOT / "scripts" / "migrate.py"

# Pre-hardening baseline: everything numbered <= this is "V1 before the
# hardening additions". 35/36/37 are the additions under test.
_BASELINE_MAX = 34


# ---------------------------------------------------------------------------
# migrate.py is a script, not an importable package member -- load it by path
# so the test uses the project's real file discovery + checksum logic.
# ---------------------------------------------------------------------------
def _load_migrate_module():
    spec = importlib.util.spec_from_file_location("_stealthlab_migrate", _MIGRATE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MIG = _load_migrate_module()


def _migration_files() -> list[Path]:
    return _MIG._real_files(_DB_DIR)


def _prefix_num(path: Path) -> int:
    return int(re.match(r"(\d+)", path.name).group(1))


# ---------------------------------------------------------------------------
# Disposable-Postgres provisioning. Two mechanisms, tried in order:
#   (a) a throwaway cluster built with the local initdb/pg_ctl toolchain
#       (pgvector must be available to that server -- migration 01 needs it);
#   (b) a `pgvector/pgvector:pg15` docker container.
# If neither is available the whole module skips with a message listing both.
# ---------------------------------------------------------------------------
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _pg_bin(name: str) -> str | None:
    """initdb/pg_ctl/pg_isready on PATH, or under a Windows PG install."""
    found = shutil.which(name)
    if found:
        return found
    for base in (
        r"C:\Program Files\PostgreSQL",
        "/usr/lib/postgresql",
        "/opt/homebrew/opt",
        "/usr/local/opt",
    ):
        p = Path(base)
        if not p.exists():
            continue
        hits = sorted(p.glob(f"*/bin/{name}")) + sorted(p.glob(f"*/bin/{name}.exe"))
        if hits:
            return str(hits[-1])
    return None


@contextmanager
def _local_cluster():
    initdb = _pg_bin("initdb")
    pg_ctl = _pg_bin("pg_ctl")
    pg_isready = _pg_bin("pg_isready")
    if not (initdb and pg_ctl and pg_isready):
        raise _NoDisposableDB("local initdb/pg_ctl/pg_isready not on PATH")

    tmp = Path(tempfile.mkdtemp(prefix="stealthlab_upgrade_pg_"))
    data = tmp / "data"
    logfile = tmp / "pg.log"
    port = _free_port()
    proc = None
    try:
        r = subprocess.run(
            [initdb, "-D", str(data), "-U", "postgres", "--auth=trust",
             "-E", "UTF8", "--no-locale"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise _NoDisposableDB(f"initdb failed: {r.stderr.strip()[:400]}")

        # Start detached (no -w: pg_ctl -w wait hangs under Git Bash on
        # Windows); poll pg_isready for readiness instead.
        proc = subprocess.Popen(
            [pg_ctl, "start", "-D", str(data), "-l", str(logfile),
             "-o", f"-p {port} -c listen_addresses=127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            if subprocess.run([pg_isready, "-h", "127.0.0.1", "-p", str(port)],
                              capture_output=True).returncode == 0:
                break
            time.sleep(0.5)
        else:
            tail = logfile.read_text(errors="replace")[-800:] if logfile.exists() else ""
            raise _NoDisposableDB(f"local postgres did not become ready; log tail:\n{tail}")

        admin = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
        _psql_exec(admin, "CREATE DATABASE up_test", pg_bin=_pg_bin)
        yield f"postgresql://postgres:postgres@127.0.0.1:{port}/up_test"
    finally:
        try:
            subprocess.run([pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
                           capture_output=True, text=True, timeout=30)
        except Exception:
            pass
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        _rmtree_retry(tmp)


def _psql_exec(dsn: str, sql: str, *, pg_bin) -> None:
    psql = pg_bin("psql")
    if not psql:
        # asyncpg fallback -- CREATE DATABASE cannot run inside a tx, so
        # use a bare connection with autocommit semantics.
        import asyncpg

        async def _go():
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute(sql)
            finally:
                await conn.close()

        asyncio.run(_go())
        return
    env = {**os.environ, "PGPASSWORD": "postgres"}
    r = subprocess.run([psql, dsn, "-v", "ON_ERROR_STOP=1", "-c", sql],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise _NoDisposableDB(f"psql '{sql}' failed: {r.stderr.strip()[:400]}")


@contextmanager
def _docker_container():
    docker = shutil.which("docker")
    if not docker:
        raise _NoDisposableDB("docker not on PATH")
    if subprocess.run([docker, "info"], capture_output=True).returncode != 0:
        raise _NoDisposableDB("docker daemon not reachable")

    name = f"stealthlab-upgrade-{uuid.uuid4().hex[:10]}"
    port = _free_port()
    try:
        r = subprocess.run(
            [docker, "run", "-d", "--name", name,
             "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=up_test",
             "-p", f"127.0.0.1:{port}:5432", "pgvector/pgvector:pg15"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise _NoDisposableDB(f"docker run failed: {r.stderr.strip()[:400]}")

        import asyncpg

        dsn = f"postgresql://postgres:postgres@127.0.0.1:{port}/up_test"
        deadline = time.time() + 60
        last = ""
        while time.time() < deadline:
            try:
                async def _ping():
                    c = await asyncpg.connect(dsn)
                    await c.close()

                asyncio.run(_ping())
                break
            except Exception as e:  # noqa: BLE001
                last = str(e)
                time.sleep(1)
        else:
            raise _NoDisposableDB(f"docker postgres never accepted connections: {last}")
        yield dsn
    finally:
        subprocess.run([docker, "rm", "-f", name], capture_output=True, text=True)


class _NoDisposableDB(RuntimeError):
    pass


@contextmanager
def _disposable_postgres():
    tried: list[str] = []
    for factory, label in ((_local_cluster, "local initdb cluster"),
                           (_docker_container, "pgvector/pgvector:pg15 docker container")):
        try:
            with factory() as dsn:
                yield dsn
            return
        except _NoDisposableDB as e:
            tried.append(f"{label}: {e}")
        except FileNotFoundError as e:
            tried.append(f"{label}: {e}")
    pytest.skip(
        "no disposable Postgres could be provisioned for the migration "
        "upgrade-path test. Tried:\n  - " + "\n  - ".join(tried)
    )


def _rmtree_retry(path: Path) -> None:
    for _ in range(10):
        try:
            shutil.rmtree(path)
            return
        except (PermissionError, OSError):
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# Migration application. Phase 1 replicates scripts/migrate.py's apply loop
# (ledger + checksum + one tx per file) for the 01..34 subset -- there is no
# --target flag. Phase 2 invokes the REAL scripts/migrate.py for 35/36/37,
# so the upgrade leg is exercised through the shipped runner.
# ---------------------------------------------------------------------------
async def _apply_baseline(dsn: str) -> list[str]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    applied: list[str] = []
    try:
        await conn.execute(_MIG.LEDGER_DDL)
        for path in _migration_files():
            if _prefix_num(path) > _BASELINE_MAX:
                continue
            checksum = _MIG._checksum(path)
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (filename, checksum, kind) "
                    "VALUES ($1, $2, 'schema')",
                    path.name, checksum,
                )
            applied.append(path.name)
        return applied
    finally:
        await conn.close()


def _run_real_migrate(dsn: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_MIGRATE_SCRIPT), "--dsn", dsn, *args],
        capture_output=True, text=True, cwd=str(_BACKEND_ROOT),
    )


# ---------------------------------------------------------------------------
# Pre-hardening dataset. Real service write paths where practical.
# ---------------------------------------------------------------------------
class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        h = abs(hash(text))
        return [((h >> (i % 40)) & 1) * 0.1 + 0.01 for i in range(1024)]


async def _seed_pre_hardening(dsn: str) -> dict:
    """Insert a realistic V1 dataset and return the recorded ids/values."""
    from app.db.session import create_pool
    from app.execution import implementation_registry
    from app.services.claims import capture_claim
    from app.utils.ids import uuid7

    tag = uuid.uuid4().hex[:8]
    pool = await create_pool(dsn, statement_cache_size=0, min_size=1, max_size=4)
    rec: dict = {"tag": tag}
    try:
        emb = _FakeEmbedder()

        async def capture_v1_procedure(*, name: str, goal: str, steps: list,
                                       owner_id: str | None = None,
                                       visibility: str = "public") -> dict:
            """Seed through the actual V1 schema, not today's writer.

            This test intentionally stops before migration 42, where the
            current writer's embedding-provider fields were introduced. A
            historical upgrade fixture must not require future columns just
            to create a representative pre-hardening row.
            """
            row = await pool.fetchrow(
                "INSERT INTO procedures (name, goal, steps, provenance, created_by, "
                "owner_id, visibility, embedding, scope_type, embedding_model_id, embedding_dim) "
                "VALUES ($1, $2, $3::jsonb, 'prior_library', 'up_e2e', $4, "
                "$5::visibility_level, $6::vector, 'global', 'upgrade-fixture', 1024) "
                "RETURNING id, procedure_id",
                name, goal, steps, owner_id, visibility,
                "[" + ",".join(str(v) for v in await emb.embed_one(name)) + "]",
            )
            return dict(row)

        # --- procedure via the actual pre-hardening schema (public; becomes a Solution) ---
        pub = await capture_v1_procedure(
            name=f"up-e2e-{tag}-public-proc", goal="public procedure goal",
            steps=[{"order": 0, "goal": "step a"}, {"order": 1, "goal": "step b"}],
        )
        rec["proc_pub_id"] = pub["procedure_id"]
        rec["proc_pub_row"] = pub["id"]

        # --- a private procedure -- the scope/visibility probe ---
        priv = await capture_v1_procedure(
            name=f"up-e2e-{tag}-private-proc", goal="private procedure goal",
            steps=[{"order": 0, "goal": "secret"}],
            visibility="private", owner_id="up-e2e-owner",
        )
        rec["proc_priv_id"] = priv["procedure_id"]
        rec["proc_priv_row"] = priv["id"]

        async with pool.acquire() as conn:
            # --- task_node (raw INSERT -- same shape every *_e2e uses) ---
            skill_ref = f"up-e2e-{tag}-skill"
            tn_id = await conn.fetchval(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ($1, 'up-e2e task', $2) RETURNING id",
                f"up-e2e-{tag}-task", skill_ref,
            )
            rec["task_node_id"] = str(tn_id)
            rec["task_node_skill_ref"] = skill_ref

            pv = await conn.fetchval(
                "SELECT version FROM procedures WHERE id=$1", pub["id"],
            )
            rec["proc_pub_version"] = pv

            # --- execution_plan + task_graph + executions + evidence ---
            plan_id = await conn.fetchval(
                "INSERT INTO execution_plans (id, procedure_id, procedure_version, "
                " procedure_row_id, task_description, procedure_content_hash, "
                " content_hash, scope_type) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                str(uuid7()), pub["procedure_id"], pv, pub["id"],
                "up-e2e pre-hardening plan",
                f"pch-{tag}", f"ch-{tag}",
            )
            rec["plan_id"] = str(plan_id)
            graph_id = await conn.fetchval(
                "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                str(uuid7()), plan_id, f"gh-{tag}",
            )
            rec["graph_id"] = str(graph_id)

            exec_ids = []
            for i in range(6):
                outcome = "success" if i < 5 else "failure"
                eid = await conn.fetchval(
                    "INSERT INTO executions (id, execution_plan_id, task_graph_id, "
                    " procedure_id, procedure_version, started_at, ended_at, outcome, "
                    " created_by, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5, now() - interval '10 seconds', now(), "
                    " $6, 'up_e2e', 'global') RETURNING id",
                    str(uuid7()), plan_id, graph_id, pub["procedure_id"], pv, outcome,
                )
                exec_ids.append(str(eid))
            rec["exec_ids"] = exec_ids

            ev_ids = []
            for _ in range(5):
                ev = await conn.fetchval(
                    "INSERT INTO evidence (id, evidence_type, target_type, target_id, "
                    " target_version, direction, strength_score, strength_method, "
                    " outcome_status, success_criteria, created_by) "
                    "VALUES ($1,'execution_result','procedure',$2,$3,'supports',1.0,"
                    " 'up_e2e_probe','success','{\"predicate\": \"case_passed\"}'::jsonb,"
                    " 'up_e2e') RETURNING id",
                    str(uuid7()), pub["procedure_id"], pv,
                )
                ev_ids.append(str(ev))
            rec["evidence_ids"] = ev_ids

        # --- claim via the real capture path (anchored on the task_node) ---
        claim_id = await capture_claim(
            pool, statement=f"up-e2e-{tag}: the backoff must include jitter",
            task_ids=[skill_ref], created_by="up_e2e",
            claim_type="constraint", confidence=0.9, embedder=emb,
        )
        assert claim_id, "capture_claim returned None -- task anchor did not resolve"
        rec["claim_id"] = claim_id

        # --- implementation via the real registry write path ---
        impl = await implementation_registry.register(
            pool, name=f"up-e2e-{tag}-impl", kind="deterministic", provider="up_e2e",
            created_by="up_e2e", description="pre-hardening implementation",
            task_node_ids=[str(tn_id)],
        )
        rec["impl_id"] = impl["id"]
        rec["impl_name"] = impl["name"]
        rec["impl_provider"] = impl["provider"]

        # --- freeze the "before" snapshot of every recorded row ---
        rec["snapshot_before"] = await _snapshot(pool, rec)
        return rec
    finally:
        await pool.close()


async def _snapshot(pool, rec: dict) -> dict:
    """Byte-for-byte-comparable view of every pre-existing row we recorded."""
    async with pool.acquire() as conn:
        snap: dict = {}

        for key, pid in (("proc_pub", rec["proc_pub_id"]), ("proc_priv", rec["proc_priv_id"])):
            r = await conn.fetchrow(
                "SELECT procedure_id, name, goal, verification_state, visibility, "
                " owner_id, scope_type, created_at, version FROM procedures "
                "WHERE procedure_id=$1 ORDER BY version DESC LIMIT 1",
                pid,
            )
            snap[key] = dict(r)

        snap["task_node"] = dict(await conn.fetchrow(
            "SELECT name, description, skill_ref FROM task_nodes WHERE id=$1",
            uuid.UUID(rec["task_node_id"]),
        ))
        snap["claim"] = dict(await conn.fetchrow(
            "SELECT node_type, name, properties::text AS props, created_by, provenance "
            "FROM knowledge_nodes WHERE id=$1",
            uuid.UUID(rec["claim_id"]),
        ))
        snap["implementation"] = dict(await conn.fetchrow(
            "SELECT name, provider, version, status, verification_status "
            "FROM implementations WHERE id=$1",
            uuid.UUID(rec["impl_id"]),
        ))
        snap["execution_plan"] = dict(await conn.fetchrow(
            "SELECT procedure_id, procedure_version, content_hash, "
            " procedure_content_hash, task_description, scope_type "
            "FROM execution_plans WHERE id=$1",
            uuid.UUID(rec["plan_id"]),
        ))
        snap["task_graph"] = dict(await conn.fetchrow(
            "SELECT execution_plan_id, graph_hash FROM task_graphs WHERE id=$1",
            uuid.UUID(rec["graph_id"]),
        ))
        ex = await conn.fetch(
            "SELECT id, outcome FROM executions WHERE id = ANY($1::uuid[]) ORDER BY id",
            rec["exec_ids"],
        )
        snap["executions"] = [(str(r["id"]), r["outcome"]) for r in ex]
        snap["evidence_count"] = await conn.fetchval(
            "SELECT count(*) FROM evidence WHERE id = ANY($1::uuid[])",
            rec["evidence_ids"],
        )
        return snap


# ---------------------------------------------------------------------------
# THE TEST
# ---------------------------------------------------------------------------
def test_migration_upgrade_path_populated_v1_to_hardening():
    # Grep proof (mirrors the acceptance note): the hardening migrations
    # (35..38) carry no destructive DDL.
    g = subprocess.run(
        ["git", "grep", "-nE", "DROP TABLE|DROP COLUMN|TRUNCATE",
         "--", "db/35_*", "db/36_*", "db/37_*", "db/38_*", "db/39_*", "db/40_*", "db/42_*", "db/43_*"],
        capture_output=True, text=True, cwd=str(_BACKEND_ROOT),
    )
    assert g.returncode == 1 and g.stdout.strip() == "", (
        f"hardening migrations contain destructive DDL:\n{g.stdout}"
    )

    all_files = _migration_files()
    baseline_files = [p for p in all_files if _prefix_num(p) <= _BASELINE_MAX]
    hardening_files = [p for p in all_files if _prefix_num(p) > _BASELINE_MAX]
    required_hardening = {
        "35_product_model.sql",
        "36_durable_execution_runs.sql",
        "37_execution_runs_terminal_chk_fix.sql",
        "38_candidates_no_action_justified.sql",
        "39_structured_skill_ingestion.sql",
        "40_ingested_artifact_extractor_identity.sql",
        "42_worker_ingestion_integrity.sql",
        "43_skill_job_payload_object.sql",
    }
    assert required_hardening <= {p.name for p in hardening_files}, [p.name for p in hardening_files]

    with _disposable_postgres() as dsn:
        # --- phase 1: baseline 01..34 only ---
        applied = asyncio.run(_apply_baseline(dsn))
        assert applied == [p.name for p in baseline_files]

        import asyncpg

        async def _count(d):
            c = await asyncpg.connect(d)
            try:
                return await c.fetchval("SELECT count(*) FROM schema_migrations")
            finally:
                await c.close()

        assert asyncio.run(_count(dsn)) == len(baseline_files)

        # --- phase 2: populate the pre-hardening dataset ---
        os.environ.pop("DATABASE_URL", None)  # force explicit-dsn everywhere
        rec = asyncio.run(_seed_pre_hardening(dsn))

        # --- phase 3: apply 35/36/37 via the REAL runner ---
        up = _run_real_migrate(dsn)
        assert up.returncode == 0, f"migrate.py upgrade run failed:\n{up.stdout}\n{up.stderr}"
        for name in ("35_product_model.sql", "36_durable_execution_runs.sql",
                     "37_execution_runs_terminal_chk_fix.sql",
                     "38_candidates_no_action_justified.sql",
                     "39_structured_skill_ingestion.sql",
                     "40_ingested_artifact_extractor_identity.sql",
                     "42_worker_ingestion_integrity.sql",
                     "43_skill_job_payload_object.sql"):
            assert f"applied   {name}" in up.stdout, up.stdout

        # --- phase 4: assertions on the upgraded DB ---
        status = _run_real_migrate(dsn, "--status")
        assert status.returncode == 0, status.stderr
        assert "MISMATCH" not in status.stdout, status.stdout
        assert "pending" not in status.stdout, status.stdout
        applied_lines = [ln for ln in status.stdout.splitlines()
                         if ln.startswith("applied   ")]
        assert len(applied_lines) == len(all_files), status.stdout
        assert asyncio.run(_count(dsn)) == len(all_files)

        asyncio.run(_assert_after_upgrade(dsn, rec))


async def _assert_after_upgrade(dsn: str, rec: dict) -> None:
    from app.db.session import create_pool
    from app.execution.durable_run import execute_run, start_run
    from app.services import product_model as pm
    from app.services.access import AccessScope, TenantScope, scope_predicates

    pool = await create_pool(dsn, statement_cache_size=0, min_size=1, max_size=4)
    try:
        # (a) every pre-existing row unchanged
        after = await _snapshot(pool, rec)
        before = rec["snapshot_before"]
        assert after == before, _diff(before, after)

        # (b) scope/visibility still enforced by the real predicate SQL
        async with pool.acquire() as conn:
            # procedures carries no tenant_id column (pre-hardening V1), so the
            # tenant axis renders literal TRUE -- the visibility axis is the
            # one under test here.
            frag, params, _ = scope_predicates(
                AccessScope.anonymous(), TenantScope.unrestricted(),
                alias="p", param_index=1,
            )
            next_i = len(params) + 1
            rows = await conn.fetch(
                f"SELECT DISTINCT p.procedure_id FROM procedures p "
                f"WHERE {frag} AND p.procedure_id = ANY(${next_i}::uuid[])",
                *params, [rec["proc_pub_id"], rec["proc_priv_id"]],
            )
            visible = {str(r["procedure_id"]) for r in rows}
            assert str(rec["proc_pub_id"]) in visible, "public proc vanished after upgrade"
            assert str(rec["proc_priv_id"]) not in visible, (
                "private proc leaked to an anonymous scope after upgrade -- "
                "visibility semantics regressed"
            )

        # (c) NEW product-model tables work on the upgraded DB and reference
        #     the pre-existing procedure + executions.
        scope = AccessScope.unrestricted()
        problem = await pm.create_problem(
            pool, title=f"[up-e2e {rec['tag']}] upgraded-db product model",
            objective="new tables usable after an in-place upgrade", proposer="up_e2e",
        )
        bench = await pm.create_benchmark(
            pool, problem_id=problem["id"], name="up-bench", version=1,
            evaluation_protocol={"verification": "deterministic"},
            environment_specification={"runtime": "linux"},
        )
        sol = await pm.associate_solution(
            pool, problem_id=problem["id"], solution_type="procedure",
            target_id=rec["proc_pub_id"], proposer="up_e2e", status="active",
        )
        ev = await pm.request_evaluation(
            pool, problem_id=problem["id"], benchmark_id=bench["id"], solution_id=sol["id"],
            procedure_id=rec["proc_pub_id"], procedure_version=rec["proc_pub_version"],
            environment={"runtime": "linux"}, methodology={"verification": "deterministic"},
        )
        done = await pm.complete_evaluation(pool, ev["id"], execution_ids=rec["exec_ids"])
        assert done["metrics"]["run_count"] == len(rec["exec_ids"]), done
        assert done["verification_summary"]["verified_successes"] == 5, done

        lb = await pm.problem_leaderboard(pool, problem["id"], scope=scope)
        assert lb["leaderboard"], lb
        assert any(e["solution_id"] == sol["id"] for e in lb["leaderboard"]), lb

        # (d) NEW durable-run tables work against the pre-existing plan/graph.
        deps = {0: [], 1: [0]}
        run_id = await start_run(
            pool, execution_plan_id=rec["plan_id"], task_graph_id=rec["graph_id"],
            procedure_id=rec["proc_pub_id"], procedure_version=rec["proc_pub_version"],
            node_orders=[0, 1], deps=deps, max_attempts=3, created_by="up_e2e",
        )

        async def _run_node(order: int, attempt: int) -> dict:
            return {"order": order, "attempt": attempt, "ok": True}

        result = await execute_run(pool, run_id, deps=deps, run_node=_run_node,
                                   worker_id="up-e2e-worker")
        assert result["status"] == "succeeded", result
    finally:
        await pool.close()


def _diff(before: dict, after: dict) -> str:
    lines = ["pre-existing rows changed across the 35/36/37 upgrade:"]
    for k in before:
        if before[k] != after.get(k):
            lines.append(f"  {k}:\n    before={before[k]!r}\n    after ={after.get(k)!r}")
    return "\n".join(lines)

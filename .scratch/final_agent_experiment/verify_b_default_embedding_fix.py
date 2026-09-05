"""
ONE non-scored B_default retrieval verification, real MCP path.

Not a scored trial, not an agent run: this calls the exact same real
functions LocalAgentRunner.run() calls for its RETRIEVAL DECISION (the
segment that crashed) -- _open_client_session, probe_environment,
LocalProcedureStore, Embedder().embed_one, orchestrate_unified_search --
in the same order run() calls them, then stops before any agent execution.
No create_file/edit_file ever runs, no GENERAL_COMPUTE tokens are spent,
nothing is written to scored_final/scored_final_v2, no verifier is
invoked. This proves the embedding fix (backend/app/services/embeddings.py
_embed_voyage's max_retries) at the real crash site
(runner.py:378, inside the real MCP session's real nested TaskGroups) without
touching, re-running, or scoring the frozen T1 matrix.

Two calls only, paced >=25s apart (Voyage's real 3-RPM/10K-TPM billing-tier
throttle has no local pacing at all in embeddings.py -- only Gemini's chain
entry does), to avoid needlessly re-provoking the exact rate condition this
fix targets while still exercising the real, unmodified retry path:

  1. RELEVANT control: real corpus query for one of the three genuinely
     admitted candidate procedures (git-worktree isolation), allow_unverified
     =True -- the only way this specific corpus (all candidate-status, zero
     verified) can produce a real match, exactly as retrieval-calibration.md
     already established.
  2. NEGATIVE control: the same, already-established irrelevant domain
     (irrigation) that retrieval-calibration.md measured well below the
     0.45 similarity floor -- must abstain (empty ranked list), not crash.

Both go through orchestrate_unified_search exactly as run() does: a REAL
client-side embed_one call (the crash site) feeding a REAL remote
search_procedures call (the server-side embed_one call) over one REAL MCP
session -- if either embedding call still crashed, this would surface it
exactly as the two observed trials did.

Run:  python verify_b_default_embedding_fix.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from orchestrator import _start_mcp_server  # noqa: E402

FROZEN_COMMIT = "bd768e62a887b13a94fdd118693a5c671df1cf95"
PORT = 8850
TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "b-default-embedding-verify"


def _load_env() -> dict:
    env = os.environ.copy()
    env_path = REPO_ROOT / "backend" / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
            os.environ.setdefault(k.strip(), v.strip())
    return env


def _make_worktree() -> Path:
    import subprocess
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    wt_path = TMP_ROOT / f"verify-{uuid.uuid4().hex[:10]}"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt_path), FROZEN_COMMIT],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    return wt_path


def _remove_worktree(wt_path: Path) -> None:
    import shutil
    import subprocess
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(wt_path)],
        cwd=REPO_ROOT, check=False, capture_output=True, text=True,
    )
    shutil.rmtree(wt_path, ignore_errors=True)


async def _retrieval_only(session, store, *, label, task_description, allow_unverified,
                           local_facts, invariant_bindings) -> dict:
    """The exact retrieval-decision prefix of LocalAgentRunner.run() (real
    functions, real call order) -- stops at matched/source, never executes."""
    from app.services.embeddings import Embedder
    from app.local_agent.unified_retrieval import orchestrate_unified_search

    t0 = time.time()
    error = None
    ranked_out = []
    try:
        query_embedding = await Embedder().embed_one(task_description, input_type="query")
        ranked = await orchestrate_unified_search(
            session, store,
            task_description=task_description,
            invariant_bindings=invariant_bindings,
            environment_facts=local_facts,
            require_verified=not allow_unverified,
            limit=3,
            query_embedding=query_embedding,
        )
        ranked_out = [
            {"source": r.source, "name": (r.procedure or {}).get("name"),
             "rank_key": r.rank_key}
            for r in ranked
        ]
    except Exception as exc:  # noqa: BLE001 -- record the real failure, never swallow
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.time() - t0
    return {"label": label, "elapsed_s": round(elapsed, 2), "error": error,
            "matched": bool(ranked_out), "ranked": ranked_out}


async def main() -> int:
    env = _load_env()
    from app.local_agent.runner import _open_client_session
    from app.local_agent.local_store import LocalProcedureStore
    from app.services.environment_facts import (
        invariant_bindings_from_facts, probe_environment, probe_python_version,
    )

    token = os.environ.get("STEALTHLAB_MCP_TOKEN")
    proc = _start_mcp_server(REPO_ROOT, PORT, env)
    wt_path = _make_worktree()
    results = []
    try:
        await asyncio.sleep(15.0)
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            print("ABORT: MCP server exited early:\n" + out[-3000:])
            return 1
        server_url = f"http://127.0.0.1:{PORT}/mcp"

        async with _open_client_session(server_url, token) as session:
            await session.initialize()

            local_facts = await asyncio.to_thread(probe_environment, str(wt_path))
            local_facts = local_facts + [probe_python_version()]
            invariant_bindings = invariant_bindings_from_facts(local_facts)
            store = LocalProcedureStore(str(wt_path))

            print("=== [1/2] RELEVANT control (allow_unverified=True) ===", flush=True)
            r1 = await _retrieval_only(
                session, store, label="relevant_control",
                task_description=(
                    "Several coding agents need to work on the same git repository at the "
                    "same time without their file edits colliding with each other."
                ),
                allow_unverified=True, local_facts=local_facts,
                invariant_bindings=invariant_bindings,
            )
            results.append(r1)
            print(json.dumps(r1, indent=2), flush=True)

            print("=== waiting 25s (real, unpaced 3-RPM Voyage throttle) ===", flush=True)
            await asyncio.sleep(25.0)

            print("=== [2/2] NEGATIVE control (allow_unverified=True) ===", flush=True)
            r2 = await _retrieval_only(
                session, store, label="negative_control",
                task_description=(
                    "Calculate the optimal seasonal irrigation schedule for a commercial "
                    "almond orchard in a Mediterranean climate."
                ),
                allow_unverified=True, local_facts=local_facts,
                invariant_bindings=invariant_bindings,
            )
            results.append(r2)
            print(json.dumps(r2, indent=2), flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        _remove_worktree(wt_path)

    print("\n=== SUMMARY ===")
    no_crash = all(r["error"] is None for r in results)
    relevant_ok = results[0]["error"] is None and results[0]["matched"]
    negative_ok = results[1]["error"] is None and not results[1]["matched"]
    print("no_provider_crash        ", no_crash)
    print("relevant_control_matched ", relevant_ok, "(", results[0]["ranked"], ")")
    print("negative_control_abstains", negative_ok, "(", results[1]["ranked"], ")")

    (HERE / "b_default_embedding_verification_result.json").write_text(
        json.dumps({"no_provider_crash": no_crash, "relevant_control_ok": relevant_ok,
                    "negative_control_ok": negative_ok, "results": results}, indent=2),
        encoding="utf-8",
    )
    return 0 if (no_crash and relevant_ok and negative_ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

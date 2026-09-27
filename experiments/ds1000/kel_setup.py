"""Kel learns from the FIT problems only -- through its own pipelines, into kel_ds1000_demo.

    python kel_setup.py name        # Goal name per fit problem (gemma, one generic sentence; stand-in for ingestion)
    python kel_setup.py import      # fit problems -> Goals under library domain Goals + frozen benchmarks
    python kel_setup.py extract     # cheapest verified fit solution -> Procedure (Kel extract_procedure)
    python kel_setup.py notes_val   # runs/notes_fit_val.json: each fit problem's own Procedure
    (python run_models.py fit_val)  # validation runs with the Procedure
    python kel_setup.py evidence    # validation outcomes -> Procedure execution evidence
    python kel_setup.py observe     # every fit attempt -> recommender observations
    python kel_setup.py refit       # recommender joint fit (NUTS)

Reference solutions are never used (only their hash is stored with the benchmark).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from typing import Any

import demo_env

from common import DOMAINS, fit_items, load_attempts, problems

PREFERENCE = ("gemma-4-31B-it", "deepseek-v3.2", "gpt-oss-120b", "claude-sonnet-5")   # same order as the BCB demo
UNIT_SCAFFOLD = {"claude-sonnet-5": "claude-code-subagent"}
NAMES = demo_env.RUNS / "goal_names.json"
MANIFEST = demo_env.RUNS / "manifest_fit.json"
PROCS = demo_env.RUNS / "procedures_fit.json"
NAMING_SYSTEM = ("You name goals for a knowledge base. Given a programming question, write the goal the asker wants "
                 "achieved as ONE short imperative sentence (at most 14 words). Make it generic: no variable names, "
                 "no concrete data values. Mention the library if it matters. Reply with the sentence only.")


def _question(pid: str) -> str:
    return problems()[pid]["prompt"].split("\nA:\n")[0].strip()[:3000]


def name() -> None:
    from models import GeneralCompute

    gc = GeneralCompute()
    names = json.loads(NAMES.read_text(encoding="utf-8")) if NAMES.exists() else {}
    for t in fit_items():
        pid = t["problem_id"]
        if pid in names:
            continue
        r = gc.complete("gemma-4-31B-it", _question(pid), system=NAMING_SYSTEM, max_tokens=60)
        text = re.sub(r"\s+", " ", (r.text or "").strip().strip('"').strip()).rstrip(".")
        if r.error or not text:
            print(f"{pid}: naming failed {r.error}")
            continue
        names[pid] = text
        NAMES.write_text(json.dumps(names, indent=1), encoding="utf-8")
        print(f"{pid:>4} {text}")


def _tasks():
    from app.benchmarks.tasks import BenchmarkTask

    names = json.loads(NAMES.read_text(encoding="utf-8"))
    out = []
    for t in fit_items():
        pid = t["problem_id"]
        p = problems()[pid]
        n = p["metadata"]["test_case_cnt"]
        tests = [f"case_{i}" for i in range(1, n + 1)] + (["test_string"] if "test_string(" in p["code_context"] else [])
        out.append(BenchmarkTask(
            source="ds1000", source_version="2024-hf", external_id=f"DS-1000/{pid}", goal_name=names[pid],
            goal_description=p["prompt"], domains=[DOMAINS[t["library"]]], test_code=p["code_context"],
            test_names=tests, visible_tests=["case_1"] if t.get("check_kind", "case1") == "case1" and n >= 2 else [],
            entry_point="result", libs=[t["library"].lower()], split="fit",
            reference_sha256=hashlib.sha256(p["reference_code"].encode()).hexdigest(),
            extra={"problem_id": pid, "family": t["family"], "role": t["role"]}))
    return out


async def do_import(pool: Any) -> None:
    from app.benchmarks.importer import import_tasks

    import os

    if os.environ.get("KEL_PRODLIKE") == "1":
        # production-like: embedded Goals, semantic identity (+ the placement jobs it enqueues),
        # no library-derived domain edges -- the hierarchy comes from production placement only
        from app.services.embeddings import Embedder

        report = await import_tasks(pool, _tasks(), embedder=Embedder(rate_limit_pool=pool), judge_mode="model",
                                    domain_edges=False)
    else:
        report = await import_tasks(pool, _tasks(), embedder=None)
    manifest = report.pop("manifest")
    MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    report.pop("summary")
    print(json.dumps(report, indent=1, default=str))


def manifest() -> dict[str, dict]:
    return {m["external_id"].split("/")[1]: m for m in json.loads(MANIFEST.read_text(encoding="utf-8"))}


def gc_client() -> Any:
    from openai import OpenAI

    from app.config import settings
    return OpenAI(api_key=settings.general_compute_api_key, base_url=settings.general_compute_base_url)


async def extract(pool: Any) -> None:
    from app.services.procedure_extraction import extract_procedure
    from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

    man, names = manifest(), json.loads(NAMES.read_text(encoding="utf-8"))
    done = json.loads(PROCS.read_text(encoding="utf-8")) if PROCS.exists() else {}
    wins: dict[str, dict] = {}
    for r in load_attempts():
        if r["arm"] == "fit_raw" and r["gold_pass"]:
            best = wins.get(r["problem_id"])
            if best is None or PREFERENCE.index(r["model"]) < PREFERENCE.index(best["model"]):
                wins[r["problem_id"]] = r
    client = gc_client()
    for pid, attempt in sorted(wins.items(), key=lambda kv: int(kv[0])):
        if pid in done:
            continue
        source = AgentRunEvidenceSource(
            goal_text=names[pid], outcome="success", tool_sequence=["write_code", "run_tests"], steps_used=2,
            observations=[
                {"observation_type": "task_statement", "label": "task", "properties": {"text": problems()[pid]["prompt"]}},
                {"observation_type": "code_solution", "label": "verified solution",
                 "properties": {"code": attempt["code"], "verified": True, "language": "python",
                                "verified_by": "DS-1000 test_execution (local harness)"}},
            ])
        try:
            result = await extract_procedure(pool, source, client=client, visibility="public")
        except Exception as exc:  # noqa: BLE001 -- report and continue
            print(f"{pid:>4} extraction failed: {exc!r}"[:200])
            continue
        if not result.procedure_id:
            print(f"{pid:>4} no procedure: {result.validation_failures or 'abstained'}"[:200])
            continue
        goal_of = await pool.fetchval("SELECT achieves_goal_id::text FROM procedures WHERE id = $1::uuid",
                                      str(result.version_row_id or result.procedure_id))
        done[pid] = {"procedure_id": str(result.procedure_id), "version_row_id": str(result.version_row_id),
                     "extracted_by": result.extracted_by, "from_model": attempt["model"], "goal_id": goal_of,
                     "expected_goal_id": man[pid]["goal_id"]}
        PROCS.write_text(json.dumps(done, indent=1), encoding="utf-8")
        print(f"{pid:>4} procedure from {attempt['model']:<16} goal_matches={goal_of == man[pid]['goal_id']} "
              f"by={result.extracted_by}")
    print(f"procedures: {len(done)} of {len(fit_items())} fit problems ({len(wins)} had a verified solution)")


async def procedure_text(pool: Any, procedure_id: str) -> str:
    row = await pool.fetchrow(
        "SELECT name, capability_statement, steps, failure_conditions FROM procedures "
        "WHERE procedure_id = $1::uuid AND t_invalid IS NULL ORDER BY version DESC LIMIT 1", procedure_id)
    steps = row["steps"] if isinstance(row["steps"], list) else json.loads(row["steps"] or "[]")
    pitfalls = row["failure_conditions"] if isinstance(row["failure_conditions"], list) \
        else json.loads(row["failure_conditions"] or "[]")
    lines = [f"Way: {row['capability_statement'] or row['name']}"]
    for s in sorted(steps, key=lambda s: s.get("order", 0)):
        apis = ", ".join(i.get("name", "") for i in (s.get("allowed_implementations") or []) if i.get("name"))
        lines.append(f"  {s.get('order')}. {s.get('action')}" + (f"  [uses: {apis}]" if apis else ""))
    if pitfalls:
        lines.append("Pitfalls:")
        lines += [f"  - {p}" for p in pitfalls]
    return "\n".join(lines)


async def notes_val(pool: Any) -> None:
    procs = json.loads(PROCS.read_text(encoding="utf-8"))
    out = {pid: {"text": await procedure_text(pool, p["procedure_id"]), "ref": p["procedure_id"]}
           for pid, p in procs.items()}
    (demo_env.RUNS / "notes_fit_val.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"validation notes: {len(out)}")


async def evidence(pool: Any) -> None:
    from app.services.procedures import record_execution_outcome

    procs = json.loads(PROCS.read_text(encoding="utf-8"))
    marker = demo_env.RUNS / "evidence_recorded.json"
    if marker.exists():
        print("evidence already recorded")
        return
    n = 0
    for r in load_attempts():
        if r["arm"] != "fit_val" or r.get("call_error") or r["problem_id"] not in procs:
            continue
        await record_execution_outcome(
            pool, procedure_row_id=procs[r["problem_id"]]["version_row_id"], success=bool(r["gold_pass"]),
            context_key=f"ds1000:{r['problem_id']}:{r['model']}",
            success_criteria={"predicate": "DS-1000 test_execution passes"} if r["gold_pass"] else None,
            failure_class=None, execution_verified=True)
        n += 1
    marker.write_text(json.dumps({"recorded": n}), encoding="utf-8")
    print(f"verified procedure outcomes recorded: {n}")


async def observe(pool: Any) -> None:
    from app.routing import store
    from app.services.shards import search_pool

    man = manifest()
    procs = json.loads(PROCS.read_text(encoding="utf-8")) if PROCS.exists() else {}
    seen = {r["instance_key"] for r in await (await search_pool(pool)).fetch(
        "SELECT DISTINCT instance_key FROM routing_observations WHERE source = 'sweep'")}
    rows = []
    for r in load_attempts():
        if r["arm"] not in ("fit_raw", "fit_val") or r.get("call_error") or f"ds1000:{r['problem_id']}" in seen:
            continue
        rows.append({
            "source": "sweep", "goal_id": man[r["problem_id"]]["goal_id"],
            "procedure_id": procs.get(r["problem_id"], {}).get("procedure_id") if r["arm"] == "fit_val" else None,
            "model_key": r["model"], "scaffold": UNIT_SCAFFOLD.get(r["model"], "direct-prompt"),
            "instance_key": f"ds1000:{r['problem_id']}", "check_kind": "tests",
            "accepted": bool(r["check_pass"]), "gold_correct": bool(r["gold_pass"]),
            "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"], "reporter": None,
            "visibility": "public", "occurred_at": None,
        })
    ids = await store.insert_observations(pool, rows)
    print(f"observations imported: {len(ids)}")


async def refit(pool: Any) -> None:
    from app.routing.config import RoutingDefaults
    from app.routing.fit import nightly_refit

    import os

    long = os.environ.get("KEL_REFIT_LONG") == "1"         # round 2: longer chains after r-hat 1.11 (deviation 3)
    cfg = RoutingDefaults(draws=128, nightly_warmup=1500 if long else 400, nightly_chains=4 if long else 2,
                          nightly_samples=1000 if long else 300, embedding_dims=4)
    print(json.dumps(await nightly_refit(pool, cfg, seed=1), indent=1, default=str))


async def main(action: str) -> None:
    demo_env.verify_after_import()
    if action == "name":
        return name()
    from app.db.session import create_pool

    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    try:
        await {"import": do_import, "extract": extract, "notes_val": notes_val, "evidence": evidence,
               "observe": observe, "refit": refit}[action](pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

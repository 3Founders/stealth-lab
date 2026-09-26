"""`python -m app.ingestion.admin routing-*` commands.

    routing-status                         active parameters, diagnostics, data counts
    routing-refit [--method nuts|flow_vi]  nightly joint fit (schedule it daily)
    routing-local-refit GOAL_ID            refit one Goal now (normally queued per observation)
    routing-price MODEL --input X --output Y [--cached Z]    USD per million tokens
    routing-model MODEL [--predecessor P] [--open-weights] [--local]
    routing-import FILE.jsonl              attempt outcomes from a benchmark pipeline / public results
    routing-audit OBSERVATION_ID --correct true|false        gold label for check-error rates
    routing-sbc [--sims N]                 simulation-based calibration on the current data design
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

COMMANDS = ("routing-status", "routing-refit", "routing-local-refit", "routing-price", "routing-model",
            "routing-import", "routing-audit", "routing-sbc")


def add_parsers(sub: Any) -> None:
    sub.add_parser("routing-status")
    p = sub.add_parser("routing-refit")
    p.add_argument("--method", choices=["nuts", "flow_vi"])
    p = sub.add_parser("routing-local-refit")
    p.add_argument("goal_id")
    p = sub.add_parser("routing-price")
    p.add_argument("model")
    p.add_argument("--input", type=float, required=True, help="USD per million input tokens")
    p.add_argument("--output", type=float, required=True, help="USD per million output tokens")
    p.add_argument("--cached", type=float, help="USD per million cached input tokens")
    p = sub.add_parser("routing-model")
    p.add_argument("model")
    p.add_argument("--predecessor")
    p.add_argument("--open-weights", action="store_true", default=None)
    p.add_argument("--local", action="store_true", default=None)
    p = sub.add_parser("routing-import")
    p.add_argument("file")
    p = sub.add_parser("routing-audit")
    p.add_argument("observation_id")
    p.add_argument("--correct", choices=["true", "false"], required=True)
    p = sub.add_parser("routing-sbc")
    p.add_argument("--sims", type=int, default=100)
    p.add_argument("--draws", type=int, default=100)


def _import_row(raw: dict) -> dict:
    model = str(raw["model"]) + (f"@{raw['version']}" if raw.get("version") else "")
    accepted = raw.get("accepted", raw.get("resolved"))
    occurred = raw.get("occurred_at")
    return {
        "source": raw.get("source") or "public_import", "goal_id": raw["goal_id"],
        "procedure_id": raw.get("procedure_id"), "model_key": model, "scaffold": raw["scaffold"],
        "instance_key": str(raw["instance_key"]), "attempt_index": raw.get("attempt_index", 0),
        "check_kind": raw.get("check_kind") or "benchmark", "accepted": accepted,
        "gold_correct": raw.get("gold_correct"), "pass_fraction": raw.get("pass_fraction"),
        "tokens_in": raw.get("tokens_in"), "tokens_out": raw.get("tokens_out"),
        "tokens_cached": raw.get("tokens_cached"), "cost_usd": raw.get("cost_usd"),
        "latency_ms": raw.get("latency_ms"), "reporter": None, "recommendation_id": None,
        "visibility": raw.get("visibility") or "public", "owner_id": raw.get("owner_id"),
        "occurred_at": datetime.fromisoformat(occurred) if isinstance(occurred, str) else occurred,
    }


async def run(pool: Any, a: Any) -> int:
    from app.routing import store
    from app.services.shards import search_pool

    if a.cmd == "routing-status":
        log = await search_pool(pool)
        row = await pool.fetchrow(
            "SELECT version, method, fitted_at, diagnostics FROM routing_params WHERE status = 'active' "
            "ORDER BY version DESC LIMIT 1")
        print(json.dumps({
            "active_params": dict(row) if row else None,
            "observations": await log.fetchval("SELECT count(*) FROM routing_observations"),
            "decisions": await log.fetchval("SELECT count(*) FROM routing_decisions"),
            "goal_posteriors": await pool.fetchval("SELECT count(*) FROM routing_posteriors WHERE entity_kind = 'goal'"),
            "priced_models": await pool.fetchval("SELECT count(DISTINCT model_key) FROM routing_prices"),
        }, default=str, indent=2))
        return 0
    if a.cmd == "routing-refit":
        from app.routing.fit import nightly_refit

        print(json.dumps(await nightly_refit(pool, method=a.method), default=str, indent=2))
        return 0
    if a.cmd == "routing-local-refit":
        from app.routing.fit import local_refit

        print(json.dumps(await local_refit(pool, a.goal_id), default=str, indent=2))
        return 0
    if a.cmd == "routing-price":
        await store.set_price(pool, a.model, input_per_mtok=a.input, output_per_mtok=a.output, cached_per_mtok=a.cached)
        print(json.dumps({"model": a.model, "input": a.input, "output": a.output, "cached": a.cached}))
        return 0
    if a.cmd == "routing-model":
        await store.set_model(pool, a.model, predecessor=a.predecessor, open_weights=a.open_weights,
                              runs_locally=a.local)
        print(json.dumps((await store.model_registry(pool)).get(a.model), default=str))
        return 0
    if a.cmd == "routing-import":
        with open(a.file, encoding="utf-8") as fh:
            rows = [_import_row(json.loads(line)) for line in fh if line.strip()]
        ids = await store.insert_observations(pool, rows)
        print(json.dumps({"imported": len(ids), "next": "run routing-refit to fit them"}))
        return 0
    if a.cmd == "routing-audit":
        log = await search_pool(pool)
        tag = await log.execute("UPDATE routing_observations SET gold_correct = $2 WHERE id = $1::uuid",
                                a.observation_id, a.correct == "true")
        print(json.dumps({"updated": int(str(tag).split()[-1])}))
        return 0
    if a.cmd == "routing-sbc":
        import numpy as np

        from app.routing.fit import build_joint_data, simulation_based_calibration

        observations = await store.all_observations(pool, public_only=True)
        if not observations:
            print(json.dumps({"error": "no observations to take the design from"}))
            return 2
        goals = sorted({str(o["goal_id"]) for o in observations})
        parents = await store.ancestry(pool, goals)
        rows = await store.goal_rows(pool, sorted(set(goals) | {p for ps in parents.values() for p in ps}))
        data, _ = build_joint_data(observations, {g: r for g, r in rows.items() if r["visibility"] == "public"},
                                   parents, await store.model_registry(pool))
        ranks = simulation_based_calibration(data, sims=a.sims, draws=a.draws)
        report = {}
        for name, r in ranks.items():
            hist, _ = np.histogram(r, bins=10, range=(0, a.draws + 1))
            expected = len(r) / 10
            chi2 = float(((hist - expected) ** 2 / expected).sum())
            report[name] = {"histogram": hist.tolist(), "chi2_9df": chi2, "uniform_at_1pct": chi2 < 21.67}
        print(json.dumps(report, indent=2))
        return 0 if all(v["uniform_at_1pct"] for v in report.values()) else 1
    raise ValueError(a.cmd)

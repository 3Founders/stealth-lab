"""`admin routing-import-public`: public model results -> the recommender's tables (plan §2.2).

Reads ONLY local files (download them first; see docs/routing_priors.md) and our task -> Goal map
(ingest_task_goals, one query). Writes model cards, evidence items, per-item observations and
aggregates -- all idempotent (cards and items upsert, observations and aggregates carry a dedupe key).
Without --apply it reports what it would write and writes nothing.

Not imported here: nebius/SWE-rebench-openhands-trajectories. The OpenHands ingestion pipeline already
records those runs as routing observations on our Goals (app/ingest/openhands/pipeline.py); importing
them again would count them twice.
"""
from __future__ import annotations

import glob
import os
from collections import Counter
from typing import Any, Sequence

from app.routing import public_evidence as pe

SOURCES = ("openrouter", "swebench", "rebench", "swe-agent", "routerbench")
SWEBENCH_SPLITS = {"verified": "verified.parquet", "lite": "lite.parquet", "multilingual": "multilingual.parquet"}
BENCH = "swe-bench"


async def task_goal_map(pool: Any) -> dict[str, str]:
    """{instance_id: our goal_id} for every benchmark task we ingested (migration 129)."""
    rows = await pool.fetch("SELECT task_key, goal_id::text AS goal_id FROM ingest_task_goals WHERE task_key LIKE 'swe:%'")
    return {r["task_key"][4:]: r["goal_id"] for r in rows}


def collect(data_dir: str, sources: Sequence[str], goal_ids: dict[str, str]) -> dict[str, Any]:
    """Everything to write, from local files only. Pure (no database) so it is testable and dry-runnable."""
    out: dict[str, Any] = {"cards": [], "items": {}, "observations": [], "aggregates": [], "skipped": []}

    def path(*parts: str) -> str:
        return os.path.join(data_dir, *parts)

    card_sources = []
    if "openrouter" in sources:
        if os.path.exists(path("openrouter_models.json")):
            card_sources.append(pe.openrouter_cards(path("openrouter_models.json")))
        else:
            out["skipped"].append("openrouter: openrouter_models.json not found")
    if "swebench" in sources:
        if not os.path.isdir(path("experiments")):
            out["skipped"].append("swebench: experiments/ not found")
        else:
            all_runs = []
            for split, fname in SWEBENCH_SPLITS.items():
                if not os.path.exists(path("swe_items", fname)):
                    out["skipped"].append(f"swebench {split}: swe_items/{fname} not found")
                    continue
                items = pe.swebench_items([path("swe_items", fname)], benchmark=BENCH, goal_ids=goal_ids)
                runs = pe.swebench_runs(path("experiments"), split, universe=set(items))
                all_runs += runs
                used = {iid for r in runs for iid in r.results}
                out["items"].update({it["item_key"]: it for iid, it in items.items() if iid in used})
                out["observations"] += pe.run_observations(runs, BENCH, items)
            card_sources.append(pe.run_cards(all_runs))
    if "rebench" in sources or "swe-agent" in sources:
        paths = sorted(glob.glob(path("swe_rebench", "*.parquet")))
        if not paths:
            out["skipped"].append("rebench: swe_rebench/*.parquet not found")
            rebench: dict[str, dict] = {}
        else:
            rebench = pe.rebench_items(paths, goal_ids=goal_ids)
        if "rebench" in sources:        # features for OUR Goals (no observations of their own here)
            out["items"].update({it["item_key"]: it for it in rebench.values() if it["is_goal"]})
        if "swe-agent" in sources:
            traj = sorted(glob.glob(path("swe_agent_traj", "*.parquet")))
            if not traj:
                out["skipped"].append("swe-agent: swe_agent_traj/*.parquet not found")
            elif rebench:
                obs = pe.swe_agent_trajectories(traj, rebench)
                out["observations"] += obs
                touched = {o["goal_id"] for o in obs}
                out["items"].update({it["item_key"]: it for it in rebench.values() if it["goal_id"] in touched})
    if "routerbench" in sources:
        p = path("routerbench", "routerbench_0shot.pkl")
        if os.path.exists(p):
            out["aggregates"] += pe.routerbench(p)
        else:
            out["skipped"].append("routerbench: routerbench/routerbench_0shot.pkl not found")
    out["cards"] = list(pe.merge_cards(*card_sources).values()) if card_sources else []
    return out


def summary(collected: dict[str, Any]) -> dict[str, Any]:
    obs = collected["observations"]
    items = list(collected["items"].values())
    return {
        "cards": len(collected["cards"]),
        "evidence_items": len(items), "of_which_our_goals": sum(1 for it in items if it["is_goal"]),
        "observations": len(obs), "observations_on_our_goals": sum(1 for o in obs if o["goal_id"] in
                                                                   {it["goal_id"] for it in items if it["is_goal"]}),
        "models": len({o["model_key"] for o in obs} | {a["model_key"] for a in collected["aggregates"]}),
        "scaffolds": dict(Counter(o["scaffold"] for o in obs).most_common(10)),
        "aggregates": len(collected["aggregates"]),
        "skipped": collected["skipped"],
    }


async def run(pool: Any, data_dir: str, *, sources: Sequence[str] = SOURCES, apply: bool = False,
              batch: int = 5000) -> dict[str, Any]:
    from app.routing import store

    goal_ids = await task_goal_map(pool)
    collected = collect(data_dir, sources, goal_ids)
    report = {"applied": apply, "task_goals_known": len(goal_ids), **summary(collected)}
    if not apply:
        return report
    report["written_cards"] = await store.upsert_model_cards(pool, [c.to_row() for c in collected["cards"]])
    report["written_items"] = await store.upsert_evidence_items(pool, list(collected["items"].values()))
    obs = collected["observations"]
    written = 0
    for i in range(0, len(obs), batch):            # duplicates (same dedupe_key) are skipped by the database
        written += len(await store.insert_observations(pool, obs[i:i + batch]))
    report["observation_rows_sent"] = written
    report["written_aggregates"] = await store.upsert_aggregate_results(pool, collected["aggregates"])
    report["next"] = "run `admin routing-refit` to fit them"
    return report


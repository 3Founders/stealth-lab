"""Readers for public model results (docs/plan_2026-10_priors_library_survey.md §2.2). Pure functions
over files already downloaded to a local folder: nothing here calls the network or the database.
`admin routing-import-public` turns their output into rows (store.py); the offline evaluation
(scripts/routing_priors_eval.py) uses them directly.

    openrouter_cards(models.json)                public catalogue -> model cards (prices, context,
                                                 open weights, cutoff, release date)
    swebench_runs(experiments/, split)           SWE-bench `experiments` repo: one run per submission,
                                                 per-instance resolved (+ cost, api calls when published)
    swebench_items(SWE-bench_Verified parquet)   items: repo, created_at, patch features, test count
    rebench_items(SWE-rebench parquet)           the same for SWE-rebench (our Goals come from it)
    swe_agent_trajectories(parquets)             nebius/SWE-agent-trajectories: per-instance resolved
    openhands_trajectories(parquet)              nebius/SWE-rebench-openhands-trajectories
    routerbench(pkl)                             RouterBench: per-(dataset, model) aggregates

Every observation carries a `dedupe_key` (source-independent: benchmark item, model, scaffold, attempt),
so the same result published twice is counted once.
"""
from __future__ import annotations

import glob
import json
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Iterator, Optional, Sequence

from app.routing import cards as cardlib
from app.routing import goal_features
from app.routing.evidence import item_key, node_id

# Ensembles and multi-attempt systems do not measure ONE model, so they are not evidence for one.
_SKIP_MODEL_TAGS = {"multiple", "ensemble", ""}


def _date(value: Any) -> Optional[date]:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text.isdigit() and len(text) == 8:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    if text.isdigit():                                           # unix seconds
        return datetime.fromtimestamp(int(text), tz=timezone.utc).date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------- model cards

def _family(model_id: str) -> Optional[str]:
    """'anthropic/claude-sonnet-4.5' -> 'anthropic/claude-sonnet'; 'qwen/qwen3-coder-480b' -> 'qwen/qwen-coder'.
    A coarse product line: provider + the name with version numbers and sizes removed."""
    import re

    provider, _, name = model_id.lower().partition("/")
    if not name:
        provider, name = "", provider
    name = re.sub(r"[:@].*$", "", name)
    words = [w for w in re.split(r"[-_.\s]+", name) if w and not re.fullmatch(r"v?\d+[a-z]?|\d+(\.\d+)?[bkm]|a\d+b|"
                                                                        r"\d{4}|\d{6,8}|preview|exp|latest|instruct|"
                                                                        r"chat|it|hf|fp8|turbo", w)]
    base = re.sub(r"\d+(\.\d+)?$", "", "-".join(words[:2])).strip("-") or name
    return f"{provider}/{base}" if provider else base


def openrouter_cards(path: str) -> list[cardlib.ModelCard]:
    with open(path, encoding="utf-8") as fh:
        models = json.load(fh)["data"]
    out = []
    for m in models:
        mid = m["id"]
        if ":free" in mid or ":extended" in mid:
            continue
        pricing = m.get("pricing") or {}

        def per_mtok(key: str) -> Optional[float]:
            try:
                v = float(pricing.get(key))
            except (TypeError, ValueError):
                return None
            return None if v < 0 else v * 1e6

        total, active = cardlib.params_from_name(mid)
        reasoning = m.get("reasoning") or {}
        effort = cardlib.EFFORT.get(str(reasoning.get("default_effort") or "").lower()) if reasoning else None
        params = m.get("supported_parameters") or []
        out.append(cardlib.ModelCard(
            model_key=cardlib.canonical_key(mid), family=_family(mid), provider=mid.split("/")[0],
            release_date=_date(m.get("created")), training_cutoff=_date(m.get("knowledge_cutoff")),
            open_weights=True if m.get("hugging_face_id") else None,   # no HF id: unknown, not "closed"
            params_b=total, active_params_b=active,
            reasoning=effort if effort is not None else (0.67 if "reasoning" in params else 0.0),
            context_k=(m.get("context_length") or 0) / 1000 or None,
            price_in=per_mtok("prompt"), price_out=per_mtok("completion"),
            aliases=tuple(sorted({mid, m.get("canonical_slug") or mid, m.get("name") or mid})), source="openrouter"))
    return out


def merge_cards(*sources: Iterable[cardlib.ModelCard]) -> dict[str, cardlib.ModelCard]:
    """Later sources fill fields earlier ones left unknown; aliases accumulate."""
    from dataclasses import replace

    merged: dict[str, cardlib.ModelCard] = {}
    for source in sources:
        for card in source:
            have = cardlib.resolve(card.model_key, merged)
            if have is None:
                merged[card.model_key] = card
                continue
            fields = {f: getattr(card, f) for f in ("family", "provider", "release_date", "training_cutoff",
                                                    "open_weights", "params_b", "active_params_b", "reasoning",
                                                    "context_k", "price_in", "price_out")
                      if getattr(have, f) is None and getattr(card, f) is not None}
            merged[have.model_key] = cardlib.with_aliases(replace(have, **fields), card.model_key, *card.aliases)
    return merged


# ---------------------------------------------------------------- SWE-bench experiments

@dataclass
class Run:
    run: str                      # the submission folder name
    model_key: str
    scaffold: str
    date: date
    os_model: Optional[bool]
    release_date: Optional[date]
    reasoning: Optional[float]
    display: str
    results: dict[str, bool] = field(default_factory=dict)          # instance -> resolved
    cost: dict[str, float] = field(default_factory=dict)            # instance -> USD (when published)
    calls: dict[str, int] = field(default_factory=dict)


def _scaffold_key(agent: str) -> str:
    return cardlib.canonical_key(agent or "unknown")


def swebench_runs(root: str, split: str = "verified", *, universe: Optional[set[str]] = None) -> list[Run]:
    """One Run per single-model, single-attempt submission. `universe` is the split's instance ids
    (an instance absent from `resolved` is a failure only if it belongs to the split)."""
    import yaml

    runs = []
    for folder in sorted(glob.glob(os.path.join(root, "evaluation", split, "*"))):
        meta_path = next((p for p in (os.path.join(folder, "metadata.yaml"), os.path.join(folder, "metadata.yml"))
                          if os.path.exists(p)), None)
        if meta_path is None:
            continue
        with open(meta_path, encoding="utf-8") as fh:
            try:
                meta = yaml.safe_load(fh) or {}
            except yaml.YAMLError:
                continue
        tags, info = meta.get("tags") or {}, meta.get("info") or {}
        models = [str(m) for m in (tags.get("model") or []) if m]
        attempts = ((tags.get("system") or {}).get("attempts")) or 1
        if len(models) != 1 or str(attempts) not in ("1", "1.0") or models[0].lower() in _SKIP_MODEL_TAGS:
            continue
        name = os.path.basename(folder)
        details_path = os.path.join(folder, "per_instance_details.json")
        results_path = os.path.join(folder, "results", "results.json")
        run = Run(run=name, model_key=cardlib.canonical_key(models[0]), scaffold=_scaffold_key(str(tags.get("agent"))),
                  date=_date(name[:8]) or date(2024, 1, 1), os_model=tags.get("os_model"),
                  release_date=_date(info.get("model_release_date")),
                  reasoning=cardlib.EFFORT.get(str(tags.get("reasoning_effort") or "").lower()),
                  display=str(tags.get("model_display") or models[0]))
        if os.path.exists(details_path):
            with open(details_path, encoding="utf-8") as fh:
                details = json.load(fh)
            for iid, d in details.items():
                run.results[iid] = bool(d.get("resolved"))
                if d.get("cost") is not None:
                    run.cost[iid] = float(d["cost"])
                if d.get("api_calls") is not None:
                    run.calls[iid] = int(d["api_calls"])
        elif os.path.exists(results_path) and universe:
            with open(results_path, encoding="utf-8") as fh:
                resolved = set(json.load(fh).get("resolved") or [])
            run.results = {iid: iid in resolved for iid in universe}
        else:
            continue
        runs.append(run)
    return runs


def run_cards(runs: Sequence[Run]) -> list[cardlib.ModelCard]:
    """What the submissions themselves say about their models (release date, open weights, size)."""
    out = {}
    for r in runs:
        total, active = cardlib.params_from_name(r.model_key)
        out.setdefault(r.model_key, cardlib.ModelCard(
            model_key=r.model_key, family=_family(r.model_key), release_date=r.release_date,
            open_weights=r.os_model, params_b=total, active_params_b=active, reasoning=r.reasoning,
            aliases=(r.display,), source="swe-bench-experiments"))
    return list(out.values())


def run_observations(runs: Sequence[Run], benchmark: str, items: dict[str, dict]) -> list[dict]:
    """Per-instance 'benchmark' observations. goal_id: our Goal when the item is one, else its evidence id."""
    obs = []
    for r in runs:
        when = datetime(r.date.year, r.date.month, r.date.day, tzinfo=timezone.utc)
        for iid, ok in r.results.items():
            it = items.get(iid)
            if it is None:
                continue
            key = item_key(benchmark, iid)
            obs.append({
                "source": "public_import", "goal_id": it["goal_id"], "model_key": r.model_key, "scaffold": r.scaffold,
                "instance_key": key, "attempt_index": 0, "check_kind": "benchmark", "accepted": ok,
                "cost_usd": r.cost.get(iid), "visibility": "public", "occurred_at": when,
                "item_created_at": it.get("created_at"),
                "dedupe_key": f"{key}|{r.model_key}|{r.scaffold}|{r.run}",
            })
    return obs


# ---------------------------------------------------------------- items (SWE-bench, SWE-rebench)

def _items_from_parquet(paths: Sequence[str], benchmark: str, goal_ids: Optional[dict[str, str]] = None) -> dict[str, dict]:
    import pyarrow.parquet as pq

    out: dict[str, dict] = {}
    for path in paths:
        table = pq.read_table(path)
        cols = set(table.column_names)
        for row in table.to_pylist():
            iid = row["instance_id"]
            tests = row.get("FAIL_TO_PASS") if "FAIL_TO_PASS" in cols else None
            if isinstance(tests, str):
                try:
                    tests = json.loads(tests)
                except ValueError:
                    tests = None
            stats = goal_features.patch_stats(row.get("patch") or "", tests=len(tests) if tests is not None else None)
            our = (goal_ids or {}).get(iid)
            out[iid] = {"item_key": item_key(benchmark, iid), "goal_id": our or node_id(item_key(benchmark, iid)),
                        "is_goal": our is not None, "benchmark": benchmark, "repo": row.get("repo"),
                        "created_at": _date(row.get("created_at")), "features": stats}
    return out


def swebench_items(paths: Sequence[str], benchmark: str = "swe-bench-verified",
                   goal_ids: Optional[dict[str, str]] = None) -> dict[str, dict]:
    return _items_from_parquet(paths, benchmark, goal_ids)


def rebench_items(paths: Sequence[str], goal_ids: Optional[dict[str, str]] = None,
                  benchmark: str = "swe-rebench") -> dict[str, dict]:
    return _items_from_parquet(paths, benchmark, goal_ids)


# ---------------------------------------------------------------- trajectory datasets

def swe_agent_trajectories(paths: Sequence[str], items: dict[str, dict], *, benchmark: str = "swe-rebench",
                           scaffold: str = "swe-agent", when: date = date(2024, 12, 1)) -> list[dict]:
    """nebius/SWE-agent-trajectories: fine-tuned Llama agents, many attempts per instance."""
    import pyarrow.parquet as pq

    table = pq.read_table(list(paths), columns=["instance_id", "model_name", "target"])
    seen: dict[tuple[str, str], int] = {}
    stamp = datetime(when.year, when.month, when.day, tzinfo=timezone.utc)
    obs = []
    for iid, model, ok in zip(*(table.column(c).to_pylist() for c in ("instance_id", "model_name", "target"))):
        it = items.get(iid)
        if it is None:
            continue
        key = item_key(benchmark, iid)
        mk = cardlib.canonical_key(model)
        n = seen.get((iid, mk), 0)
        seen[(iid, mk)] = n + 1
        obs.append({"source": "public_import", "goal_id": it["goal_id"], "model_key": mk, "scaffold": scaffold,
                    "instance_key": key, "attempt_index": 0, "check_kind": "benchmark",
                    "accepted": bool(ok), "visibility": "public", "occurred_at": stamp,
                    "item_created_at": it.get("created_at"), "dedupe_key": f"{key}|{mk}|{scaffold}|nebius-sat|{n}"})
    return obs


def openhands_trajectories(path: str, items: dict[str, dict], *, benchmark: str = "swe-rebench",
                           model_key: str = "qwen3-coder-480b-a35b-instruct", scaffold: str = "openhands",
                           when: date = date(2025, 9, 1)) -> list[dict]:
    """nebius/SWE-rebench-openhands-trajectories (Qwen3-Coder-480B, OpenHands v0.54)."""
    import pyarrow.parquet as pq

    schema = pq.read_schema(path)
    wanted = [c for c in ("instance_id", "resolved", "exit_status", "trajectory_id") if c in schema.names]
    table = pq.read_table(path, columns=wanted)
    cols = {c: table.column(c).to_pylist() for c in wanted}
    stamp = datetime(when.year, when.month, when.day, tzinfo=timezone.utc)
    seen: dict[str, int] = {}
    obs = []
    for i, iid in enumerate(cols["instance_id"]):
        it = items.get(iid)
        if it is None or cols.get("resolved") is None:
            continue
        key = item_key(benchmark, iid)
        n = seen.get(iid, 0)
        seen[iid] = n + 1
        resolved = cols["resolved"][i]
        obs.append({"source": "public_import", "goal_id": it["goal_id"], "model_key": model_key, "scaffold": scaffold,
                    "instance_key": key, "attempt_index": 0, "check_kind": "benchmark",
                    "accepted": bool(resolved), "visibility": "public", "occurred_at": stamp,
                    "item_created_at": it.get("created_at"),
                    "dedupe_key": f"{key}|{model_key}|{scaffold}|nebius-oh|{n}"})
    return obs


# ---------------------------------------------------------------- aggregates

def routerbench(path: str, *, when: date = date(2024, 3, 1)) -> list[dict]:
    """RouterBench (withmartian/routerbench, 0-shot pickle): per-(dataset, model) k of n correct.
    Its items are not coding Goals, so they enter as aggregates on one benchmark node per dataset."""
    import pandas as pd

    df = pd.read_pickle(path)
    model_cols = [c for c in df.columns if c not in ("sample_id", "prompt", "eval_name", "oracle_model_to_route_to")
                  and not str(c).endswith("|model_response") and not str(c).endswith("|total_cost")]
    stamp = datetime(when.year, when.month, when.day, tzinfo=timezone.utc)
    out = []
    for dataset, part in df.groupby(df["eval_name"].astype(str).str.split(".").str[0]):
        for col in model_cols:
            vals = pd.to_numeric(part[col], errors="coerce").dropna()
            if vals.empty:
                continue
            k = int(round(float((vals >= 0.5).sum())))
            mk = cardlib.canonical_key(str(col))
            out.append({"source": "routerbench", "benchmark": f"routerbench:{dataset}", "model_key": mk,
                        "scaffold": "direct", "n": int(len(vals)), "k": k, "occurred_at": stamp,
                        "dedupe_key": f"routerbench:{dataset}|{mk}|direct|0shot"})
    return out


def aggregate_of_runs(runs: Sequence[Run], benchmark: str, items: dict[str, dict]) -> list[dict]:
    """A run's per-instance results collapsed to k of n -- used to test the aggregate likelihood against
    the per-instance one on the same data (they must agree in expectation)."""
    out = []
    for r in runs:
        res = [ok for iid, ok in r.results.items() if iid in items]
        if not res:
            continue
        dates = [items[iid]["created_at"] for iid in r.results if iid in items and items[iid].get("created_at")]
        out.append({"source": "derived", "benchmark": benchmark, "model_key": r.model_key, "scaffold": r.scaffold,
                    "n": len(res), "k": int(sum(res)),
                    "occurred_at": datetime(r.date.year, r.date.month, r.date.day, tzinfo=timezone.utc),
                    "item_created_min": min(dates) if dates else None, "item_created_max": max(dates) if dates else None,
                    "dedupe_key": f"{benchmark}|{r.model_key}|{r.scaffold}|{r.run}"})
    return out


def log_odds(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def iter_jsonl(path: str) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)

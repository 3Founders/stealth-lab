"""Build the bundled model prior: routing works before any production fit (app/routing/prior_bundle.py).

One joint fit on the public files only (docs/routing_priors.md lists them), with the model cards and the
structural task features, and no link to our Goals or to any database: what every model is generally good at,
how a task's fix size moves that, and the check error rates. The result ships with the server as three files
under app/routing/data/: prior_bundle.npz (the global draws, the same arrays a nightly refit stores),
prior_bundle.json (the fit's public meta) and prior_cards.json (every model card, so a model nobody has run yet
still gets a price and a card-based prior).

    python scripts/build_prior_bundle.py --data ../.scratch/routing-priors-data [--splits verified,lite,multilingual]

Takes about ten minutes of CPU (low-rank VI) and ~2 GB of memory. Re-run it when the public files are refreshed.

With --codes <procedure_vectors.npz> (a one-time export of the embeddings of the Ways that achieve our benchmark
Goals) it also builds the semantic codebook (app/routing/semantic_codes.py) and fits on EVERY public source the
production import reads (SWE-bench experiments, SWE-rebench + SWE-agent trajectories, RouterBench), with each
task's Way code as a parent in the Goal hierarchy. The draws of every code node ship with the bundle, so a new
Way's prior comes from the Ways like it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import routing_priors_eval as ev  # noqa: E402
from app.routing import evidence, import_public, prior_bundle, semantic_codes, token_calibration  # noqa: E402
from app.routing import fit as fitlib  # noqa: E402
from app.routing import cards as cardlib  # noqa: E402
from app.routing.config import CHECK_PRIORS  # noqa: E402
from app.routing.predict import pack, unpack  # noqa: E402


def refresh_check_rates(arrays: dict, kinds: list, seed: int = 0) -> dict:
    """The public data holds no check outcomes except benchmark grading, so every other kind's (false accept,
    false reject) draws are its prior: redraw them from CHECK_PRIORS, which carries what we measured."""
    rng = np.random.default_rng(seed)
    alpha, beta = np.array(arrays["alpha"], copy=True), np.array(arrays["beta"], copy=True)
    for i, kind in enumerate(kinds):
        if kind in CHECK_PRIORS:
            (a1, b1), (a2, b2) = CHECK_PRIORS[kind]
            alpha[:, i] = rng.beta(a1, b1, alpha.shape[0])
            beta[:, i] = rng.beta(a2, b2, beta.shape[0])
    return {**arrays, "alpha": alpha, "beta": beta}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", required=True, help="folder with the downloaded public files")
    ap.add_argument("--splits", default="verified,lite,multilingual")
    ap.add_argument("--out", default=prior_bundle.DATA_DIR)
    ap.add_argument("--method", default="auto", help="auto (what production would choose), vi or nuts")
    ap.add_argument("--full", action="store_true", help="the full draw budget instead of the fast one")
    ap.add_argument("--codes", help="procedure_vectors.npz: build the codebook and fit with code parents")
    ap.add_argument("--k1", type=int, default=24, help="coarse code groups")
    ap.add_argument("--k2", type=int, default=8, help="sub-codes per group")
    ap.add_argument("--tokens-only", action="store_true",
                    help="keep the fitted draws; refresh only the cards and the token calibration (seconds)")
    args = ap.parse_args()

    if args.codes:
        return build_with_codes(args)
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    t0 = time.time()
    data = ev.Data(args.data, splits)
    conf = ev.cfg(fast=not args.full)
    print(f"data: {len(data.items)} items, {len(data.obs)} observations, {len(data.cards)} cards "
          f"({time.time() - t0:.0f}s)", flush=True)
    tokens = token_calibration.calibrate(data.obs, data.card)
    check_meta = {"check_priors": {k: list(map(list, v)) for k, v in CHECK_PRIORS.items()}}
    print("tokens:", json.dumps(tokens["calibration"]), flush=True)
    cards = [c.to_row() for c in data.cards.values()]
    if args.tokens_only:
        meta_path = os.path.join(args.out, prior_bundle.META)
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["tokens"] = tokens
        meta.update(check_meta)
        arrays_path = os.path.join(args.out, prior_bundle.ARRAYS)
        with open(arrays_path, "rb") as fh:
            arrays = unpack(fh.read())
        with open(arrays_path, "wb") as fh:
            fh.write(pack(refresh_check_rates(arrays, meta["check_kinds"])))
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1, sort_keys=True, default=str)
        with open(os.path.join(args.out, prior_bundle.CARDS), "w", encoding="utf-8") as fh:
            json.dump(sorted(cards, key=lambda r: r["model_key"]), fh, indent=0, default=str)
        return 0
    ev.FIT_METHOD = args.method              # "auto": fit.choose_method decides, as a nightly refit would
    f = ev.fit(data.obs, data.items, cards=data.cards, features=True, conf=conf)
    g = f["g"]
    meta = {**g.meta, "fitted_at": datetime.now(timezone.utc).isoformat(), "method": f["method"],
            "source": "bundled public prior: SWE-bench " + "/".join(splits) + " experiments + OpenRouter cards",
            "n_observations": f["n_obs"], "diagnostics": f["diag"], "tokens": tokens, **check_meta}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, prior_bundle.ARRAYS), "wb") as fh:
        fh.write(pack(refresh_check_rates(dict(g.arrays), meta["check_kinds"])))
    with open(os.path.join(args.out, prior_bundle.META), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1, sort_keys=True, default=str)
    with open(os.path.join(args.out, prior_bundle.CARDS), "w", encoding="utf-8") as fh:
        json.dump(sorted(cards, key=lambda r: r["model_key"]), fh, indent=0, default=str)
    size = sum(os.path.getsize(os.path.join(args.out, n)) for n in (prior_bundle.ARRAYS, prior_bundle.META,
                                                                   prior_bundle.CARDS))
    print(json.dumps({"method": f["method"], "seconds": round(f["seconds"]), "models": len(g.models),
                      "draws": g.draws, "cards": len(cards), "bytes": size, "diagnostics": f["diag"]},
                     default=str, indent=1))
    return 0


def build_with_codes(args) -> int:
    t0 = time.time()
    z = np.load(args.codes, allow_pickle=False)
    vectors, proc_goals = z["vectors"], [str(x) for x in z["goal_ids"]]
    task_goal = dict(zip([str(x) for x in z["task_ids"]], [str(x) for x in z["task_goals"]]))
    model_id = str(z["model"])
    version = "cb1-" + datetime.now(timezone.utc).strftime("%Y%m%d")
    cb = semantic_codes.build(vectors, k1=args.k1, k2=args.k2, version=version, embedding_model_id=model_id)
    codes = cb.assign_many(vectors)
    by_goal: dict[str, dict[str, int]] = {}
    for g, c in zip(proc_goals, codes):
        by_goal.setdefault(g, {})[c] = by_goal.get(g, {}).get(c, 0) + 1
    goal_code = {g: max(cs.items(), key=lambda kv: (kv[1], kv[0]))[0] for g, cs in by_goal.items()}
    print(f"codebook {version}: {semantic_codes.describe(cb)}; {len(goal_code)} goals coded "
          f"({time.time() - t0:.0f}s)", flush=True)

    collected = import_public.collect(args.data, import_public.SOURCES, task_goal)
    obs = [{**o, "occurred_at": o.get("occurred_at") or ev.STAMP} for o in collected["observations"]]
    cards = {c.model_key: c for c in collected["cards"]}
    items = [dict(it, is_goal=False) for it in collected["items"].values()]   # every task is evidence here
    goal_meta, parents, aggs = evidence.assemble(items, collected["aggregates"])
    # code parents: task Goal -> its Way's code -> the code's coarse group
    nodes: dict[str, str] = {}
    for g in list(goal_meta):
        code = goal_code.get(g)
        if not code or goal_meta[g].get("synthetic") is None:
            continue
        leaf, top = semantic_codes.node_id(code, version), semantic_codes.node_id(semantic_codes.coarse(code), version)
        for node, name in ((leaf, code), (top, semantic_codes.coarse(code))):
            goal_meta.setdefault(node, {"visibility": "public", "embedding": None, "owner_id": None, "synthetic": True})
            nodes[node] = name
        parents.setdefault(leaf, [top])
        parents[g] = [*parents.get(g, []), leaf]
    print(f"data: {len(items)} items, {len(obs)} observations, {len(aggs)} aggregates, {len(cards)} cards, "
          f"{len(nodes)} code nodes ({time.time() - t0:.0f}s)", flush=True)
    conf = ev.cfg(fast=not args.full)
    data, meta = fitlib.build_joint_data(obs, goal_meta, parents, {}, conf, cards=cards, aggregates=aggs)
    samples, used, diag = fitlib.run_joint(data, conf, seed=0, method=None if args.method == "auto" else args.method)
    arrays = fitlib.global_arrays(samples, meta)
    index = {g: i for i, g in enumerate(meta["goals"])}
    code_list = sorted((name, node) for node, name in nodes.items() if node in index)
    arrays["code_x"] = np.stack([samples["goal_x"][:, index[node], :] for _, node in code_list], axis=1) \
        if code_list else np.zeros((samples["goal_x"].shape[0], 0, samples["goal_x"].shape[2]))
    resolve = lambda m: cardlib.resolve(m, cards)                       # noqa: E731
    tokens = token_calibration.calibrate(obs, resolve)
    out_meta = {**fitlib.public_meta(meta), "fitted_at": datetime.now(timezone.utc).isoformat(), "method": used,
                "source": "bundled public prior: SWE-bench experiments, SWE-rebench + SWE-agent trajectories, "
                          "RouterBench, OpenRouter cards; Way codes as parents",
                "n_observations": len(obs), "diagnostics": diag, "tokens": tokens,
                "check_priors": {k: list(map(list, v)) for k, v in CHECK_PRIORS.items()},
                "codebook_version": version, "code_nodes": [name for name, _ in code_list]}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, prior_bundle.ARRAYS), "wb") as fh:
        fh.write(pack(refresh_check_rates(arrays, out_meta["check_kinds"])))
    with open(os.path.join(args.out, prior_bundle.META), "w", encoding="utf-8") as fh:
        json.dump(out_meta, fh, indent=1, sort_keys=True, default=str)
    with open(os.path.join(args.out, prior_bundle.CARDS), "w", encoding="utf-8") as fh:
        json.dump(sorted((c.to_row() for c in cards.values()), key=lambda r: r["model_key"]), fh, indent=0, default=str)
    with open(os.path.join(args.out, semantic_codes.CODEBOOK), "wb") as fh:
        fh.write(cb.to_bytes())
    print(json.dumps({"method": used, "seconds": round(time.time() - t0), "models": len(meta["models"]),
                      "goals": len(meta["goals"]), "code_nodes": len(code_list), "diagnostics": diag},
                     default=str, indent=1))
    return 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    raise SystemExit(main())

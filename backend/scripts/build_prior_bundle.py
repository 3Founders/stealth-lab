"""Build the bundled model prior: routing works before any production fit (app/routing/prior_bundle.py).

One joint fit on the public files only (docs/routing_priors.md lists them), with the model cards and the
structural task features, and no link to our Goals or to any database: what every model is generally good at,
how a task's fix size moves that, and the check error rates. The result ships with the server as three files
under app/routing/data/: prior_bundle.npz (the global draws, the same arrays a nightly refit stores),
prior_bundle.json (the fit's public meta) and prior_cards.json (every model card, so a model nobody has run yet
still gets a price and a card-based prior).

    python scripts/build_prior_bundle.py --data ../.scratch/routing-priors-data [--splits verified,lite,multilingual]

Takes about ten minutes of CPU (low-rank VI) and ~2 GB of memory. Re-run it when the public files are refreshed.
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
from app.routing import prior_bundle, token_calibration  # noqa: E402
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
    ap.add_argument("--tokens-only", action="store_true",
                    help="keep the fitted draws; refresh only the cards and the token calibration (seconds)")
    args = ap.parse_args()

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


if __name__ == "__main__":
    np.seterr(all="ignore")
    raise SystemExit(main())

"""The bundled model prior: what routing knows before any production fit exists.

`scripts/build_prior_bundle.py` fits the routing model once on public files only (SWE-bench experiment results,
the benchmark items' fix sizes, OpenRouter model cards), with no link to our Goals and no database. It ships as
three files in `app/routing/data/`. The service uses it whenever the database has no fitted parameters yet (a
fresh deployment, a local server, the local-only experiment), and its cards fill in any model the database has
no card for. A nightly refit, once one runs, replaces it: the database's parameters always win.

Version: `BUNDLE_VERSION` (0). Database versions start at 1, so a stored Goal posterior is never mistaken for one
aligned with the bundle.
"""
from __future__ import annotations

import functools
import json
import os
from typing import Any, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ARRAYS, META, CARDS = "prior_bundle.npz", "prior_bundle.json", "prior_cards.json"
BUNDLE_VERSION = 0


def available(data_dir: str = DATA_DIR) -> bool:
    return all(os.path.isfile(os.path.join(data_dir, n)) for n in (ARRAYS, META))


@functools.lru_cache(maxsize=4)
def load_globals(data_dir: str = DATA_DIR) -> Optional[Any]:
    """The bundled `predict.Globals`, or None when the files are not there."""
    if not available(data_dir):
        return None
    from app.routing import predict

    with open(os.path.join(data_dir, ARRAYS), "rb") as fh:
        blob = fh.read()
    with open(os.path.join(data_dir, META), encoding="utf-8") as fh:
        meta = json.load(fh)
    meta.setdefault("tokens", {})
    meta["bundled"] = True
    return predict.load_globals(BUNDLE_VERSION, blob, meta)


@functools.lru_cache(maxsize=4)
def card_rows(data_dir: str = DATA_DIR) -> tuple[dict, ...]:
    """The bundled model cards as `routing_model_cards` rows (empty when the file is not there)."""
    path = os.path.join(data_dir, CARDS)
    if not os.path.isfile(path):
        return ()
    with open(path, encoding="utf-8") as fh:
        return tuple(json.load(fh))

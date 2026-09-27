"""The sample, fixed BEFORE any model runs, plus a hash of the design and the analysis plan.

Families: DS-1000 problems sharing a `perturbation_origin_id` (an original StackOverflow
problem and its Surface / Semantic / Difficult-Rewrite variants). Only eligible problems
(runs/eligibility.json) are used. Every random choice is a sha256 order under SEED.

  fit (imported into Kel; models attempt them; Kel learns from verified solutions)
    * TRANSFER_FAMILIES origins of families that also have >= 1 eligible variant
    * DISTRACTORS origins of other families (a realistic library: most stored knowledge
      is NOT about the next task)
  test (never imported; they reach Kel only as find_ways queries)
    * transfer: up to VARIANTS_PER_FAMILY variants of each transfer family
    * control:  CONTROLS origins of families with no member anywhere else in the design
  Every other member of a used family is excluded from everything.

Stratified by library (largest-remainder allocation, proportional to what is available).

    python design.py      # writes runs/design.json and runs/design.sha256
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = "ds1000-kel-v1"
TRANSFER_FAMILIES = 40
DISTRACTORS = 20
CONTROLS = 20
VARIANTS_PER_FAMILY = 2
LIBRARIES = ("Pandas", "Numpy", "Scipy", "Sklearn")


def h(*parts) -> str:
    return hashlib.sha256("\x1f".join(map(str, (SEED, *parts))).encode()).hexdigest()


def allocate(total: int, available: dict[str, int]) -> dict[str, int]:
    pool = sum(available.values())
    exact = {k: total * v / pool for k, v in available.items()}
    out = {k: min(int(x), available[k]) for k, x in exact.items()}
    for k in sorted(exact, key=lambda k: (-(exact[k] - int(exact[k])), k)):
        if sum(out.values()) >= total:
            break
        if out[k] < available[k]:
            out[k] += 1
    return out


def main() -> None:
    import argparse
    import os

    global SEED, DISTRACTORS
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--transfer", type=int, default=None)
    ap.add_argument("--variants", type=int, default=None, help="max variants per family; 0 = all")
    a = ap.parse_args()
    global TRANSFER_FAMILIES, VARIANTS_PER_FAMILY
    TRANSFER_FAMILIES = a.transfer or TRANSFER_FAMILIES
    if a.variants is not None:
        VARIANTS_PER_FAMILY = a.variants or 10_000
    runs = HERE / ("runs" if a.round == 1 else f"runs{a.round}")
    runs.mkdir(exist_ok=True)
    used_before: set[str] = set()
    if a.round > 1:
        SEED, DISTRACTORS = f"ds1000-kel-v{a.round}", 0     # Kel's library already holds round 1's 60 fit problems
        for r in range(1, a.round):
            prev = json.loads((HERE / ("runs" if r == 1 else f"runs{r}") / "design.json").read_text(encoding="utf-8"))
            used_before |= {x["family"] for x in prev["fit"] + prev["test"]}
    rows = {str(json.loads(l)["metadata"]["problem_id"]): json.loads(l)
            for l in (HERE / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines()}
    elig = json.loads((HERE / "runs" / "eligibility.json").read_text(encoding="utf-8"))
    fam: dict[str, dict] = defaultdict(lambda: {"origin": None, "variants": [], "library": None})
    for pid, r in rows.items():
        m = r["metadata"]
        if m["library"] not in LIBRARIES or not elig.get(pid, {}).get("eligible"):
            continue
        f = fam[str(m["perturbation_origin_id"])]
        f["library"] = m["library"]
        if m["perturbation_type"] == "Origin":
            f["origin"] = pid
        else:
            f["variants"].append(pid)
    fam = {k: v for k, v in fam.items() if v["origin"] is not None and k not in used_before}
    by_lib = lambda pred: {lib: sorted((k for k, v in fam.items() if v["library"] == lib and pred(v)),
                                       key=lambda k: h("family", k)) for lib in LIBRARIES}

    with_variants = by_lib(lambda v: len(v["variants"]) > 0)
    n = allocate(TRANSFER_FAMILIES, {l: len(x) for l, x in with_variants.items()})
    transfer = [k for lib in LIBRARIES for k in with_variants[lib][: n[lib]]]
    used = set(transfer)

    rest = {lib: [k for k in ks if k not in used] for lib, ks in by_lib(lambda v: True).items()}
    n = allocate(DISTRACTORS, {l: len(x) for l, x in rest.items()})
    distract = [k for lib in LIBRARIES for k in rest[lib][: n[lib]]]
    used |= set(distract)

    rest = {lib: [k for k in ks if k not in used] for lib, ks in rest.items()}
    n = allocate(CONTROLS, {l: len(x) for l, x in rest.items()})
    control = [k for lib in LIBRARIES for k in rest[lib][: n[lib]]]

    test_transfer = []
    for k in transfer:
        chosen = sorted(fam[k]["variants"], key=lambda p: h("variant", p))[:VARIANTS_PER_FAMILY]
        test_transfer += [{"problem_id": p, "family": k, "library": fam[k]["library"],
                           "perturbation": rows[p]["metadata"]["perturbation_type"]} for p in chosen]
    design = {
        "seed": SEED, "libraries": LIBRARIES,
        "fit": [{"problem_id": fam[k]["origin"], "family": k, "library": fam[k]["library"],
                 "role": "transfer_origin" if k in transfer else "distractor"} for k in transfer + distract],
        "test": test_transfer + [{"problem_id": fam[k]["origin"], "family": k, "library": fam[k]["library"],
                                  "perturbation": "Origin", "role": "control"} for k in control],
    }
    for t in design["test"]:
        t.setdefault("role", "transfer")
        t["check_kind"] = elig[t["problem_id"]]["check_kind"]
    text = json.dumps(design, indent=1)
    (runs / "design.json").write_text(text, encoding="utf-8")
    prereg = (HERE / ("PREREGISTRATION.md" if a.round == 1 else f"PREREGISTRATION_{a.round}.md")).read_text(encoding="utf-8")
    digest = hashlib.sha256((text + "\n" + prereg).encode()).hexdigest()
    (runs / "design.sha256").write_text(digest + "\n", encoding="utf-8")
    from collections import Counter
    print("fit", Counter((f["library"], f["role"]) for f in design["fit"]))
    print("test", Counter((t["role"], t["perturbation"]) for t in design["test"]))
    print("test by library", Counter(t["library"] for t in design["test"]))
    print("design+preregistration sha256:", digest)


if __name__ == "__main__":
    main()

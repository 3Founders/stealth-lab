"""
Experiment 7b -- semantic search over STRIPPED representations.

AFTER's finding is that a stored item becomes more reusable once the
metadata around it is stripped away and only the reusable core is kept.
Experiment 7 embedded raw `problem_statement` text on both sides and got
dense hit@5 = 0.635. SWE-bench Pro problem statements are mostly not core:
they carry `Steps to Reproduce` walkthroughs, `## OS / Environment`
blocks, `ansible --version` dumps, config paste-ins and markdown scaffold.
None of that says where the fix lands.

This varies the DOCUMENT representation (what a stored patch looks like in
memory) and the QUERY representation (what the incoming issue looks like),
and measures the same file-overlap metric as Experiment 7.

LEAKAGE BOUNDARY, which decides whether this experiment is honest:

  Query side  may use ONLY the current instance's problem_statement. Never
              its patch, never its `interface` -- those contain the file
              paths that are the answer.
  Document side may use anything recorded about a PRIOR patch, including
              its interface and its diff. That is precisely what the memory
              is: a record of a completed fix. Storing where a past fix
              landed is the design under test
              (KNOWLEDGE_UPDATION_EXPERIMENT.md: "the graph indexes,
              doesn't duplicate"), not a leak.

So D3/D4 below are legitimate memory representations, and their advantage
over D1 -- if any -- is the actual result.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from scipy.stats import binomtest

from experiments.after.local_embed import LocalEmbedder
from experiments.after.retrieval_methods import BM25, cosine_scores, rrf

FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.M)
HUNK_RE = re.compile(r"^@@[^@]*@@\s*(.*)$", re.M)
SYMBOL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# Section headers whose content is environment/procedure, not the defect.
_DROP_SECTION = re.compile(
    r"(?is)^\s*#{0,4}\s*\**\s*("
    r"steps?\s+to\s+reproduce|reproduction|how\s+to\s+reproduce|"
    r"configuration|os\s*/?\s*environment|environment|system\s+info|"
    r"version|component\s+name|additional\s+information|actual\s+results"
    r")\b\**\s*:?\s*$"
)
_FENCE = re.compile(r"```.*?```", re.S)
_ENV_HINT = re.compile(
    r"(?i)(ansible \[|python version|config file|executable location|"
    r"module search path|\$ [a-z-]+ --version|node -v|npm -v|os\s*:|browser\s*:)"
)
_MD_NOISE = re.compile(r"(?m)^\s*[-*+]\s*$|\*\*|__|^#+\s*", re.M)


def strip_metadata(text: str) -> str:
    """
    Drop the parts of an issue report that describe the reporter's machine
    or the click-path, and keep the parts that describe the defect.

    Fenced blocks are dropped only when they look like an environment dump;
    a code block showing the failing call IS signal and is kept.
    """
    text = _FENCE.sub(lambda m: "" if _ENV_HINT.search(m.group(0)) else m.group(0), text)
    kept, dropping = [], False
    for line in text.splitlines():
        if _DROP_SECTION.match(line):
            dropping = True
            continue
        # A new header ends the dropped section.
        if dropping and re.match(r"\s*#{1,4}\s+\S|^\s*\*\*[A-Z]", line):
            dropping = False
        if dropping:
            continue
        if _ENV_HINT.search(line):
            continue
        kept.append(line)
    out = _MD_NOISE.sub(" ", "\n".join(kept))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", out)).strip()


def patch_signature(patch: str, files: list[str]) -> str:
    """
    A patch reduced to WHERE it landed: paths, path components, and the
    identifiers named in hunk context lines. No diff body -- storing the
    fix itself is what "index, don't duplicate" rules out.
    """
    parts: list[str] = list(files)
    for f in files:
        parts.extend(p for p in re.split(r"[/_.\-]", f) if len(p) > 2)
    syms: list[str] = []
    for ctx in HUNK_RE.findall(patch):
        syms.extend(SYMBOL_RE.findall(ctx))
    parts.extend(dict.fromkeys(syms))
    return " ".join(dict.fromkeys(parts))[:2000]


def load() -> list[dict]:
    import pandas as pd

    p = glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/datasets--ScaleAI--SWE-bench_Pro/snapshots/*/data/*.parquet"))[0]
    df = pd.read_parquet(p, columns=["repo", "instance_id", "patch", "problem_statement",
                                     "interface", "requirements"])
    out = []
    for i, r in df.iterrows():
        patch = str(r["patch"])
        files = sorted({m.group(2) for m in FILE_RE.finditer(patch)})
        if not files:
            continue
        raw = str(r["problem_statement"])
        iface = "" if r["interface"] is None else str(r["interface"])
        out.append({
            "idx": int(i), "repo": str(r["repo"]), "files": files,
            "raw": raw[:2000],
            "stripped": strip_metadata(raw)[:2000],
            "interface": iface[:2000],
            "signature": patch_signature(patch, files),
        })
    return out


def score(sel: list[dict], gold: set[str]) -> dict:
    got: set[str] = set()
    for s in sel:
        got |= set(s["files"])
    inter = got & gold
    return {"hit": bool(inter),
            "recall": len(inter) / len(gold) if gold else 0.0,
            "prec": len(inter) / len(got) if got else 0.0}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    inst = load()
    N = len(inst)
    have_iface = sum(1 for i in inst if i["interface"].strip())
    print(f"{N} instances | interface present: {have_iface} "
          f"| mean raw {statistics.mean(len(i['raw']) for i in inst):.0f} chars "
          f"-> stripped {statistics.mean(len(i['stripped']) for i in inst):.0f} chars")
    print(f"stripping removed "
          f"{100 * (1 - statistics.mean(len(i['stripped']) for i in inst) / statistics.mean(len(i['raw']) for i in inst)):.1f}% of text")

    # Document representations. Query side is restricted to the problem
    # statement -- see the leakage boundary in the module docstring.
    DOCS = {
        "D1_raw_problem": lambda d: d["raw"],
        "D2_stripped_problem": lambda d: d["stripped"],
        "D3_interface": lambda d: d["interface"] or d["stripped"],
        "D4_patch_signature": lambda d: d["signature"],
        "D5_stripped_plus_interface": lambda d: (d["stripped"] + "\n" + d["interface"])[:2500],
        "D6_stripped_plus_signature": lambda d: (d["stripped"] + "\n" + d["signature"])[:2500],
    }
    QUERIES = {"Q1_raw": lambda d: d["raw"], "Q2_stripped": lambda d: d["stripped"]}

    emb = LocalEmbedder()
    print("\nembedding all representations (local, cached) ...")
    cache: dict[str, np.ndarray] = {}
    for name, fn in {**{f"DOC::{k}": v for k, v in DOCS.items()},
                     **{f"Q::{k}": v for k, v in QUERIES.items()}}.items():
        texts = [fn(d) or " " for d in inst]
        cache[name] = np.vstack(await emb.embed(texts, input_type="document"))
        print(f"  {name}")
    print(f"  {emb.stats()}")

    results: dict[str, dict] = {}
    hits: dict[str, list[bool]] = {}

    for qname in QUERIES:
        qmat = cache[f"Q::{qname}"]
        for dname, fn in DOCS.items():
            dmat = cache[f"DOC::{dname}"]
            bm = BM25([fn(d) or " " for d in inst])
            rows_d, rows_f = [], []
            for qi, cur in enumerate(inst):
                gold = set(cur["files"])
                mask = np.ones(N, dtype=bool)
                mask[qi] = False
                cand = np.flatnonzero(mask)

                d_s = cosine_scores(qmat[qi], dmat[cand])
                d_o = list(np.argsort(-d_s))
                rows_d.append(score([inst[cand[i]] for i in d_o[:args.k]], gold))

                b_o = list(np.argsort(-bm.scores(QUERIES[qname](cur))[cand]))
                f_o = list(np.argsort(-rrf([d_o, b_o], len(cand))))
                rows_f.append(score([inst[cand[i]] for i in f_o[:args.k]], gold))

            for tag, rows in (("dense", rows_d), ("rrf", rows_f)):
                key = f"{qname} | {dname} | {tag}"
                results[key] = {
                    "hit@k": round(sum(r["hit"] for r in rows) / len(rows), 4),
                    "file_recall": round(statistics.mean(r["recall"] for r in rows), 4),
                    "file_precision": round(statistics.mean(r["prec"] for r in rows), 4),
                }
                hits[key] = [r["hit"] for r in rows]
            print(f"  done {qname} x {dname}")

    base = "Q1_raw | D1_raw_problem | dense"
    for k in list(results):
        if k == base:
            continue
        x = sum(1 for a, b in zip(hits[k], hits[base]) if a and not b)
        y = sum(1 for a, b in zip(hits[k], hits[base]) if b and not a)
        results[k]["p_vs_baseline"] = (
            round(float(binomtest(x, x + y, 0.5).pvalue), 8) if x + y else 1.0)
        results[k]["net_wins"] = x - y

    print(f"\n=== hit@{args.k}, leave-one-out over {N} instances ===")
    print(f"{'query | document | fusion':<52}{'hit@k':>8}{'recall':>9}{'prec':>8}{'p':>11}")
    for k, v in sorted(results.items(), key=lambda kv: -kv[1]["hit@k"]):
        p = v.get("p_vs_baseline")
        print(f"{k:<52}{v['hit@k']:>8.3f}{v['file_recall']:>9.3f}{v['file_precision']:>8.3f}"
              f"{(f'{p:.6f}' if p is not None else '-'):>11}")

    Path(__file__).parent.joinpath("results_exp7b_representations.json").write_text(
        json.dumps({"k": args.k, "n": N, "results": results}, indent=2), encoding="utf-8")
    print("\nwrote results_exp7b_representations.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

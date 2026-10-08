"""Build the memory block of every knowledge arm, once, before any held-out run (PREREGISTRATION.md section 4).

    .venv/Scripts/python build_notes.py [--part test|calibration|all] [--workers 4]

Writes runs/notes_L1.json, notes_L2.json, notes_L3.json ({instance_id: {"text": ...}} -- the format
experiments/swebench/generate.py reads), runs/notes_meta.json (what went into each block: commit shas, Goal ids,
scores, sizes) and runs/kel_frozen.json (generate.py refuses held-out arms without it).

Tiers are cumulative: L1 = local; L2 = L1 + enterprise; L3 = L2 + global.
* local       history.local_context(): claims + this repo's past fixes from git ancestors of base_commit.
* enterprise  corpus Goals of the same GitHub org (this repo included), created STRICTLY before the task.
* global      corpus Goals of every other org, created strictly before the task.
Goals already shown by the local library (same repo, same PR number) are dropped from the higher tiers; undated
Goals are never shown (they cannot be ordered against the task). Retrieval for both global tiers is bge-small
cosine (the model find_ways embeds with) over the frozen corpus export; providers.global = "find_ways" is the
seam for the real server tool.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

import history
from common import (CACHE, CONFIG, NOTE_ARMS, RUNS, clip, corpus_dir, org_of, parquet_path, pr_number, read_json,
                    repo_of, stable_hash, write_json)

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_lock = threading.Lock()


# ---------------------------------------------------------------- repo mirrors (same layout as generate.py)
def mirror(repo: str) -> Path:
    path = CACHE / repo.replace("/", "__")
    with _lock:
        if not path.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            history.git(CACHE, "clone", "--filter=blob:none", "--no-checkout", f"https://github.com/{repo}.git",
                        str(path))
    return path


# ---------------------------------------------------------------- corpus
class Corpus:
    def __init__(self, dates: dict[str, str]):
        d = corpus_dir()
        self.rows = [json.loads(l) for l in open(d / CONFIG["corpus"]["jsonl"], encoding="utf-8") if l.strip()]
        self.X = np.load(d / CONFIG["corpus"]["embeddings"]).astype(np.float32)
        if len(self.rows) != len(self.X):
            raise SystemExit(f"corpus rows ({len(self.rows)}) and embeddings ({len(self.X)}) differ")
        self.dates = np.array([dates.get(r.get("row_ref") or "", "") for r in self.rows])
        self.repo = [repo_of(r.get("row_ref")) for r in self.rows]
        self.org = np.array([org_of(x) or "" for x in self.repo])
        self.patches: dict[str, str] = {}

    def candidates(self, task: dict, tier: str) -> np.ndarray:
        """Boolean mask of Goals a tier may show for this task (dated, strictly earlier, not the task itself)."""
        dated = (self.dates != "") & (self.dates < task["created_at"])
        not_self = np.array([r.get("row_ref") != task["instance_id"] for r in self.rows])
        same_org = self.org == org_of(task["repo"].lower())
        return dated & not_self & (same_org if tier == "enterprise" else ~same_org)

    def load_patches(self, refs: set[str]) -> None:
        missing = refs - set(self.patches)
        if not missing:
            return
        for ds in CONFIG["history_datasets"]:
            for f in ds["files"]:
                t = pq.read_table(parquet_path(ds["name"], f), columns=["instance_id", "patch"])
                ids = t.column("instance_id").to_pylist()
                pat = t.column("patch")
                for k, iid in enumerate(ids):
                    if iid in missing and iid not in self.patches:
                        self.patches[iid] = pat[k].as_py() or ""


def embed_queries(texts: list[str]) -> np.ndarray:
    from fastembed import TextEmbedding

    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    return np.array(list(model.embed([QUERY_PREFIX + t for t in texts], batch_size=16)), dtype=np.float32)


def check_embedding_compat(corpus: Corpus) -> float:
    """The stored corpus vectors were made with sentence-transformers; queries use fastembed (same weights, ONNX).
    Re-embed a few corpus docs and require cosine >= 0.99 with the stored vectors."""
    from fastembed import TextEmbedding

    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    idx = [0, len(corpus.rows) // 2, len(corpus.rows) - 1]
    docs = [f"{corpus.rows[i]['name']}\n{corpus.rows[i]['desc']}" for i in idx]
    v = np.array(list(model.embed(docs)), dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    sims = [float(v[k] @ corpus.X[i]) for k, i in enumerate(idx)]
    if min(sims) < 0.99:
        raise SystemExit(f"query and corpus embeddings are not compatible (cos {sims}); re-embed the corpus")
    return min(sims)


# ---------------------------------------------------------------- formatting
def fmt_local(ctx: history.LocalContext, cap: int) -> str:
    out = ["## This repository's own knowledge (.stealth, local)"]
    if ctx.claims:
        out.append("### Facts (claims.md)")
        out += [f"- {c}" for c in ctx.claims]
    if ctx.library:
        out.append("### Past fixes in this repository (library.md, from git history before this commit)")
        for e in ctx.library:
            files = ", ".join(e.files[:8]) + (" ..." if len(e.files) > 8 else "")
            block = [f"#### {e.subject}", f"commit {e.sha[:10]}; files: {files}"]
            if e.body:
                block.append(clip(e.body, 400))
            if e.diff:
                block.append("```diff\n" + e.diff.rstrip() + "\n```")
            out.append("\n".join(block))
    return clip("\n".join(out), cap) if len(out) > 1 else ""


def fmt_goals(title: str, goals: list[dict], cap: int) -> str:
    if not goals:
        return ""
    out = [title]
    for g in goals:
        block = [f"#### Goal: {g['name']}", f"Way: {g.get('proc') or '-'}"]
        block += [f"- step: {s}" for s in (g.get("steps") or [])[:8] if s]
        block += [f"- pitfall: {p}" for p in (g.get("pitfalls") or [])[:4] if p]
        if g.get("diff"):
            block.append("Real diff of that fix:\n```diff\n" + g["diff"].rstrip() + "\n```")
        candidate = "\n".join(out + ["\n".join(block)])
        if len(candidate) > cap and len(out) > 1:
            break
        out.append("\n".join(block))
    return clip("\n".join(out), cap)


def local_pr_numbers(ctx: history.LocalContext) -> set[int]:
    nums = set()
    for e in ctx.library:
        nums |= {int(n) for n in re.findall(r"#(\d+)", f"{e.subject}\n{e.body}")}
    return nums


def pick_goals(corpus: Corpus, q: np.ndarray, task: dict, tier: str, skip_prs: set[int], mem: dict) -> list[dict]:
    mask = corpus.candidates(task, tier)
    if skip_prs:
        same_repo = np.array([x == task["repo"].lower() for x in corpus.repo])
        dup = np.array([pr_number(r.get("row_ref")) in skip_prs for r in corpus.rows])
        mask &= ~(same_repo & dup)
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    s = corpus.X[idx] @ q
    order = idx[np.argsort(-s, kind="stable")][: mem["global_entries"]]
    picked = []
    for rank, i in enumerate(order):
        r = corpus.rows[i]
        picked.append({"gid": r["gid"], "row_ref": r.get("row_ref"), "score": float(corpus.X[i] @ q),
                       "name": r["name"], "proc": r.get("proc"), "steps": r.get("steps"),
                       "pitfalls": r.get("pitfalls"), "with_diff": rank < mem["global_diffs"]})
    return picked


# ---------------------------------------------------------------- main
def build(ids: list[str], inst: dict, workers: int, freeze: bool = True) -> None:
    mem, prov = CONFIG["memory"], CONFIG["providers"]
    if prov["global"] != "replica":
        raise NotImplementedError("providers.global = 'find_ways' needs the real server (STEALTH_MCP_URL); "
                                  "not wired yet -- see README.md")
    corpus = Corpus(read_json(RUNS / "corpus_dates.json"))
    compat = check_embedding_compat(corpus)
    Q = embed_queries([inst[i]["problem_statement"] for i in ids])
    Q /= np.linalg.norm(Q, axis=1, keepdims=True)

    local: dict[str, history.LocalContext] = {}
    errors: dict[str, str] = {}

    def do_local(i: str) -> None:
        t = inst[i]
        try:
            m = mirror(t["repo"])
            local[i] = history.local_context(m, t["base_commit"], t["problem_statement"], mem, prov["local"])
        except Exception as exc:  # noqa: BLE001 -- recorded; the task gets an empty local tier and is flagged
            errors[i] = f"{type(exc).__name__}: {str(exc)[:300]}"
            local[i] = history.LocalContext()
        print(f"{i:<50} fixes={local[i].fix_commits:<5} lib={len(local[i].library)} "
              f"claims={len(local[i].claims)}{' ERROR' if i in errors else ''}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do_local, ids))

    picks = {}
    for k, i in enumerate(ids):
        skip = local_pr_numbers(local[i])
        picks[i] = {tier: pick_goals(corpus, Q[k], inst[i], tier, skip, mem) for tier in ("enterprise", "global")}
    corpus.load_patches({g["row_ref"] for p in picks.values() for gs in p.values() for g in gs
                         if g["with_diff"] and g["row_ref"]})

    notes = {arm: {} for arm in NOTE_ARMS}
    meta = {}
    for i in ids:
        for gs in picks[i].values():
            for g in gs:
                g["diff"] = clip(corpus.patches.get(g["row_ref"] or "", ""), mem["diff_max_chars"]) if g["with_diff"] else ""
        cap = mem["tier_max_chars"]
        t_local = fmt_local(local[i], cap)
        t_ent = fmt_goals("## Knowledge from your organisation (Kel enterprise tier: earlier fixes in this org's "
                          "repositories)", picks[i]["enterprise"], cap)
        t_glob = fmt_goals("## Knowledge from Kel (global tier: earlier fixes in other open-source repositories)",
                           picks[i]["global"], cap)
        tiers = {"L1": [t_local], "L2": [t_local, t_ent], "L3": [t_local, t_ent, t_glob]}
        for arm, parts in tiers.items():
            notes[arm][i] = {"text": "\n\n".join(p for p in parts if p)}
        meta[i] = {
            "local": {"scanned_commits": local[i].scanned_commits, "fix_commits": local[i].fix_commits,
                      "claims": len(local[i].claims),
                      "library": [{"sha": e.sha, "subject": e.subject, "score": round(e.score, 3),
                                   "diff_chars": len(e.diff)} for e in local[i].library],
                      "error": errors.get(i)},
            **{tier: [{k: g[k] for k in ("gid", "row_ref", "score")} | {"diff_chars": len(g["diff"])}
                      for g in picks[i][tier]] for tier in ("enterprise", "global")},
            "chars": {arm: len(notes[arm][i]["text"]) for arm in NOTE_ARMS},
        }
    for arm in NOTE_ARMS:
        prev = RUNS / f"notes_{arm}.json"
        merged = {**(read_json(prev) if prev.exists() else {}), **notes[arm]}
        write_json(prev, merged)
    prev_meta = RUNS / "notes_meta.json"
    write_json(prev_meta, {**(read_json(prev_meta) if prev_meta.exists() else {}), **meta})
    prereg = Path(__file__).parent / "PREREGISTRATION.md"
    if freeze and not prereg.exists():
        raise SystemExit("PREREGISTRATION.md is missing: the plan is frozen before any notes are built")
    frozen = {"config_sha256": stable_hash((Path(__file__).parent / "experiment.json").read_text(encoding="utf-8")),
              # LF-normalised: a Windows autocrlf checkout must hash the same plan to the same value
              "preregistration_sha256": (stable_hash(prereg.read_text(encoding="utf-8").replace("\r\n", "\n"))
                                         if prereg.exists() else None),
              "providers": prov, "memory": mem, "embedding_compat_min_cos": round(compat, 4),
              "notes_sha256": {arm: stable_hash(json.dumps(read_json(RUNS / f"notes_{arm}.json"), sort_keys=True))
                               for arm in NOTE_ARMS},
              "local_errors": errors}
    if freeze:   # a --limit smoke test never freezes Kel (generate.py would then accept held-out arms)
        write_json(RUNS / "kel_frozen.json", frozen)
    print(json.dumps({"tasks": len(ids), "local_errors": len(errors),
                      "empty_local": sum(1 for i in ids if not notes["L1"][i]["text"]),
                      "mean_chars": {a: round(np.mean([meta[i]["chars"][a] for i in ids])) for a in NOTE_ARMS}},
                     indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["test", "calibration", "all"], default="all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="first N ids only (smoke test)")
    a = ap.parse_args()
    design, inst = read_json(RUNS / "design.json"), read_json(RUNS / "instances.json")
    ids = design["test"] + design["calibration"] if a.part == "all" else design[a.part]
    build(ids[: a.limit] if a.limit else ids, inst, a.workers, freeze=not a.limit)


if __name__ == "__main__":
    main()

"""Local Kel: embed the corpus Goals and the unseen issues with bge-small, keep the top 3 Goals (+ their Procedure) per issue."""
import json
import os
import re
import sys

import numpy as np
from sentence_transformers import SentenceTransformer

D = sys.argv[1]
corpus = [json.loads(l) for l in open(f"{D}/corpus.jsonl", encoding="utf-8")]
queries = json.load(open(f"{D}/queries.json", encoding="utf-8"))
model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")
cache = f"{D}/corpus_bge_small.npy"
if os.path.exists(cache):
    X = np.load(cache)
else:
    X = model.encode([f"{c['name']}\n{c['desc']}" for c in corpus], batch_size=64, normalize_embeddings=True,
                     show_progress_bar=False).astype(np.float32)
    np.save(cache, X)
Q = model.encode(["Represent this sentence for searching relevant passages: " + q["issue"] for q in queries],
                 batch_size=16, normalize_embeddings=True, show_progress_bar=False).astype(np.float32)
S = Q @ X.T


def repo_of(row_ref):
    m = re.match(r"(.+?)__(.+)-\d+$", row_ref or "")
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


out = []
same_repo = 0
for i, q in enumerate(queries):
    top = np.argsort(-S[i])[:3]
    hits = []
    for j in top:
        c = corpus[j]
        hits.append({"score": float(S[i, j]), "goal": c["name"], "desc": c["desc"][:600], "procedure": c.get("proc"),
                     "steps": c.get("steps", [])[:8], "pitfalls": c.get("pitfalls", [])[:4],
                     "same_repo": repo_of(c.get("row_ref")) == q["repo"].lower()})
    same_repo += any(h["same_repo"] for h in hits)
    out.append({**{k: q[k] for k in ("id", "source", "repo", "language")}, "issue": q["issue"], "patch": q["patch"], "kel": hits})
json.dump(out, open(f"{D}/retrieved.json", "w", encoding="utf-8"), indent=1)
print(f"queries {len(out)} | a top-3 Goal from the SAME repository: {same_repo} ({same_repo / len(out):.0%})", flush=True)

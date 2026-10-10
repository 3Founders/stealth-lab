"""Repo-aware retrieval: for unseen tasks whose repository has history in Kel, the top 3 Goals from THAT repository only."""
import json
import re
import sys

import numpy as np
from sentence_transformers import SentenceTransformer

D = sys.argv[1]
corpus = [json.loads(l) for l in open(f"{D}/corpus.jsonl", encoding="utf-8")]
items = json.load(open(f"{D}/retrieved.json", encoding="utf-8"))
X = np.load(f"{D}/corpus_bge_small.npy")


def repo_of(ref):
    m = re.match(r"(.+?)__(.+)-\d+$", ref or "")
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


repo_idx = {}
for i, c in enumerate(corpus):
    repo_idx.setdefault(repo_of(c.get("row_ref")), []).append(i)
items = [it for it in items if it["repo"].lower() in repo_idx]
model = SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")
Q = model.encode(["Represent this sentence for searching relevant passages: " + it["issue"] for it in items],
                 batch_size=16, normalize_embeddings=True, show_progress_bar=False).astype(np.float32)
out = []
for q, it in zip(Q, items):
    cand = repo_idx[it["repo"].lower()]
    s = X[cand] @ q
    hits = []
    for k in np.argsort(-s)[:3]:
        c = corpus[cand[k]]
        hits.append({"score": float(s[k]), "goal": c["name"], "desc": c["desc"][:600], "procedure": c.get("proc"),
                     "steps": c.get("steps", [])[:8], "pitfalls": c.get("pitfalls", [])[:4], "history": len(cand), "row_ref": c.get("row_ref"), "src": c.get("source")})
    out.append({**{k: it[k] for k in ("id", "source", "repo", "language", "issue", "patch")}, "kel": hits})
json.dump(out, open(f"{D}/retrieved_repo_refs.json", "w", encoding="utf-8"), indent=1)
print("repo-scoped tasks:", len(out), flush=True)

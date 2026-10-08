"""Semantic codes for Ways (procedures): a two-level code tree over their embeddings (the GenRec idea).

A Way's embedding is mapped to a short code such as `c07.3`: `c07` is its coarse group (nearest of K1 top
centroids), `.3` its sub-group inside that (nearest of K2 centroids learned on that group's members). Ways with
the same code do similar work, so in the routing model the code nodes are PARENTS in the Goal hierarchy: a Way
nobody has run yet borrows what is known about its code, and a rare code borrows from its coarse group
(service.prior_draws_for_case, fit via evidence goals' parents).

The codebook is built from Way embeddings (scripts/build_prior_bundle.py), versioned, and shipped with the
server like the model prior. Codes stay fixed within a version; a new version maps every old code to the nearest
new one (`remap`), so outcomes recorded under old codes carry over. Assignment needs the embedding model the
codebook was built with (`embedding_model_id`); a different model gives no code rather than a wrong one.

numpy only.
"""
from __future__ import annotations

import functools
import io
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CODEBOOK = "codebook.npz"
CODE_RE = r"^c[0-9]{2}\.[0-9]+$"


@dataclass
class Codebook:
    version: str
    embedding_model_id: str
    top: np.ndarray              # (K1, d) unit vectors
    sub: np.ndarray              # (K1, K2, d) unit vectors (rows of zeros where a group had fewer members)
    sub_count: np.ndarray        # (K1,) how many sub-centroids each group really has
    meta: dict

    @property
    def dim(self) -> int:
        return int(self.top.shape[1])

    def assign(self, vector: Sequence[float]) -> Optional[str]:
        v = _unit(np.asarray(vector, dtype=np.float64)[None, :])
        if v.shape[1] != self.dim or not np.isfinite(v).all():
            return None
        a = int(np.argmax(self.top @ v[0]))
        k = int(self.sub_count[a])
        b = int(np.argmax(self.sub[a, :k] @ v[0])) if k > 0 else 0
        return code_name(a, b)

    def assign_many(self, vectors: np.ndarray) -> list[str]:
        x = _unit(np.asarray(vectors, dtype=np.float64))
        a = np.argmax(x @ self.top.T, axis=1)
        out = []
        for i, ai in enumerate(a):
            k = int(self.sub_count[ai])
            b = int(np.argmax(self.sub[ai, :k] @ x[i])) if k > 0 else 0
            out.append(code_name(int(ai), b))
        return out

    def centroid(self, code: str) -> Optional[np.ndarray]:
        parsed = parse_code(code)
        if parsed is None:
            return None
        a, b = parsed
        if a >= self.top.shape[0] or b >= max(int(self.sub_count[a]), 1):
            return None
        return self.sub[a, b] if int(self.sub_count[a]) > 0 else self.top[a]

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, top=self.top.astype(np.float32), sub=self.sub.astype(np.float32),
                            sub_count=self.sub_count.astype(np.int32),
                            meta=np.array(json.dumps({**self.meta, "version": self.version,
                                                      "embedding_model_id": self.embedding_model_id})))
        return buf.getvalue()

    @staticmethod
    def from_bytes(blob: bytes) -> "Codebook":
        z = np.load(io.BytesIO(blob), allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        return Codebook(version=meta["version"], embedding_model_id=meta["embedding_model_id"],
                        top=z["top"].astype(np.float64), sub=z["sub"].astype(np.float64),
                        sub_count=z["sub_count"].astype(np.int64), meta=meta)


def code_name(a: int, b: int) -> str:
    return f"c{a:02d}.{b}"


def parse_code(code: str) -> Optional[tuple[int, int]]:
    import re

    if not isinstance(code, str) or not re.match(CODE_RE, code):
        return None
    top, sub = code[1:].split(".")
    return int(top), int(sub)


def coarse(code: str) -> str:
    return code.split(".")[0]


def node_id(code: str, version: str) -> str:
    """The routing-hierarchy node of a code (or of its coarse group `cNN`), stable per codebook version."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"stealthlab:routing:code:{version}:{code}"))


def _unit(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.where(n > 0, n, 1.0)


def _kmeans(x: np.ndarray, k: int, rng: np.random.Generator, iters: int = 30) -> np.ndarray:
    """Spherical k-means (cosine) with k-means++ seeding. x: unit rows. Returns (k', d) unit centroids, k' <= k."""
    n = x.shape[0]
    k = min(k, n)
    if k <= 0:
        return np.zeros((0, x.shape[1]))
    first = int(rng.integers(n))
    cents = [x[first]]
    d2 = 1.0 - x @ x[first]
    for _ in range(1, k):
        p = np.clip(d2, 0, None)
        p = p / p.sum() if p.sum() > 0 else np.full(n, 1.0 / n)
        c = int(rng.choice(n, p=p))
        cents.append(x[c])
        d2 = np.minimum(d2, 1.0 - x @ x[c])
    c = np.stack(cents)
    for _ in range(iters):
        lab = np.argmax(x @ c.T, axis=1)
        new = np.stack([x[lab == j].sum(axis=0) if (lab == j).any() else c[j] for j in range(c.shape[0])])
        new = _unit(new)
        if np.allclose(new, c, atol=1e-6):
            break
        c = new
    return c


def build(vectors: np.ndarray, *, k1: int = 24, k2: int = 8, min_group: int = 20, version: str,
          embedding_model_id: str, seed: int = 0) -> Codebook:
    """Two-level spherical k-means. A coarse group with fewer than `min_group` members gets one sub-code."""
    rng = np.random.default_rng(seed)
    x = _unit(np.asarray(vectors, dtype=np.float64))
    top = _kmeans(x, k1, rng)
    lab = np.argmax(x @ top.T, axis=1)
    d = x.shape[1]
    sub = np.zeros((top.shape[0], k2, d))
    sub_count = np.zeros(top.shape[0], dtype=np.int64)
    sizes = []
    for a in range(top.shape[0]):
        members = x[lab == a]
        sizes.append(int(members.shape[0]))
        if members.shape[0] < min_group:
            sub[a, 0] = top[a]
            sub_count[a] = 1
            continue
        c = _kmeans(members, k2, rng)
        sub[a, :c.shape[0]] = c
        sub_count[a] = c.shape[0]
    return Codebook(version=version, embedding_model_id=embedding_model_id, top=top, sub=sub, sub_count=sub_count,
                    meta={"n": int(x.shape[0]), "k1": int(top.shape[0]), "k2": int(k2), "group_sizes": sizes})


def remap(old: Codebook, new: Codebook) -> dict[str, str]:
    """{old code: nearest new code} (by centroid), so outcomes recorded under an older version carry over."""
    out = {}
    for a in range(old.top.shape[0]):
        for b in range(max(int(old.sub_count[a]), 1)):
            code = code_name(a, b)
            c = old.centroid(code)
            if c is not None:
                out[code] = new.assign(c) or ""
    return out


@functools.lru_cache(maxsize=2)
def load(data_dir: str = DATA_DIR) -> Optional[Codebook]:
    path = os.path.join(data_dir, CODEBOOK)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return Codebook.from_bytes(fh.read())


def code_parents(code: Optional[str], version: str) -> list[str]:
    """Hierarchy node ids for a code: its own node (whose parent is the coarse group's node in the fit)."""
    return [node_id(code, version)] if code and parse_code(code) else []


def describe(cb: Codebook) -> Mapping[str, Any]:
    return {"version": cb.version, "embedding_model_id": cb.embedding_model_id, "codes": int(sum(
        max(int(k), 1) for k in cb.sub_count)), "coarse_groups": int(cb.top.shape[0]), "built_from": cb.meta.get("n")}

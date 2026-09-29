"""Near-duplicate detection for text items (SKILL.md files), persisted across runs.

Exact duplicates are caught by content hash (the ledger identity). Near duplicates -- the same skill copied into
another repository with a changed name line or whitespace -- are caught here:

  text  -> normalised (lowercase, whitespace collapsed) -> 5-word shingles -> MinHash signature (128 values)
        -> 32 bands of 4 values -> band keys stored in `ingest_near_dup_bands` for every WRITTEN item.

A new item's band keys are looked up; each candidate that shares a band is compared by the estimated Jaccard
similarity of the two signatures (the fraction of equal MinHash values). At or above THRESHOLD it is rejected as
a near duplicate of that item. With 32 bands of 4, a pair at 0.9 similarity shares at least one band with
probability > 0.999; a pair at 0.5 only ~0.87 of the time, and is then rejected only if the estimate says >= 0.9.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

import numpy as np

NUM_PERM = 128
BANDS = 32
ROWS = NUM_PERM // BANDS
SHINGLE_WORDS = 5
THRESHOLD = 0.9
_PRIME = (1 << 61) - 1
_rng = np.random.default_rng(20260929)
_A = _rng.integers(1, _PRIME, size=NUM_PERM, dtype=np.uint64)
_B = _rng.integers(0, _PRIME, size=NUM_PERM, dtype=np.uint64)


def _shingle_hashes(text: str) -> np.ndarray:
    words = re.sub(r"\s+", " ", text.lower()).strip().split(" ")
    if len(words) < SHINGLE_WORDS:
        grams = [" ".join(words)]
    else:
        grams = [" ".join(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)]
    return np.array(sorted({int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=7).digest(), "big")
                            for g in grams}), dtype=np.uint64)


def signature(text: str) -> list[int]:
    h = _shingle_hashes(text)
    # (a*h + b) mod p, computed in Python ints per permutation to avoid uint64 overflow
    out = []
    hs = [int(x) for x in h]
    for a, b in zip(_A.tolist(), _B.tolist()):
        out.append(min((a * x + b) % _PRIME for x in hs))
    return out


def band_keys(sig: list[int]) -> list[str]:
    return [f"{band}:{hashlib.blake2b(repr(sig[band * ROWS:(band + 1) * ROWS]).encode(), digest_size=8).hexdigest()}"
            for band in range(BANDS)]


def similarity(a: list[int], b: list[int]) -> float:
    return sum(1 for x, y in zip(a, b) if x == y) / NUM_PERM


async def nearest_written(pool: Any, pipeline: str, sig: list[int]) -> Optional[tuple[str, float]]:
    """(item_key, similarity) of the most similar WRITTEN item at or above THRESHOLD, or None."""
    keys = band_keys(sig)
    rows = await pool.fetch(
        "SELECT DISTINCT b.item_key, l.detail->'minhash' AS sig FROM ingest_near_dup_bands b "
        "JOIN ingest_ledger l ON l.pipeline = b.pipeline AND l.item_key = b.item_key AND l.status = 'written' "
        "WHERE b.pipeline = $1 AND b.band_key = ANY($2::text[])", pipeline, keys)
    best: Optional[tuple[str, float]] = None
    for r in rows:
        other = r["sig"]
        if isinstance(other, str):
            import json

            other = json.loads(other)
        if not other:
            continue
        s = similarity(sig, [int(x) for x in other])
        if s >= THRESHOLD and (best is None or s > best[1]):
            best = (r["item_key"], s)
    return best


async def remember(pool: Any, pipeline: str, item_key: str, sig: list[int]) -> None:
    await pool.executemany(
        "INSERT INTO ingest_near_dup_bands (pipeline, band_key, item_key) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        [(pipeline, k, item_key) for k in band_keys(sig)])

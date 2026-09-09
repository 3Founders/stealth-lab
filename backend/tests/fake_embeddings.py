"""
Deterministic, offline stand-in for the ONE real network-reaching seam on
`Embedder` (app/services/embeddings.py): `Embedder._embed_configured_provider`.

Root cause this exists to fix: three tests in test_local_agent_runner_offline.py
instantiated the real `Embedder` (directly, or indirectly via the runner's
own query/capture embedding calls) with no mock, so they made live Gemini/
Voyage HTTP calls -- failing (or, worse, silently succeeding and billing)
depending on whatever credentials happened to be configured in the
environment running the "offline" suite.

Why patch `_embed_configured_provider` and not `embed`/`embed_one`: `embed()` also
contains real, non-network production logic this suite should keep
exercising -- the cross-call cache and the input_type/task_type wiring.
`_embed_configured_provider` is the exact point where that logic hands off to a real
provider (`_embed_gemini` / `_embed_voyage` / local model), so patching it
removes only the network dependency and leaves everything else real.

Why not a plain hash-of-text fake: this suite's semantic-retrieval test
(test_runner_finds_a_local_procedure_via_semantic_similarity_not_lexical_
overlap) exists specifically to prove real cosine-similarity ranking finds
a procedure whose wording shares ZERO words with the query. A fake that
hashes raw text produces uncorrelated noise for any two distinct strings,
semantically related or not -- indistinguishable, from that test's
perspective, from a broken embedder that returns garbage. Instead each
text is decomposed into a small set of named concepts via a synonym table;
each concept gets one fixed pseudo-random unit vector (seeded off its own
name, stable for the whole process), and a text's vector is the normalized
sum of its matched concepts' vectors. Two sentences sharing concepts land
close in cosine space with no literal word in common; sentences sharing no
concepts land near-orthogonal. Extend `_SYNONYMS` if a future offline test
needs another semantically-related-but-lexically-disjoint pair -- the
mechanism itself is generic, not specific to any one test's wording.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from collections.abc import Sequence

_SYNONYMS: dict[str, str] = {
    "login": "auth", "authentication": "auth", "credentials": "auth", "auth": "auth",
    "session": "auth_session", "token": "auth_session",
    "expired": "auth_session_expiry", "timed": "auth_session_expiry", "out": "auth_session_expiry",
    "fail": "negative_outcome", "failing": "negative_outcome", "failed": "negative_outcome",
    "broke": "negative_outcome", "broken": "negative_outcome",
    "resolve": "remediation", "fix": "remediation",
    "attempt": "user_action", "users": "user_action", "user": "user_action",
}

_WORD_RE = re.compile(r"[a-z0-9]+")
_CONCEPT_VECTOR_CACHE: dict[tuple[str, int], list[float]] = {}


def _concept_vector(concept: str, dimension: int) -> list[float]:
    cache_key = (concept, dimension)
    cached = _CONCEPT_VECTOR_CACHE.get(cache_key)
    if cached is not None:
        return cached
    seed = int.from_bytes(hashlib.sha256(concept.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    vec = [rng.uniform(-1.0, 1.0) for _ in range(dimension)]
    _CONCEPT_VECTOR_CACHE[cache_key] = vec
    return vec


def fake_embed_text(text: str, dimension: int) -> list[float]:
    """Deterministic function of (text, dimension) -- same input always
    yields the same vector, no randomness across process runs (the PRNG
    is reseeded per concept from a stable hash, never from wall-clock)."""
    words = _WORD_RE.findall(text.lower())
    concepts = {_SYNONYMS[w] for w in words if w in _SYNONYMS}
    if not concepts:
        # Nothing recognized: key a fake concept off the whole text so
        # arbitrary unrelated strings still get a stable, mutually
        # near-orthogonal vector instead of colliding at the zero vector.
        concepts = {f"text:{text}"}

    vec = [0.0] * dimension
    for concept in concepts:
        cv = _concept_vector(concept, dimension)
        for i in range(dimension):
            vec[i] += cv[i]

    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def install_fake_embedder(monkeypatch) -> None:
    """Patch `Embedder._embed_configured_provider` for the duration of one test so
    no code path reachable from it -- caching, batching, `embed_one`, the
    local runner's own query/capture embedding calls -- can reach a real
    provider. Call once per test that exercises anything which embeds
    text; safe to call from multiple tests since it always monkeypatches
    the class method fresh (pytest's `monkeypatch` fixture undoes it after
    each test)."""
    import app.services.embeddings as embeddings_module

    async def _fake_embed_configured_provider(
        self, texts: Sequence[str], input_type: str
    ) -> list[list[float]]:
        return [fake_embed_text(t, self.dimension) for t in texts]

    monkeypatch.setattr(embeddings_module.Embedder, "_embed_configured_provider", _fake_embed_configured_provider)

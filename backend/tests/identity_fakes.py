"""Deterministic stand-ins for the two model seams (embedder, semantic judge)
used by the identity / retrieval / ingestion suites. No network, no billing.

* ``ConceptEmbedder`` -- words map to named concepts; each concept owns one
  fixed pseudo-random unit vector; a text is the normalised sum of its
  concepts. Texts sharing concepts are close in cosine space with no literal
  word in common (a vector-only semantic match); texts sharing only a
  *misleading word* are not (unless that word is a concept of both).
* ``FrozenProvider`` -- a "recorded verdicts" semantic provider: an explicit
  table of (A-name, B-name) -> (relation, confidence). Anything not in the table
  is ``distinct``. Stands in for the frozen JEV/NLI fixtures the offline suite
  uses; the optional live tests use the real chain.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from typing import Any, Callable, Optional

from app.services.embeddings import EmbeddingMetadata
from app.services.semantic.chain import SemanticJudge
from app.services.semantic.policy import RetryPolicy, SemanticMetrics
from app.services.semantic.providers import ALL_CAPS, SemanticProvider

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the of to for in on at by with and or from into is are be as it its this that every all".split())
DIM = 1024

DEFAULT_CONCEPTS = {
    # call-site search
    "callers": "callsite", "caller": "callsite", "call": "callsite", "calls": "callsite", "sites": "callsite",
    "site": "callsite", "usages": "callsite", "references": "callsite", "locate": "find", "find": "find",
    "search": "find", "discover": "find",
    # deployment
    "deploy": "deploy", "release": "deploy", "ship": "deploy", "rollout": "deploy",
    # migrations
    "migrate": "migrate", "migration": "migrate", "schema": "schema", "database": "schema", "db": "schema",
    # testing
    "test": "testing", "tests": "testing", "testing": "testing", "verify": "testing",
    "flaky": "flaky", "intermittent": "flaky", "nondeterministic": "flaky",
}


def _concept_vec(name: str) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(DIM)]


_CACHE: dict[str, list[float]] = {}


def concept_embed(text: str, concepts: Optional[dict[str, str]] = None) -> list[float]:
    concepts = concepts or DEFAULT_CONCEPTS
    names = {concepts.get(w, w) for w in _WORD.findall(text.lower()) if w not in _STOP}
    vec = [0.0] * DIM
    for n in names or {f"text:{text}"}:
        cv = _CACHE.setdefault(n, _concept_vec(n))
        for i in range(DIM):
            vec[i] += cv[i]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class ConceptEmbedder:
    model = "fake-concept-1"

    def __init__(self, concepts: Optional[dict[str, str]] = None, *, fail: bool = False):
        self.concepts = concepts
        self.fail = fail
        self.calls = 0

    def embedding_model_id(self) -> str:
        return self.model

    async def embed_one(self, text: str, input_type: str = "document") -> list[float]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding provider temporarily unavailable")
        return concept_embed(text, self.concepts)

    async def embed_one_with_metadata(self, text: str, input_type: str = "document"):
        vec = await self.embed_one(text, input_type)
        return vec, EmbeddingMetadata("fake", self.model, DIM, input_type, hashlib.sha256(text.encode()).hexdigest())


def _key(text: str) -> str:
    return text.split(":")[0].strip().lower()


class FrozenProvider(SemanticProvider):
    """Recorded identity verdicts. ``verdicts[(a, b)] = (relation, confidence)``
    where a/b are lower-cased NAMES (text before the first ':')."""

    def __init__(self, verdicts: dict[tuple[str, str], tuple[str, float]], *, name: str = "frozen-jev",
                 fail: Optional[Callable[[], Optional[BaseException]]] = None,
                 default: tuple[str, float] = ("distinct", 0.95)):
        self.name, self.model = name, f"{name}-model"
        self.capabilities = frozenset(ALL_CAPS)
        self.verdicts = {(k[0].lower(), k[1].lower()): v for k, v in verdicts.items()}
        self.fail, self.default = fail, default
        self.calls: list[tuple[str, str, str]] = []

    async def identity(self, kind: str, a: str, b: str) -> dict:
        self.calls.append((kind, a, b))
        if self.fail:
            exc = self.fail()
            if exc:
                raise exc
        rel, conf = self.verdicts.get((_key(a), _key(b)), self.default)
        return {"relation": rel, "confidence": conf}


def make_judge(*providers: SemanticProvider, attempts: int = 1) -> SemanticJudge:
    async def no_sleep(_s: float) -> None:
        return None

    return SemanticJudge(list(providers), RetryPolicy(per_provider_attempts=attempts, backoff_base_s=0.0,
                                                      backoff_max_s=0.0, timeout_s=5.0),
                         SemanticMetrics(), sleep=no_sleep, rng=lambda: 0.5)


class CallbackProvider(SemanticProvider):
    """Frozen-fixture judge driven by a pure function ``fn(kind, a, b) ->
    (relation, confidence)``; records every call. ``name='jev'`` makes the
    chain report JEV as the selected provider (mode == 'jev')."""

    def __init__(self, fn, *, name: str = "jev", fail=None):
        self.name, self.model = name, f"{name}-frozen"
        self.capabilities = frozenset(ALL_CAPS)
        self.fn, self.fail = fn, fail
        self.calls: list[tuple[str, str, str]] = []

    async def identity(self, kind: str, a: str, b: str) -> dict:
        self.calls.append((kind, a, b))
        if self.fail:
            exc = self.fail(kind)
            if exc:
                raise exc
        rel, conf = self.fn(kind, a, b)
        return {"relation": rel, "confidence": conf}

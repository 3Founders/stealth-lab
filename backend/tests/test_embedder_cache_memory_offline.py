"""The cross-call embedding cache holds packed float32, not lists of Python floats.

Why: the cap comment promised ~80 MB at 20000 entries, but a 1024-dimension vector held as list[float] is ~33 KB
(pointer + float object per component), so the real worst case was ~650 MB. Packed float32 is 4 KB per vector.
Honest scope: this bounds the cache; it does not change how many vectors a caller itself keeps."""
import sys

from app.services import embeddings as em


def _clear():
    with em._EMBED_CACHE_LOCK:
        em._EMBED_CACHE.clear()
        em._EMBED_CACHE_ORDER.clear()


def test_packed_entry_is_a_fraction_of_the_list_it_replaces():
    _clear()
    vec = [0.1 * (i % 7) + 0.001 * i for i in range(1024)]
    em._cache_put("k", vec)
    packed = sys.getsizeof(em._EMBED_CACHE["k"])
    as_list = sys.getsizeof(vec) + sum(sys.getsizeof(x) for x in vec)
    assert packed < 4200 and as_list > 30000 and packed * 7 < as_list
    _clear()


def test_hit_round_trips_at_float32_precision_and_is_a_fresh_list():
    _clear()
    vec = [0.5, -0.25, 3.0, 1e-3]
    em._cache_put("k", vec)
    a, b = em._cache_get("k"), em._cache_get("k")
    assert a is not b and a == b
    assert all(abs(x - y) < 1e-6 for x, y in zip(a, vec))
    a[0] = 99.0                                       # a caller mutating its copy cannot corrupt the cache
    assert em._cache_get("k")[0] == 0.5
    assert em._cache_get("missing") is None
    _clear()


def test_fifo_eviction_stays_bounded(monkeypatch):
    _clear()
    monkeypatch.setattr(em, "_EMBED_CACHE_MAX", 3)
    for i in range(6):
        em._cache_put(f"k{i}", [float(i)])
    assert list(em._EMBED_CACHE) == ["k3", "k4", "k5"] and len(em._EMBED_CACHE_ORDER) == 3
    _clear()

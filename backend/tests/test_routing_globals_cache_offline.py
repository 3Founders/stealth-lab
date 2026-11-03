"""The routing decision path loads a fit's draws once per version, not on every request.

Before: `_globals` fetched the whole draws blob (about 10 MB compressed) from Postgres for the active fit on every
decision and used the bytes as an lru_cache key (hashing them each time, keeping four alive). Now the active lookup
returns the version only, and the blob is fetched on a cache miss. Honest scope: this covers the database-fit path;
the bundled prior has its own cache (prior_bundle.load_globals)."""
import asyncio

from app.routing import service, store

BLOB_FETCHES = []
ACTIVE_CALLS = []


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _wire(monkeypatch, version=7):
    BLOB_FETCHES.clear(); ACTIVE_CALLS.clear(); service._GLOBALS_BY_VERSION.clear()

    async def active(pool, *, with_draws=True):
        ACTIVE_CALLS.append(with_draws)
        return {"version": version, "draws": b"x" if with_draws else None, "meta": {}, "method": "m"}

    async def by_version(pool, v):
        BLOB_FETCHES.append(v)
        return {"version": v, "draws": b"blob", "meta": {"k": 1}, "method": "m"}

    monkeypatch.setattr(store, "active_params", active)
    monkeypatch.setattr(store, "params_version", by_version)
    monkeypatch.setattr(service.predict, "load_globals", lambda v, blob, meta: object())


def test_active_fit_blob_is_fetched_once_per_version(monkeypatch):
    _wire(monkeypatch)
    first = run(service._globals(None))
    for _ in range(5):
        assert run(service._globals(None)) is first
    assert BLOB_FETCHES == [7]                       # one blob fetch for six decisions
    assert set(ACTIVE_CALLS) == {False}              # the per-request lookup never asks for the draws


def test_a_new_active_version_loads_its_own_and_old_ones_are_bounded(monkeypatch):
    _wire(monkeypatch)
    for v in range(1, 9):
        async def active(pool, *, with_draws=True, _v=v):
            return {"version": _v, "draws": None, "meta": {}, "method": "m"}
        monkeypatch.setattr(store, "active_params", active)
        run(service._globals(None))
    assert BLOB_FETCHES == list(range(1, 9))
    assert len(service._GLOBALS_BY_VERSION) == service._GLOBALS_KEEP


def test_an_unknown_pinned_version_is_none_and_not_cached(monkeypatch):
    _wire(monkeypatch)

    async def missing(pool, v):
        return None
    monkeypatch.setattr(store, "params_version", missing)
    assert run(service._globals(None, 99)) is None
    assert 99 not in service._GLOBALS_BY_VERSION

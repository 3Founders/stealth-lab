"""Wiring. One place that knows how the services fit together."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.runners import default_runners
from app.services.cache import CacheStore
from app.services.decompose import DecompositionService
from app.services.embeddings import Embedder
from app.services.executor import Executor
from app.services.graph import TaskGraph
from app.services.intake import IntakeService
from app.services.matching import TaskMatcher
from app.services.router import PostgresRouterStore, Router
from app.services.traces import PostgresTraceRecorder


async def get_pool(request: Request):
    return request.app.state.pool


@dataclass
class Services:
    pool: object
    graph: TaskGraph
    matcher: TaskMatcher
    intake: IntakeService
    cache: CacheStore
    store: PostgresRouterStore
    router: Router
    executor: Executor
    decomposer: DecompositionService


def build_services(pool) -> Services:
    """
    Built per request rather than once at startup.

    These objects are thin -- they hold a pool reference and nothing else --
    and building them per request means a settings change takes effect on
    reload without a stale singleton holding the old value.
    """
    embedder = Embedder()
    graph = TaskGraph(pool, embedder=embedder)
    matcher = TaskMatcher(pool, embedder=embedder)
    cache = CacheStore(pool)
    store = PostgresRouterStore(pool, cache)
    router = Router(store)
    executor = Executor(
        router=router,
        store=store,
        runners=default_runners(),
        recorder=PostgresTraceRecorder(pool),
        cache_store=cache,
    )
    return Services(
        pool=pool,
        graph=graph,
        matcher=matcher,
        intake=IntakeService(matcher),
        cache=cache,
        store=store,
        router=router,
        executor=executor,
        decomposer=DecompositionService(matcher=matcher),
    )

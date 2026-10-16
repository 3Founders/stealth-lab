"""Set the routing row-level-security scope at every entry point (migration 150)."""
B = r"C:\Users\user\stealth-lab\backend"


def patch(path, reps):
    p = f"{B}\\{path}"
    s = open(p, encoding="utf-8").read()
    for a, b in reps:
        assert s.count(a) == 1, (path, a[:90], s.count(a))
        s = s.replace(a, b)
    open(p, "w", encoding="utf-8", newline="").write(s)


# --- requests: the caller's own scope
patch(r"app\routing\service.py", [
    ('''async def recommend(pool: Any, *, goal_id: str, candidates: Sequence[Any], access_scope: AccessScope,''',
     '''async def recommend(pool: Any, *, access_scope: AccessScope, **kwargs: Any) -> dict[str, Any]:
    """`_recommend` under the caller's routing row-level-security scope (migration 150)."""
    with store.routing_scope(access_scope):
        return await _recommend(pool, access_scope=access_scope, **kwargs)


async def _recommend(pool: Any, *, goal_id: str, candidates: Sequence[Any], access_scope: AccessScope,'''),
])

patch(r"app\routing\plan.py", [
    ('''async def load_instance(pool: Any, scope: AccessScope, instance_key: str) -> Instance:''',
     '''async def load_instance(pool: Any, scope: AccessScope, instance_key: str) -> Instance:
    """`_load_instance` under the caller's routing row-level-security scope (migration 150)."""
    with store.routing_scope(scope):
        return await _load_instance(pool, scope, instance_key)


async def _load_instance(pool: Any, scope: AccessScope, instance_key: str) -> Instance:'''),
    ('''async def report_result(pool: Any, *, scope: AccessScope, instance: Instance, accepted: bool,''',
     '''async def report_result(pool: Any, *, scope: AccessScope, **kwargs: Any) -> dict[str, Any]:
    """`_report_result` under the caller's routing row-level-security scope (migration 150)."""
    with store.routing_scope(scope):
        return await _report_result(pool, scope=scope, **kwargs)


async def _report_result(pool: Any, *, scope: AccessScope, instance: Instance, accepted: bool,'''),
])

# --- workers / maintenance: the explicit system scope
patch(r"app\routing\jobs.py", [
    ('''async def handle_local_refit(pool: Any, payload: Mapping[str, Any]) -> dict:
    from app.routing.fit import local_refit

    return await local_refit(pool, str(payload["goal_id"]))''',
     '''async def handle_local_refit(pool: Any, payload: Mapping[str, Any]) -> dict:
    from app.routing.fit import local_refit
    from app.routing.store import routing_scope

    with routing_scope(system=True):          # a worker reads a Goal's observations whoever owns them
        return await local_refit(pool, str(payload["goal_id"]))'''),
    ('''    return await nightly_refit(pool, method=payload.get("method"))''',
     '''    from app.routing.store import routing_scope

    with routing_scope(system=True):
        return await nightly_refit(pool, method=payload.get("method"))'''),
    ('''    return await model_update.run(pool)''',
     '''    from app.routing.store import routing_scope

    with routing_scope(system=True):
        return await model_update.run(pool)'''),
])

patch(r"app\routing\admin_cli.py", [
    ('''async def run(pool: Any, a: Any) -> int:''',
     '''async def run(pool: Any, a: Any) -> int:
    """Operator commands run under the routing system scope (migration 150): they read and write every owner's logs."""
    from app.routing.store import routing_scope

    with routing_scope(system=True):
        return await _run(pool, a)


async def _run(pool: Any, a: Any) -> int:'''),
    ('''            "observations": await search_group.fetchval_sum(pool, "SELECT count(*) FROM routing_observations"),
            "decisions": await search_group.fetchval_sum(pool, "SELECT count(*) FROM routing_decisions"),''',
     '''            "observations": await search_group.fetchval_sum(pool, "SELECT count(*) FROM routing_observations",
                                                            scope=store.current_scope()),
            "decisions": await search_group.fetchval_sum(pool, "SELECT count(*) FROM routing_decisions",
                                                         scope=store.current_scope()),'''),
    ('''            pool, "UPDATE routing_observations SET gold_correct = $2 WHERE id = $1::uuid",
            a.observation_id, a.correct == "true")''',
     '''            pool, "UPDATE routing_observations SET gold_correct = $2 WHERE id = $1::uuid",
            a.observation_id, a.correct == "true", scope=store.current_scope())'''),
])
print("ok")

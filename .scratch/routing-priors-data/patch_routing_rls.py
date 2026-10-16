"""Adopt the migration-150 routing scope in app/routing/store.py."""
import re

p = r"C:\Users\user\stealth-lab\backend\app\routing\store.py"
s = open(p, encoding="utf-8").read()


def rep(a, b, count=1):
    global s
    assert s.count(a) == count, (a[:90], s.count(a))
    s = s.replace(a, b)


# --- the scope: a context variable set at each entry point (service / plan for requests, workers for system)
rep('''class ObservationRejected(ValueError):
    pass
''', '''class ObservationRejected(ValueError):
    pass


# ------------------------------------------------------------------ row-level security scope (migration 150)
#
# routing_decisions / routing_observations carry FORCE RLS: 'public' rows for everyone, any other row only for its
# owner's readers (the caller's subject and organisations) or under the explicit system scope. Every statement on
# them runs inside a transaction that binds the CURRENT scope first. The scope is set once per entry point:
#   requests  (service.recommend, plan.load_instance / report_result)  -> routing_scope(access_scope)
#   workers   (refits, model update, admin commands, imports)          -> routing_scope(system=True)
# With no scope set a statement sees and writes 'public' rows only -- a forgotten scope fails closed.

import contextlib as _contextlib
import contextvars as _contextvars

_RLS_SCOPE: "_contextvars.ContextVar[dict]" = _contextvars.ContextVar("routing_rls_scope", default={})


@_contextlib.contextmanager
def routing_scope(access_scope: Optional[AccessScope] = None, *, system: bool = False):
    """Bind the routing logs' row-level-security scope for everything awaited inside (contextvars follow awaits)."""
    from app.services.access import routing_readers_of

    if system:
        value = {"system": True}
    elif access_scope is not None and getattr(access_scope, "is_unrestricted", False):
        value = {"system": True}
    elif access_scope is not None:
        value = {"readers": routing_readers_of(access_scope)}
    else:
        value = {}
    token = _RLS_SCOPE.set(value)
    try:
        yield
    finally:
        _RLS_SCOPE.reset(token)


def current_scope() -> dict:
    return dict(_RLS_SCOPE.get())


@_contextlib.asynccontextmanager
async def _log_tx(log: Any):
    from app.services.access import scoped_transaction

    async with scoped_transaction(log, **current_scope()) as conn:
        yield conn
''')

# --- writes
rep('''        log = await _goal_log_pool(pool, goal_id)
        await log.executemany(f"INSERT INTO routing_observations ({cols}) VALUES ({marks}){conflict}", batch)''',
    '''        log = await _goal_log_pool(pool, goal_id)
        async with _log_tx(log) as conn:
            await conn.executemany(f"INSERT INTO routing_observations ({cols}) VALUES ({marks}){conflict}", batch)''')

# --- single-goal reads: log.fetch / log.fetchrow / log.fetchval -> conn.* inside _log_tx
s = re.sub(
    r"(    log = await _goal_log_pool\(pool, (?:goal_id|str\(goal_id\)|str\(row\[\"goal_id\"\]\))\)\n)"
    r"((?:.*\n)*?)",
    lambda m: m.group(0), s)          # (no-op anchor; explicit replacements follow)

blocks = [
    ('''    log = await _goal_log_pool(pool, goal_id)
    rows = await log.fetch(
        "SELECT *, goal_id::text AS goal_id, procedure_id::text AS procedure_id FROM routing_observations "
        "WHERE goal_id = $1::uuid ORDER BY occurred_at", str(goal_id))''',
     '''    log = await _goal_log_pool(pool, goal_id)
    async with _log_tx(log) as conn:
        rows = await conn.fetch(
            "SELECT *, goal_id::text AS goal_id, procedure_id::text AS procedure_id FROM routing_observations "
            "WHERE goal_id = $1::uuid ORDER BY occurred_at", str(goal_id))'''),
]
for a, b in blocks:
    rep(a, b)

open(p, "w", encoding="utf-8", newline="").write(s)
print("ok part 1")

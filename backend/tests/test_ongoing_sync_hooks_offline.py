"""
DB-free coverage proving the ongoing-sync hook (app.stealth.ongoing_sync.
sync_delta_if_synced) is actually wired into the additional local write
points beyond record_stealth_edit: record_run_update and
close_exploration (docs/local_project_sync_security.md §G). The heavy
DB-backed internals of each tool are stubbed out here -- this test proves
the WIRING, not those tools' own unrelated behavior (which their own
DB-backed e2e suites already cover).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.authn import Actor, reset_current_actor, set_current_actor


def _run(coro):
    return asyncio.run(coro)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


class _actor_on_cv:
    def __init__(self, subject):
        self.actor = Actor(subject=subject) if subject else None

    def __enter__(self):
        self._tok = set_current_actor(self.actor)

    def __exit__(self, *exc):
        reset_current_actor(self._tok)



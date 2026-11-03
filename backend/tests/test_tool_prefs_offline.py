"""The person's "tools that need a sign-in" preference (app/tool_routing/prefs.py).

The phrase path is what makes "just say it" work; the store is what the gateway and the room agent read.
"""
from __future__ import annotations

import asyncio

from app.tool_routing import prefs


def run(coro):
    return asyncio.run(coro)


class Conn:
    def __init__(self, pool):
        self.pool = pool

    async def fetchval(self, sql, *args):
        return self.pool.value

    async def execute(self, sql, *args):
        self.pool.value = args[1]
        return "OK"


class Acq:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return Conn(self.pool)

    async def __aexit__(self, *exc):
        return False


class Pool:
    def __init__(self):
        self.value = None

    def acquire(self):
        return Acq(self)


def test_a_clear_short_message_sets_the_preference():
    assert prefs.preference_from_text("don't use tools that need a sign-in") is False
    assert prefs.preference_from_text("do not use tools that need a login") is False
    assert prefs.preference_from_text("turn off sign-in tools") is False
    assert prefs.preference_from_text("allow tools that need a sign-in") is True
    assert prefs.preference_from_text("you can use tools that need oauth again") is True
    # A task that merely mentions a sign-in tool is not a setting.
    assert prefs.preference_from_text("find me a tool that needs a sign-in for slides") is None
    assert prefs.preference_from_text("make a deck about oauth sign-in") is None
    assert prefs.preference_from_text("x" * 300) is None


def test_the_default_is_on_and_the_setting_round_trips():
    pool = Pool()
    assert run(prefs.allow_signin(pool, "user-1")) is True       # no row
    assert run(prefs.set_allow_signin(pool, "user-1", False)) is False
    assert run(prefs.allow_signin(pool, "user-1")) is False
    assert run(prefs.set_allow_signin(pool, "user-1", True)) is True
    assert run(prefs.allow_signin(pool, "user-1")) is True
    assert run(prefs.allow_signin(None, "user-1")) is True       # no database: the default, never a broken call

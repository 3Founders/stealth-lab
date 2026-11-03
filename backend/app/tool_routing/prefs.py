"""The person's tool preference: may kell use tools that need a sign-in?

Stored per account (the Supabase subject), so it follows the person to every device and applies to the room agent,
which runs server-side. The default is on: a tool that needs a sign-in is offered with a link. When it is off, the
gateway refuses those servers before any network call and says how to turn them back on; tools that need no sign-in
are unaffected.

`preference_from_text` is the "just say it" path: a short message that clearly asks to stop or allow sign-in tools is
recognised by the agent route, which saves the preference and answers privately instead of sending the message to a model.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# A message longer than this is a task, not a settings instruction; never treat it as one.
MAX_SETTING_TEXT = 200

_OFF = re.compile(r"\b(no|not|don'?t|do not|stop|disable|turn off|without|never)\b", re.I)
_ON = re.compile(r"\b(allow|enable|turn on|use|ok|okay|yes)\b", re.I)
_SIGNIN = re.compile(r"\b(sign[ -]?in|signin|log[ -]?in|login|oauth)\b", re.I)
_TOOL = re.compile(r"\b(tools?|servers?|connectors?)\b", re.I)


def preference_from_text(text: str) -> Optional[bool]:
    """True to allow sign-in tools, False to refuse them, None when the message is not a settings instruction."""
    t = " ".join(str(text or "").split())
    if not t or len(t) > MAX_SETTING_TEXT or not _TOOL.search(t) or not _SIGNIN.search(t):
        return None
    # "don't use tools that need a sign-in" has both an off word and "use"; the off word wins.
    if _OFF.search(t):
        return False
    if _ON.search(t):
        return True
    return None


async def allow_signin(pool: Any, owner: str) -> bool:
    """The person's setting; True (the default) when there is no row or no database."""
    if pool is None or not owner:
        return True
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval("SELECT allow_signin_tools FROM tool_prefs WHERE owner_id = $1", owner)
        return True if value is None else bool(value)
    except Exception:  # noqa: BLE001 -- a preference read must never break a tool call
        return True


async def set_allow_signin(pool: Any, owner: str, allow: bool) -> bool:
    if pool is None or not owner:
        return bool(allow)
    async with pool.acquire() as conn:
        await conn.execute("""INSERT INTO tool_prefs (owner_id, allow_signin_tools) VALUES ($1, $2)
                              ON CONFLICT (owner_id) DO UPDATE SET allow_signin_tools = $2, updated_at = now()""",
                           owner, bool(allow))
    return bool(allow)

-- Tools that need a sign-in are on by default (178_tool_prefs.sql). Until 2026-11-04 the room agent read loose messages such as
-- "ok I did the sign in, use the tool now" (or one with "not") as the setting itself, and could turn it off without anyone asking.
-- Every "off" stored before this migration is put back on, once; an "off" set after it is kept.
UPDATE tool_prefs SET allow_signin_tools = TRUE, updated_at = now()
 WHERE NOT allow_signin_tools AND updated_at < TIMESTAMPTZ '2026-11-04 15:30:00+00';

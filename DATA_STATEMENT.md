# Data statement

Plain language, no legalese. This is what happens to your data when you run
StealthLab v0.1. For who can access it once it's stored, see `SECURITY.md`.

## The short version

Your traces stay on your machine, in a Postgres database you run yourself.
There is no StealthLab-hosted backend in v0.1 — we don't have a server that
your data flows to, because it doesn't exist. The one thing that does leave
your machine is text sent to an embedding provider you configure, so search
works. We never train a model on your traces. There is no telemetry to
StealthLab in v0.1 — not opt-out, not hidden: the code path to send us
anything simply isn't built yet.

## What's stored, and where

- Traces from your coding agent (via the Claude Code hook) are redacted
  client-side, then written to a local JSONL file
  (`$CLAUDE_PROJECT_DIR/.claude/traces/` by default) and ingested into your
  own Postgres instance.
- Everything StealthLab derives from those traces — episodes, observations,
  claims, procedures, evidence — lives in that same Postgres database. It is
  yours: it's the container/volume you started, on infrastructure you
  control (see `backend/README.md` for setup).
- Nothing is copied, synced, or mirrored anywhere else by this project.

## What leaves your machine, and to whom

**Embedding generation is the one real exception**, and it's disclosed here
rather than left implicit. To make retrieval work, the text of stored nodes
(already passed through redaction) is sent to whichever embedding provider
you've configured — Voyage AI or Google Gemini — using your own API key.
That provider processes the text under its own terms and privacy policy,
which this project doesn't control. This is a genuine third-party data flow,
not telemetry to us: we never see it, and it only happens because you
supplied the API key that makes it possible.

No other network call leaves the shipped v0.1 capability set (`demo.md` §1)
by default. If you configure additional providers for optional/experimental
features outside that set, treat each one as its own disclosure — this
statement covers what ships, not everything the codebase can be pointed at.

## Redaction happens before any of this

`trace_redaction.py` runs at the point traces are collected, before
anything is written to disk or sent anywhere — there's no setting in v0.1
that skips it. It catches known secret shapes (API keys, tokens, private
key blocks) and excludes tool input/output wholesale when a sensitive path
was touched (`.env`, `.ssh/`, `id_rsa`, `.aws/credentials`, etc).

Said plainly, matching the code's own honesty about itself: **this is a
best-effort floor, not a guarantee.** No fixed pattern list catches every
secret shape that will ever exist. That's exactly why storage defaults to
local-only regardless of what redaction catches, and why the embedding flow
above only ever sends already-redacted text, never a raw trace.

## We never train on your traces

This is a durable commitment, not a v0.1-only fact of nothing being built
yet: StealthLab will not train a model on your traces or derived data
without asking you first, explicitly, separately from this document. Today
that commitment is easy to keep because no training pipeline exists at all.
If that ever changes — a hosted tier, an opt-in shared corpus, anything —
it ships as a clearly-labeled, opt-in feature with its own explicit
disclosure, never as a quiet default flip on existing installs.

## Uninstalling

There's nothing to deactivate on our end, because nothing runs on our end.
To remove everything: stop the MCP server / status page processes, delete
your Postgres volume, and delete your local trace directory
(`$CLAUDE_PROJECT_DIR/.claude/traces/` unless you pointed it elsewhere). No
account to close, no data-deletion request to file with us — you already
hold everything, so removing it is entirely in your hands.

## Questions

Open a GitHub issue, or see `SECURITY.md` if the question is about a
possible exposure rather than a general question.

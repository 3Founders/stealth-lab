# Security policy & threat model

StealthLab v0.1 is a **single-user, local-first** system: one coding agent,
one machine, one Postgres instance you run yourself. This document states
that posture plainly — what it protects against, what it deliberately does
not, and how to report a problem. It is the threat-model reference cited by
`demo.md` §2's production-posture rules and required by `demo.md` §3's ship
checklist before v0.1 releases.

For what happens to your data specifically (what's stored, what leaves your
machine, redaction, training), see `DATA_STATEMENT.md`. This document covers
access and trust boundaries.

## Deployment model this applies to

Everything below describes v0.1 as shipped: an MCP server and a status page
you run on your own machine against your own Postgres, fed by a Claude Code
hook. There is no StealthLab-hosted service, no multi-tenant deployment, and
no team/org access model in v0.1 — see `demo.md` §4's non-goals. If you
expose any of these processes beyond loopback, or run them on a shared or
multi-user host, everything below stops applying and you are outside this
threat model.

## The core posture: loopback-first, token-gated, not authorization

1. **Servers bind `127.0.0.1` by default.** The MCP server
   (`stealthlab-mcp-server`) and the status page (`stealthlab-status-page`)
   both default to loopback. Nothing here is designed, tested, or safe to
   put behind a public tunnel, a `0.0.0.0` bind, or a reverse proxy that
   accepts untrusted traffic. If you change the bind address, you have taken
   on a risk this project does not evaluate.

2. **The bearer token is authentication, not authorization.** Set
   `STEALTHLAB_MCP_TOKEN` and the MCP server refuses to start without it
   (fails at import time, not on first use); every HTTP tool call requires
   `Authorization: Bearer <token>`, checked with a constant-time comparison
   so a network observer can't learn it byte-by-byte from timing. What the
   token proves is narrow: **the caller is someone you handed the token
   to.** It does not mean the caller is limited in what they can then do —
   there is no per-tool, per-scope, or per-user permission model in v0.1.
   Concretely:
   - `apply_change_set` is a **raw, ungated write primitive**. Anyone
     holding a valid token can call it directly, bypassing human review of
     anything that would normally flow through `propose_synthesis`'s
     approval path.
   - `find_best_way`'s `repo_path` argument is **caller-controlled**. The
     sandbox stops edits from escaping the given path; nothing stops a
     caller from pointing it at a sensitive real directory in the first
     place.
   - `stdio` transport **bypasses the token entirely**, by MCP protocol
     design, not by an oversight here — anyone who can spawn the process
     already has equivalent access, so there is nothing left to gate.

   **Practical meaning:** treat the token as a way to let a process or
   machine you already trust reach the server — the same trust you'd extend
   by handing someone a shell on the box — not as an access-control boundary
   between untrusted parties. If you would not give someone a shell, do not
   give them the token.

3. **Single process, in-memory task state.** The Tasks-extension backing
   store (`propose_synthesis` / `find_best_way` progress) lives in one
   process's memory. Don't run multiple replicas behind a load balancer
   expecting shared state — `--workers 1` is load-bearing, not a default
   left alone.

## Redaction: default-on, best-effort, not a guarantee

`trace_redaction.py` is the single choke point every trace passes through
before it is ever written to disk or transmitted — there is no flag, in
v0.1, to persist a raw unredacted trace. It runs client-side, at the
collector, before anything leaves the hook process.

What it catches: known-token shapes (AWS, GitHub, Slack, Stripe, OpenAI,
Anthropic keys; generic `Bearer` tokens; PEM private-key blocks), plus a
second layer that excludes tool input/output **wholesale** when the tool
touched a sensitive path (`.env`, `.pem`, `.ssh/`, `id_rsa`-style keys,
`.aws/credentials`) — because a secret's byte content often doesn't match
any known-prefix pattern, but the path it lives at is a reliable signal on
its own.

**What it does not claim:** no fixed pattern set catches every real secret
shape that will ever exist. This is stated in the redaction module's own
docstring, not softened here for a public document — treat redaction as a
floor that measurably reduces exposure, never as a guarantee that nothing
sensitive can reach storage or an embedding provider. Because of that limit,
local-only storage stays the hard default regardless of what redaction
catches — see `DATA_STATEMENT.md` for exactly what does and does not leave
your machine.

## Known v1 limitations (stated plainly, not buried)

These are accepted-for-now tradeoffs for a single-user local tool, not bugs
to be quietly patched:

- `apply_change_set` is an ungated write primitive (above).
- `find_best_way`'s `repo_path` is caller-controlled (above).
- No per-user, per-scope, or per-tool authorization model exists in v0.1 —
  a valid token grants everything a token can grant.
- `stdio` transport has no authentication at all, by protocol design.
- Bulk/bootstrap ingestion endpoints are not gated beyond whatever protects
  the process itself.

None of these are appropriate for a shared, multi-tenant, or
Internet-exposed deployment. That deployment shape does not exist yet
(`demo.md` §4); when it does, this document will be rewritten around it, not
patched around the edges.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** on this repository
(the "Security" tab → "Report a vulnerability") rather than a public issue,
so a fix can land before details are public. There is no formal disclosure
SLA yet — this is a pre-v0.1 project — but reports will be acknowledged and
credited unless you ask otherwise.

If the report concerns data exposure specifically (a redaction gap, a path
this document's threat model didn't anticipate), please say so explicitly —
those get triaged against `DATA_STATEMENT.md`'s commitments, not just code
correctness.

## Supply chain

Embeddings are generated by a third-party provider you configure (Voyage AI
and/or Google Gemini, via your own API key) — this is the one network egress
point v0.1's core path has by default. See `DATA_STATEMENT.md` for exactly
what data that involves. No other third-party API calls happen in the
shipped v0.1 capability set (`demo.md` §1, C1–C5); anything beyond that
(the experimental multi-model harness under `experiments/`) is a separate,
explicitly non-production surface.

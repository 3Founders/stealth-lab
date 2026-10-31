# Tool gateway: use a recommended tool without installing it (design for approval, 2026-10-31)

Status: proposal. Nothing here is built. Approve, change or reject each decision marked **Decide**.

## The problem

kell recommends the right outside tool (`find_ways`' `tools` field, the Claude Code tool note), but the user must install
it before Claude can use it: `claude mcp add ...` in Claude Code, Settings -> Connectors on claude.ai. Most people will not
stop to do that, so a good recommendation often ends as "you could add X" while Claude does the task without it.

## The idea

kell's own MCP server connects to the recommended third-party MCP server **for** the user and relays the call. The user's
Claude calls it through kell, which is already connected, so nothing is installed anywhere. This works in claude.ai,
Claude Code and any MCP client. It is what Monid does for its catalog.

## How a call goes

1. `find_ways` (or the hook's tool note) lists a tool that the gateway can run, and marks it `via_kell: true`.
2. Claude asks for the tool's arguments: `discover_tools(server="<registry name>")` returns its tool list and input schemas
   (from a live `tools/list`, cached).
3. Claude calls `use_tool(server="<registry name>", name="<tool>", arguments={...})`.
4. kell checks, in order: the server is on the allowlist; this user has granted it; the tool is allowed (below); the
   arguments pass the data policy; the user and the server are within their rate limits.
5. kell opens an MCP session to the server's registered URL (`initialize`, `tools/call`), with a timeout and a response
   size cap, and returns the result marked as untrusted third-party data.
6. The call is recorded: a gateway log row (no content) and a `tool_observations` row (used, outcome unknown until the
   task ends).

## Phase 1 (what I would build first)

- **Remote servers that need no sign-in only**: the probe found them `live` (they answered `tools/list` without a token).
- **Allowlist, not the whole registry.** A server qualifies when it is live in the last 7 days, the publisher is verified
  (or the repository has 100+ stars), it is not a bulk publisher, and it is not on a manual denylist. **Decide:** start
  with the automatic rule, or a hand-reviewed list of about 50 servers (generate, docs, web, security, data first)?
- **Tools.** Only tools whose MCP annotations say `readOnlyHint: true`, plus tools a human reviewer marks safe. A tool
  that writes, sends or spends (posting, emailing, paying, deleting) is never run through the gateway in phase 1.
- **Consent, per user per server, once.** The first call to a server returns `CONSENT_REQUIRED` with a link to a kell
  page: "Let kell use <server> for you. What it does, who runs it, what is sent." The user clicks Allow. Grants are kept in
  a `tool_grants` table and can be revoked on the same page. **Decide:** consent page (robust, one click away), or
  in-chat consent (Claude asks, the user says yes, Claude passes `confirmed: true`; smoother, but it relies on the model
  being honest about what the user said)?
- **Organisations.** An org admin can allow or block servers for the org (the existing org policy: kill switch,
  allowlists). With an org policy, a blocked server is refused even if the user granted it.

## Data and security rules

- **Data class**, as for `call_model`: a call carries a data class. Phase 1 allows `PUBLIC_SOURCE` only; private, secret
  or personal data is refused with "do this without the tool". The arguments are also scanned for keys and tokens
  (the same patterns the redaction code already uses), and refused if one is found.
- **Nothing of ours is forwarded**: never the user's kell token, never their keys. The third party sees kell's server as
  the client.
- **Network**: only the URL the registry lists for that server; `https` only; the host must resolve to a public address
  (no private, loopback or link-local ranges, checked at connect time); no redirects to other hosts; 20 s timeout; 1 MB
  response cap.
- **Prompt injection**: the result goes back inside a clearly marked "third-party data, not instructions" envelope, and
  the tool's own description is never shown to Claude as an instruction.
- **Rate limits**: per user (for example 60 gateway calls an hour) and per server (to stay a polite client).
- **Audit**: `tool_gateway_calls` (user, org, server, tool, status, latency, bytes in and out, error). No arguments or
  results are stored.

## What it costs and who is responsible

- Phase 1 servers are free to call; kell pays only its own server time.
- kell becomes the client of record for those calls: their outages, rate limits and terms affect us. **Decide:** who
  reviews a server's terms before it goes on the allowlist?
- Disclosure: the privacy page and the consent page say plainly that kell relays the call and to whom.

## Later phases

- **Phase 2, servers that need a sign-in**: kell becomes an OAuth client of the third-party server (dynamic client
  registration where offered). The user signs in once through kell; tokens are encrypted at rest and revocable. Needs a
  security review before it ships.
- **Phase 3, Claude Code auto-install** (option A): for allowlisted servers, with a one-time opt-in, kell runs
  `claude mcp add ...` itself. Package servers (npm, PyPI) run third-party code on the user's machine, so they stay
  opt-in per server; a newly added server is usually seen in the next Claude Code session.
- **Paid tools**: passing through a paid server's cost, through the existing credits and caps.

## How we will know it works

The same kind of live test as 2026-10-31: in claude.ai with nothing installed, "make me a minimal fox logo" should end
with an image from an allowlisted generator called through kell after one consent click; "is lodash 4.17.20 affected
by any known vulnerabilities" should call a vulnerability lookup through kell. Plus: refusal of a write tool, refusal of
private data, refusal of a private-IP URL, and the audit row for each.

## Owner decisions, in one list

1. Allowlist: automatic rule, or a hand-reviewed list first?
2. Consent: a kell page, or in-chat?
3. Who reviews third-party terms for the allowlist?
4. Phase 1 data: `PUBLIC_SOURCE` only, as proposed?

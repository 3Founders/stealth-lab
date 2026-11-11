# Prompting a friend's Claude Code: options and the legal line

Research date: 2026-11-11. Sources: Anthropic's Claude Code docs (`/goal`, `/remote-control`, `/headless`,
`/legal-and-compliance`), the Consumer Terms (effective 2025-10-08), and Exa searches for how people share Claude Code
sessions. This is product/legal research, not legal advice; a lawyer should review the final design.

## The short answer

There is no Anthropic feature that lets **you** type into **your friend's** Claude Code account. The supported
remote surfaces are for the account owner:

- **Remote Control** connects `claude.ai/code` or the Claude mobile app to a session running on **your own** machine. It
  signs in with your own claude.ai account and "grants no one else access" (`/remote-control`).
- **Channels** push events from a chat app (Telegram, Discord, your own server) to a Claude Code session on the machine
  that installed the channel plugin. The session still runs under the owner's account.
- **Headless / Agent SDK** runs `claude -p` or the SDK programmatically. Anthropic's legal page says developers building
  products on top of Claude should authenticate with an **API key** (Commercial Terms), not a Claude.ai subscription.

Sharing the friend's login, OAuth token, Remote Control URL, or session token is **not** allowed. The Consumer Terms are
explicit: "You may not share your Account login information, Anthropic API key, or Account credentials with anyone else.
You also may not make your Account available to anyone else." The Claude Code legal page adds that third-party
developers may not "collect, store, or intermediate Claude.ai credentials or session tokens", and may not route requests
through Free/Pro/Max/Ultra credentials "on behalf of their users".

So the lawful version of "prompt my friend's Claude Code" is:

> The friend runs an **opt-in bridge on their own machine**, authenticated with **their own Anthropic API key** (not
> their Pro/Max login), and kell sends prompts to that bridge. The friend approves who may prompt, which repo it may
> touch, and which tools it may use. kell never sees or stores an Anthropic credential.

That is a "bring your own key" (BYOK) friend agent, not account sharing.

## What each surface actually is

| Surface | Who it serves | Credential | Can a friend prompt it? |
|---|---|---|---|
| Remote Control | the account owner, from their phone/browser | claude.ai subscription (API keys not supported) | No. The URL/account is the owner's; sharing it is account sharing. |
| Channels | the owner, from a chat app | the local Claude Code session's auth | Only the owner's own channel. A friend in the chat is not the owner. |
| Headless `claude -p` | scripts/CI | API key or subscription | Technically yes; legally, use an API key for another person's prompts. |
| Agent SDK | a product the developer runs | API key (Anthropic says so) | Yes, as a product, with each end user on their own key. |
| MCP server | tools, not the agent | whatever the server uses | Exposes tools; it does not give someone else the agent loop. |
| Third-party relay (e.g. `claude-opp`) | transport between machines | each machine's own Claude Code | The relay is neutral; the account-sharing rules still apply. |

## Legal findings (the parts that decide the design)

1. **No credential sharing.** Consumer Terms §2: don't share account login information, API key, or credentials; don't
   make the account available to anyone else. This rules out "give me your Claude Code token" and "use my friend's
   Remote Control link".
2. **Subscription use is personal/ordinary.** The legal page says advertised limits assume "ordinary, individual usage"
   of Claude Code and the Agent SDK. Routing a friend's prompts through a Pro/Max/Ultra subscription is not ordinary
   individual use.
3. **Products must use API keys.** The legal page: developers building products that interact with Claude, including the
   Agent SDK, "should use API key authentication through Claude Console or a supported cloud provider"; they may not
   offer Claude.ai login in their own apps or route requests through consumer plan credentials on behalf of users.
4. **Preinstalling Claude Code in a product** is possible under the Commercial Terms, but the binary must be unmodified,
   authentication must not be removed, and the product may not pay for, resell, or intermediate usage on end users'
   behalf. Each end user authenticates with their own API key, subscription, or cloud-provider credential.
5. **Data leaves the friend's machine.** Claude Code sends the prompt, relevant files/tool output and transcript to
   Anthropic under the friend's account/API key. If the friend's repo is under an NDA or contains third-party data, the
   friend needs permission to share it. Remote Control also stores the transcript on Anthropic servers.
6. **The friend's organization may have stricter rules.** Team/Enterprise admins can disable Remote Control, require
   trusted devices, restrict tools/data classes, or apply a BAA/ZDR configuration. Check the org policy before building.
7. **India-specific:** the DPDP Act 2023 (consent, purpose limitation, notice) and the IT Act/SPDI rules apply to
   personal data in the transcript; a cross-border transfer needs a lawful basis. A friend's code may also be a trade
   secret. Get a written, revocable consent and keep the data flow minimal.

## Recommended design for kell: a friend-hosted BYOK bridge

```
kell Rooms ──(kell account + room invite)──▶ friend's bridge (their machine)
                                              │  ANTHROPIC_API_KEY (their key)
                                              │  claude -p / Agent SDK in a chosen worktree
                                              │  tool allowlist + approval
                                              └─ streams events back to the room
```

Why this shape:

- **No credential leaves the friend's machine.** kell authenticates the *person* with a kell session; the bridge
  authenticates *kell* with a short-lived, revocable invite; Anthropic authenticates the *friend* with their API key.
- **The friend stays in control.** They choose the repo/worktree, the model, the tool allowlist, the spend cap, and
  whether each prompt is auto-approved or needs a click. They can revoke the room at any time.
- **Billing is honest.** The friend's API key pays Anthropic directly; kell does not resell or intermediate Claude
  usage. If kell ever charges for the relay, that charge must be for kell's own service, not for Claude tokens.
- **Auditable.** Every prompt/response/tool call is logged on the friend's machine with who asked and what changed.

### Bridge contract (MVP)

- Outbound connection from the friend's machine to kell (WebSocket/MCP over TLS); no inbound port.
- `POST /v1/friend/prompt` with `{room, from, prompt, files?, approval_id?}`; stream
  `assistant.delta`, `tool.started`, `tool.finished`, `done`, `error`.
- Auth: a kell-issued, room-scoped token with an expiry (e.g. 24h) and a revoke endpoint. The friend's kell account
  approves the room; the bridge stores only the token hash.
- Sandbox: a dedicated git worktree, a tool allowlist (`read`, `edit`, `bash` optional), no network by default, no
  secrets in the environment, and a per-prompt spend cap.
- Approval: first prompt per room requires the friend's click; then "allow this room for this session" or
  "ask every time".
- Kill switch: the friend's local UI/CLI can stop the bridge and every running turn.

### What not to build

- A "login with your friend's Claude" button, or importing their `~/.claude` credentials.
- Forwarding a Remote Control URL or a Claude Code OAuth token to another person.
- A relay that pools several people's Pro/Max subscriptions behind one kell account.
- Silent prompts: the friend must see who is asking and be able to stop it.

## Phases

1. **Research prototype (no product):** a local bridge the friend runs with their own API key; kell Rooms sends a
   prompt and shows the stream. One room, one friend, manual approval, no file upload.
2. **Consent and audit:** room invites, revoke, per-prompt approval, audit log, spend cap, a clear "this runs on
   <friend>'s machine with <friend>'s API key" label.
3. **Safety:** worktree isolation, tool allowlist, secret redaction, rate limits, a "stop everything" control.
4. **Only then** consider packaging it as a feature; have counsel review the terms, the consent text, and the data flow.

## Open questions for counsel

- Is a friend-to-friend relay "making the account available to anyone else" even when the friend uses an API key? (The
  API-key path is the strongest, but the consent and control design matters.)
- Does kell's relay count as "intermediating" Claude usage if kell never touches the token or bills for tokens?
- What consent/notice is needed under India's DPDP Act for the transcript, and where may it be stored?
- What happens when the friend's repo is under an NDA, or contains another person's data?
- Do Anthropic's usage limits/policies treat a friend's API-key usage differently from a product's end-user usage?

## Sources

- Claude Code `/goal`: https://code.claude.com/docs/en/goal
- Claude Code Remote Control: https://code.claude.com/docs/en/remote-control
- Claude Code headless/Agent SDK: https://code.claude.com/docs/en/headless
- Claude Code legal and compliance: https://code.claude.com/docs/en/legal-and-compliance
- Anthropic Consumer Terms (2025-10-08): https://www.anthropic.com/legal/consumer-terms
- Anthropic Commercial Terms: https://www.anthropic.com/legal/commercial-terms
- Anthropic Usage Policy: https://www.anthropic.com/legal/aup
- Exa results used: `claude-opp` (MIT relay over Tailscale), `claude-code-remote`, and the sharing/terms analyses at
  neovek.com, joxo.ai and usepoly.co (checked against the primary Anthropic pages above).

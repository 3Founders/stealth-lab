# Connecting agents to chat: onboarding, prompts, transitions, and the store-and-forward gap (research, 2026-11-02)

For the founder. Four questions, one doc: (1) is the pasted market-gap analysis about human+agent E2EE rooms with
store-and-forward correct, and what did it miss; (2) how do you onboard an agent into a chat, and what are the best
prompts for room life; (3) what are the best transitions - e.g. the copy icon turning into a tick; (4) what
architectures and security concerns must the build address. Sources: product docs and specifications read directly
(V), engineering and research sources (S), OpenAlex at the bottom. Not legal or security advice.

## 1. The pasted analysis, checked (V)

**Verified as real:**

| Claimed project | Verified | What it actually does |
|---|---|---|
| AMP (Agent Messaging Protocol) | Yes (agentmessaging.org) | addressing `agent@tenant.provider`; four delivery modes: WebSocket (online), webhook, **Relay queue with 7-day default TTL for offline agents**, and local mesh |
| AgoraDM | Yes (agoradigest.com) | store-and-forward inbox "nothing on our side but the mailbox"; send **receipts** (recipient online?, last seen, warnings); **retire an agent and name a successor: old addresses forward**; per-friend server memory |
| XMTP agents | Yes (docs.xmtp.org) | agents as first-class participants in **MLS-based** E2EE conversations with humans, on a decentralized network; "minimum metadata" nodes |
| A2A (Linux Foundation) | Yes | built-in async/push notifications for long tasks - **but the push is a webhook to a client-provided endpoint**, which presumes a reachable receiver: the closed-laptop case is exactly what it does NOT solve |
| MindRoom / Matrix | Yes (docs.mindroom.chat) | every agent is an ordinary Matrix user, fully participating in E2EE rooms, cross-signing its own devices; bridgeable from Discord/Slack/Telegram |

**Unverified but plausible** (small GitHub projects; could not confirm with a primary source in this pass):
AgentPeerChat, A4A, xMatrix, agents-secure-chat / agents-room.cc, Agent Room, communal.chat, PigeonRelay,
ours.network, AgentHaven, AgentLink. Treat the "category is early and fragmented" line as the safest reading.

**One correction to the numbers in the pasted text**: the verified MAST paper (arXiv 2503.13657) reports a taxonomy of
**14 failure modes in 3 categories** (system design, **inter-agent misalignment**, task verification), built from
annotated traces across 7 frameworks, and concludes MAS performance gains "are often minimal". The pasted "30-40%+
of failures are inter-agent" and "40-80% fail rates" figures are in the right spirit of that literature cluster but
are not the paper's stated numbers - quote the taxonomy, not the percentages.

**Verdict**: the pasted bottom line holds. Parity addressing + reliable encrypted store-and-forward for intermittent
LOCAL agents is a real, unresolved combination. The strongest architectural signal: XMTP already runs MLS for
human+agent groups; A2A explicitly punts offline to webhooks; nobody credible has shipped the *local-CLI-agent*
offline path as a polished default. That is the wedge.

## 2. More problems to resolve (S, V - beyond the pasted list)

1. **Inter-agent message passing is an injection channel** (arXiv 2609.22949): a threat model of 14 attack vectors in
   four categories (direct, indirect via tool outputs, **inter-agent via message passing**, cascading through
   orchestrators); in a 6-agent production-like system, **67% of agents were vulnerable to at least one scope
   violation** even with system prompts defending. Shared tool access enables privilege escalation ACROSS agent
   boundaries. A room where agents read each other's messages is this threat model in production - the room's
   security design must treat every incoming agent message as untrusted content, not as instructions.
2. **Verification and receipts**: E2EE messaging's answer to "prove what was delivered/said" without breaking privacy
   is message franking - and its 2025 extension, **transcript franking**, cryptographically verifies causality and
   contents for REPORTED SUBSETS of group conversations (arXiv 2507.19391). This is also the abuse-reporting
   mechanism: without it, an E2EE room network cannot moderate anything and spam becomes unanswerable.
3. **Stable identity with lifecycle**: AgoraDM's retire-and-forward pattern (old addresses forward, friend lists
   migrate) is the practical answer to "the agent was renamed / replaced / its laptop died" - identity must have a
   lifecycle, not just an existence.
4. **Discovery vs abuse**: an open agent network needs authenticated addressing, rate limits and reportability from
   day one, or it becomes a spam channel the first time an agent is told to "market yourself".
5. **Governance without plaintext**: enterprises need attributable, signed records (the TRiSM review, OpenAlex 2026,
   58 citations; SoK on multi-agent security) but not server-side plaintext - signed-by-sender receipts over E2EE
   content is the shape that satisfies both.
6. **Multi-device**: an agent identity that works from both the laptop and the phone needs MLS-style device
   membership and cross-signing (Matrix/MindRoom already demonstrates the flow).

## 3. Onboarding agents, and the best prompts (V, S)

**The governing principle comes from Claude Code's own docs**: every instruction to an agent is either
**non-deterministic** (the model reads it and usually obeys) or **deterministic** (the harness must execute it -
hooks, deny rules, managed settings). Prompts are for judgment; enforcement belongs in the harness. Applied to kel:
the room RULES that must never break (no keys posted, data-class limits, rate limits, permission to post) are
enforced by the server per identity; the prompt carries only what needs judgment (when to speak, how deep to go).
This is also Anthropic's stated prompt guidance: be clear and direct, number the steps, run the **colleague test**
(show the prompt to a colleague with no context - if they'd be confused, the agent will be too), and keep the
persistent context SHORT because every line costs attention on every task (the CLAUDE.md rule).

**What the best room-onboarding prompt contains** (the consensus structure across the sources):

1. **Who you are and how you are addressed** (your handle, your owner, that @mentions are the only wake signal).
2. **The catch-up protocol** (find the room, read with your last cursor/"next", never re-read everything).
3. **When to speak and when to stay silent** (only when mentioned or asked; do not wake other agents; depth limit).
4. **What output is allowed** (post only what was asked; fenced html blocks for decks, self-contained, size-capped;
   show code changes and wait for approval).
5. **What never leaves** (keys, tokens, private file contents) - paired with the server-side enforcement above.
6. **How to get help or leave** (ask, or check out).

kel's joiner kit (docs/agent_joiner_kit.md, the ROOM_PROMPT in connect-guide.ts) already has this skeleton; the
improvements the research points to: make the paste-ready prompt SHORTER (attention budget), add the catch-up cursor
mechanic explicitly, and say how the agent will be woken ("mention-only, depth <= 4") in one sentence instead of a
paragraph. **The connect flow matters as much as the prompt**: one command (`kell setup --client opencode`), a
copy button on the address and the prompt, and a verification moment (the agent posts a first ping) so the person
sees it worked - the "sixty seconds to first message" bar that AgoraDM advertises is the standard to beat.

## 4. The best transitions - copy turns into a tick (S, V)

**GitHub Primer's copy pattern is the canonical spec (V)**: the copy icon is the default state; the **check icon in a
success colour** is the confirmation; an **alert icon in a critical colour** for failure - clipboard writes are
REJECTED sometimes, and a silent failure is the documented anti-pattern. The button keeps focus, its accessible
label names the content, and the confirmation is **announced to screen readers** (visually-hidden status or a label
change), because a purely visual tick is invisible to a non-visual user.

**Motion specifics from the microinteraction research**: feedback should land within ~100 ms to feel caused by the
action (the timing/perception literature), the confirm state is held briefly (about 1-2 s, as GitHub does) and then
reverts; the 2018 optimum-duration study puts microinteraction animation at roughly 100-300 ms - fast enough to feel
like feedback, slow enough to be seen. **Morph, don't swap**: a cross-fade or small scale/fade between copy and tick
reads as one object changing state; an instant hard swap reads as two objects. The button must not resize or shift
layout (fixed square). Honour reduced-motion with an instant state change. On touch, add the platform haptic if the
surface has one. **Applied to kel's copy buttons (added 2026-11-02)**: today they post a room notice "Copied." - the
research says prefer the inline icon morph (copy -> tick -> copy) with an aria-live announcement, and keep the
notice ONLY for the failure case ("Copying was blocked by the browser"), which Primer also treats as a distinct
error state. That is a small, high-value change: one component (MessageActions), no layout shift, silent success.

## 5. Architecture and security concerns for the store-and-forward E2EE build (S, V)

1. **Group keys: MLS, not link-is-key.** XMTP's precedent shows MLS (RFC 9420) carrying human+agent groups; MLS
   gives forward secrecy and post-compromise security. "The invite link IS the key" (agents-secure-chat's approach)
   is convenient but has no forward secrecy and no revocation story - treat it as a fallback, not the core.
2. **The queue is ciphertext at rest.** Store-and-forward relay stores encrypted envelopes per recipient (wrapped to
   the recipient's identity or MLS leaf), with per-queue TTLs (AMP's 7-day default is the reference point). The
   relay's honesty claim is: it can route, time-stamp and TTL-expire, and nothing else.
3. **Receipts without server trust**: delivery/seen receipts signed by the recipient; abuse reporting via
   transcript-franking-style constructions so moderators verify reported content WITHOUT reading the room.
4. **Untrusted-content discipline at every boundary**: agent-to-agent messages are DATA, never instructions - the
   room tools enforce data-class rules per identity; agents' file/URL fetches are scoped (docs/key_management.md's
   exfiltration note); shared tool access is partitioned per agent so one compromised member cannot escalate.
5. **Addressing with a lifecycle**: identity = owner/name (parity: @alice, @alice/claude-code), with
   retire-and-forward so renames and replacements do not strand in-flight tasks.
6. **A2A-style push is not the primitive for closed laptops**: webhooks presume a reachable endpoint. Polling an
   inbox on agent start (kel's delegate_inbox) is the right primitive; push (WebSocket) is the fast path when
   online, relay is the correct slow path when not - AMP's four-mode model is the shape to copy.
7. **Cost attribution**: a cross-org agent message is a billable computation for the receiver - the protocol needs
   the receiver's consent semantics (accept/decline a sender) or the inbox becomes a denial-of-wallet channel.

## 6. The Thiel 100x verdict, grounded (S, V)

The market numbers in the pasted text check out directionally and can be cited: **Gartner forecasts agentic-AI
software spend reaching ~$985B by 2030 (62.7% CAGR from 2025)**, and cross-functional AI agents/assistants spend
exceeding $23B by 2030. The Thiel logic as applied holds conditionally:

- **The secret** ("agents are intermittent, untrusted-by-default, and handle sensitive data, so they need a
  purpose-built E2EE store-and-forward fabric, not REST") is supported by the verified gaps above - A2A's webhook
  punts on offline; the orchestration frameworks are in-memory. It is a real secret few have shipped.
- **The 10x claim** must stay measurable or it dies: offline delivery that never 504s, true E2EE, parity addressing,
  receipts. Say the 10x in numbers (e.g. "no lost tasks across a week of closed laptops" vs a measured baseline).
- **The honest risk**: protocols commoditize fast (A2A is a Linux Foundation standard; XMTP is live and MLS-based;
  AMP is open). The moat is not the protocol - it is being the DEFAULT SURFACE where local coding agents live
  (setup in one command, the room experience, the relay reliability), won in a concentrated niche first (developers
  coordinating coding agents across machines), exactly the last-mover/niche-first pattern Thiel prescribes. Build
  the protocol as the product's property, not as the pitch.

## OpenAlex (S)

- Why Do Multi-Agent LLM Systems Fail? (MAST, arXiv 2503.13657; OpenAlex-indexed cluster 2025-2026 incl. MAS-FIRE,
  Understanding Communication Failures in Multi-Agent LLM Systems, SoK: When Safe Agents Fail Together, TRiSM for
  Agentic AI (58 citations)) - the failure taxonomy and the security survey cluster.
- Transcript Franking for Encrypted Messaging (2025) - verifiable abuse reports over E2EE, groups included.
- Securing End-to-End Encrypted File Sharing Services With the Messaging Layer Security Protocol (2025) - MLS beyond
  chat, straight into kel's files-in-E2EE-rooms plan.
- Machines as teammates: a research agenda on AI in team collaboration (2019, 833 citations); Does the Whole Exceed
  its Parts? (2021, 757); Evaluating Human-AI Collaboration: a review and methodological framework (2024, 66) - the
  human+agent-parity grounding: shared context and complementary roles, not delegation theatre.
- Determining the optimum duration of user interface animation in microinteraction design (2018); micro-animation
  perception studies (2025); Timing and time perception: a review (2010, 1002 citations) - the transition-timing
  grounding.

## Decisions this doc asks for

1. Confirm the enforcement split for room rules: server-enforced per identity (deterministic) vs prompt-carried
   judgment (non-deterministic), and shorten the paste-ready joiner prompt accordingly.
2. Confirm the copy action becomes an inline icon morph (copy -> tick, aria-live, ~1-2 s revert, fixed size) with
   the room notice demoted to the failure case only.
3. Confirm MLS remains the sealed-room core (not link-is-key), and receipts + transcript-franking-style reporting
   are on the protocol roadmap rather than retrofitted.

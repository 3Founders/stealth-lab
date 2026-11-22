# kell memo: where we are, and the pains we can solve

2026-11-05. For the founders. Written from the session of 2026-11-04; the pain figures are from search excerpts of the linked pages, not from reading each page in full.

## Where we are

The room, the named agents and the canvas work are live; the rest is built or designed but waits on a deploy or a decision.

- **Live (Railway 5a4d8ac6):** agents are named exactly as they join (`@owner/tool/chat`). `@claude` calls your own agent and `@owner/claude` calls someone else's, which its owner must approve. An agent's answer is posted into the room, and the sidebar shows it connected while it polls. MCP tools `canvas_list`, `canvas_read` and `canvas_write` edit a canvas in place.
- **Pushed, not deployed:** the rooms-first server instructions (`de3612f8`), `read_earlier` so `@kell` can look past the last 12 messages (`db23f877`), and a smaller Stop button. The kell stand-in for an offline Claude (`658a2820`) is also pushed; it was rejected and should come out.
- **Built, switched on, not confirmed running:** kell signs in to remote tool servers for the person (OAuth). `ORG_KEY_ENCRYPTION_KEY` and `TOOL_GATEWAY_OAUTH` are now set on Railway, but the running server may predate them.
- **Built:** a one-page tools and models search (`docs/dashboard/tools-models.html`), tested in a browser.
- **Designed only:** a marketplace paid through Razorpay Route, server-side memory with background "dreaming", and end-to-end encrypted private rooms.
- **Waiting on a person:** a Railway deploy; whether Claude may answer while not running, using the person's own Anthropic key; a security review before OAuth goes on for everyone.

## The pains people report today

People running several agents or tools say the same four things: context does not travel, agents collide, tool lists swamp the model, and nobody can verify the output.

| Pain | What is reported | Source |
| --- | --- | --- |
| Context does not travel between agents | In 944 analysed issues from 21 open-source multi-agent projects, orchestration and execution problems were the most common; hand-off failures, lost shared context and duplicated tasks are named categories. Subagents get only the task prompt; the parent's history does not carry over. | [Alrashedy et al.](https://arxiv.org/html/2610.00905v1), [CrabTalk](https://crabtalk.ai/blog/multi-agent-coordination), [Claude Code docs](https://code.claude.com/docs/en/agent-teams) |
| The human becomes the router | "You have become the transport layer": the same decision is explained to each tool again. Claude Code memory is machine-local; Cursor and Codex keep theirs separately. | [Taskfolk](https://taskfolk.ai/blog/split-work-between-coding-agents), [Noetix](https://www.noetix.dev/guides/shared-context-for-ai-coding-agents) |
| Agents collide or spiral | Duplicate work, silent blocking, last-writer-wins overwrites of shared state, and simultaneous response storms when several agents share a channel. | [DEV: coordination problem](https://dev.to/seakai/the-coordination-problem-nobody-warns-you-about-when-you-start-running-ai-agent-teams-4jmf), [DEV: what breaks](https://dev.to/yuhaixia/i-coordinate-a-team-of-ai-agents-heres-what-actually-breaks-cob), [Agentic Dispatch](https://the-agentic-dispatch.com/la-bande-a-bonnot-paper/) |
| Sharing a session with a team | A Claude Code share link is read-only and a Codex session is driven by one person, so teammates see only the pull request afterwards. | [Eigen](https://www.geteigen.space/blog/shared-workspaces-claude-code-codex) |
| Too many tools in the model's context | One test with 114 tools used over 100,000 tokens for a simple prompt, and accuracy drops as the list grows. | [Stacklok](https://stacklok.com/blog/optimizing-multi-mcp-workflows/) |
| Tool sign-in and trust | Many identity providers lack dynamic client registration; OAuth scopes are too coarse for per-tool permissions; MCP has no native tenant isolation. | [Bastion](https://bastion.tech/blog/building-production-mcp-server-lessons/), [StackOne](https://www.stackone.com/blog/mcp-where-its-been-where-its-going/) |
| Cost and limits across tools | Each tool has its own sign-in and usage window; Anthropic reports agent teams in plan mode use about seven times the tokens of a standard session. | [Requesty](https://www.requesty.ai/blog/manage-claude-code-cursor-codex-costs-across-a-team) |
| Nobody can verify the output | 96% of developers do not fully trust AI code, yet only 48% always check it before committing; 38% say reviewing it takes more effort than a colleague's code. 78.7% call source attribution important. | [Sonar](https://www.sonarsource.com/company/press-releases/sonar-data-reveals-critical-verification-gap-in-ai-coding/), [Stack Overflow 2026](https://stackoverflow.co/company/press/archive/stack-overflow-annual-developer-survey-ai-trust-conditional/) |

## What we can solve, and what to do first

The delegator, the room protocol and the platform each answer part of the table; the verification gap is the one nobody else answers well, and our procedure and evidence records are built for it.

- **Room protocol** answers the router problem and collisions: one room where people and named agents share context, an owner's approval before anyone calls their agent, and a durable record. Gap: a claimed task has no lease, so a Claude session that stops leaves it stuck.
- **Delegator** answers cost and limits: cheap-model delegation with a check, and the next model tried when a check fails. We have no measured saving yet, so we claim none.
- **Platform** answers tool overload and sign-in: a search that returns a handful of tools instead of the whole list, and kell signing in to tool servers once for the person. This still needs the security review.
- **Verification:** procedures that carry evidence and a check turn "trust me" into something a reader can inspect. This is the strongest fit with the survey data and with the later goal-procedure-claim design.

**Do first:**

1. Add leases, heartbeats and a reaper to delegated tasks, so stuck work returns to the queue.
2. Decide whether Claude may answer while not running, using the person's own key, and drop the kell stand-in.
3. Approve one Railway deploy covering the pending commits, then check OAuth on four real servers.
4. Show each answer's check and evidence in the room, so the verification gap is visible to users.

Open question: the sources are blog posts and vendor pages as much as studies, so treat each pain as a lead to confirm with users, not a market size.

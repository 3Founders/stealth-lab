# Open-source AI chat / multi-agent frontends: licenses, maintenance, reusable UI pieces (as of 2026-10-28)

Method note: findings come from search-result summaries and a few page fetches. Most candidates were not verified against the repo LICENSE files or source. Items marked Gap need a direct GitHub check.

## Does any project already model a ROOM with several humans and several named agents with @mentions? Which come closest?

### Takeaway
Three projects fit: MindRoom (Matrix-based, strongest evidence), Open WebUI Channels (the only mainstream chat UI with multi-user, multi-model @mention rooms), and the small nexor-agora and stoops-cli projects. Dedicated chat UIs such as LibreChat, LobeChat, Chainlit and assistant-ui are single-user chats and are not room models, though I did not verify that for every one.

### Cited Findings
- MindRoom is a multi-agent runtime on Matrix. Each agent is a Matrix account. Tagged agents respond. In threads with two or more people, agents answer only when tagged. A router picks a responder in a new untagged thread. Mentioning several agents makes them collaborate. Opt-in "adaptive participation" lets an agent answer untagged messages. It uses Matrix thread relations and bridges to Slack, Telegram and Discord. — [libraries.io/pypi/mindroom](https://libraries.io/pypi/mindroom)
- MindRoom license is Apache-2.0, except `saas-platform/`, which is BSL 1.1 and converts to Apache-2.0 on 2030-02-06. Stack is Python (mindroom-nio, Agno toolkits), any Matrix homeserver, and Mem0/ChromaDB memory. It ships its own client, MindRoom Chat (web, Mac, iOS, Android beta), and a dashboard that needs Node 24 and Bun. Version 2026.10.633 was released Oct 28, 2026, so it is actively maintained. — [libraries.io/pypi/mindroom](https://libraries.io/pypi/mindroom)
- Open WebUI Channels are persistent shared spaces where multiple users and multiple models interact in one timeline. A model is summoned by typing @ and its name. Replies stream in real time and can use the model's configured tools, filters and knowledge bases. — [docs.openwebui.com/features/channels](https://docs.openwebui.com/features/channels.md)
- Open WebUI v0.6.29 (2025-09-17) added model mentions in channels, @ mentions for users and models, and # mentions for channels. This comes from a reposted changelog, which is a secondary source. — [changelog repost](https://lemmy.sudovanilla.org/post/11899)
- Open WebUI channels also have a `channel.mention` event for apps. — [docs.openwebui.com/features/channels/building-apps](https://docs.openwebui.com/features/channels/building-apps.txt)
- nexor-agora is described as MIT-licensed, self-hosted chat for humans and their Claude Code agents. Mentioning a person pings them through Slack or Telegram. Mentioning an agent wakes a waiting Claude session. It has MCP tools `chat_send` and `chat_wait`. — [pypi.org/project/nexor-agora](https://pypi.org/project/nexor-agora/0.4.3/) (via search summary; my direct fetch of the 0.4.4 page failed)
- stoops-cli is listed as a chat server where Claude Code, Codex and humans share a room, with messages pushed into each agent's session in real time. License and @mention handling were not stated. — [mdskills listing](https://www.mdskills.ai/mcp-servers/stoops-cli?tab=source)

### Inferences
- MindRoom's rules (tag required once two or more humans are present, router for untagged messages) are directly relevant to routing in a multi-human room.
- Open WebUI is the closest ready-made web UI for human-plus-model rooms. Its "agents" are configured models, not autonomous named agents with identity, and its license has a branding clause (see the license question).
- By inference only, Matrix gives rooms, threads, identity and bridges at the protocol level. Building on Matrix and bridging is a plausible alternative to building a room model from scratch.

### Gaps
- Not verified: nexor-agora's maintenance status, UI quality and star counts; stoops-cli's license.
- No searches covered Discord-bot-style agent hubs, "agent Slack" products, or the Matrix bridges beyond MindRoom.
- Whether Open WebUI channels have tool-call cards, stop controls, or per-agent identity beyond the model name was not confirmed.

## How do they render tool calls and MCP payloads (dual human and machine view)?

### Takeaway
Evidence is thin. MindRoom shows collapsible tool traces in chat, and Vercel AI Elements and assistant-ui are the component libraries aimed at this. The AG-UI protocol standardizes tool-call events. I did not verify any project's rendering code or a dual human/machine view.

### Cited Findings
- MindRoom Chat shows tool traces in the chat, collapsible. — [libraries.io/pypi/mindroom](https://libraries.io/pypi/mindroom)
- AI Elements is Apache-2.0 (per package listings). It is installed through a CLI that copies component `.tsx` source into your project. Its README and changelog mention reasoning displays and tool-call visualizations. — [AI Elements README](https://cdn.jsdelivr.net/gh/vercel/ai-elements@main/README.md), [Vercel changelog](https://vercel.com/changelog/introducing-ai-elements), [package listing](https://depscope.dev/pkg/npm/ai-elements)
- assistant-ui is described as an MIT React library built on shadcn/ui and Tailwind. A paid hosted add-on exists (assistant-cloud). A separate "Tool UI" component set from the same company has an unconfirmed license. — [ossinsight](https://ossinsight.io/analyze/assistant-ui/assistant-ui), [pricing page](https://aicoolies.com/pricing/assistant-ui), [everydev](https://www.everydev.ai/tools/assistant-ui)
- AG-UI is an event-based, bidirectional protocol over SSE (WebSockets and webhooks also possible). CopilotKit's docs say it has 16 event types covering text deltas, tool calls, state snapshots and deltas, and run lifecycle. The Kotlin SDK docs say 24, so the count is version-dependent. — [CopilotKit docs](https://docs.showcase.copilotkit.ai/agentic-protocols/ag-ui), [Kotlin SDK docs](https://docs.copilotkit.ai/ag-ui/sdk/kotlin/core/overview)
- Microsoft Agent Framework supports AG-UI. — [learn.microsoft.com](https://learn.microsoft.com/agent-framework/integrations/ag-ui)

### Inferences
- AG-UI's tool-call and state-delta events are a candidate wire format: a human card can be generated from the same structured payload the machine consumes.
- AI Elements and assistant-ui are copy-in or library React components. Their licenses are permissive, so they are the lowest-risk pieces to reuse for streaming messages, reasoning/thinking blocks and tool cards.

### Gaps
- Not checked: LibreChat, LobeChat, Dify, Langflow, Chainlit, OpenHands and AutoGen Studio rendering of tool calls or MCP payloads.
- I did not confirm the exact AG-UI event names, or that AI Elements has a tool-confirmation component.
- No project was found that shows an explicit side-by-side human and raw-JSON view.

## How do they implement stop, pause and human-approval controls?

### Takeaway
Coding agents have mature per-category approval models (Cline, Roo, Zed). Among room-style projects, MindRoom has approval cards and cancel commands. AG-UI and CopilotKit define human-in-the-loop pausing.

### Cited Findings
- MindRoom shows approval cards listing exactly what will be sent and to whom. Per-tool approval rules are configurable. `!auto_approve` skips approval for a time window. Responses can be cancelled, and `!cancel_job` and `!cancel_schedule` stop background work. No dedicated stop button was described. — [libraries.io/pypi/mindroom](https://libraries.io/pypi/mindroom)
- CopilotKit docs have Human-in-the-Loop pages on pausing the agent for input and on headless interrupts. — [CopilotKit docs](https://docs.showcase.copilotkit.ai/agentic-protocols/ag-ui)
- Microsoft Agent Framework's AG-UI integration lists function approval requests for user confirmation, with middleware converting approval-required functions into the approval protocol. — [learn.microsoft.com](https://learn.microsoft.com/agent-framework/integrations/ag-ui)
- Cline evaluates auto-approve per tool call across categories (read file, edit file, run command, browser). A YOLO mode approves everything. — [docs.cline.bot/features/auto-approve](https://docs.cline.bot/features/auto-approve), [aurascape](https://aurascape.ai/answers/cline-auto-approval-yolo-mode-risks/)
- Roo Code auto-approve categories: read, edit, run commands (with an allow-list), browser, MCP, mode switching and retries, with a master toggle. — [docs.roocode.com](https://docs.roocode.com/advanced-usage/auto-approving-actions)
- Zed Agent's docs treat Tool Permissions as the source of truth for approval, separate from Agent Profiles, which control tool availability. The Agent Client Protocol (Apache-2.0) supports permission-gated tool execution. — [zed.dev/docs/ai/zed-agent](https://zed.dev/docs/ai/zed-agent.md), [ACP adapter README](https://cdn.jsdelivr.net/npm/cursor-agent-acp@0.1.1/README.md)

### Inferences
- The shared pattern across Cline, Roo and Zed is approval keyed by tool category with a global escape hatch. That maps onto a scoped approval policy per room or agent.
- Approval requests carried as protocol events (AG-UI, ACP) keep the gate on the machine side rather than only in the UI.

### Gaps
- Stop/abort behavior was not researched for assistant-ui, Vercel AI SDK, LibreChat, Open WebUI or Chainlit.
- OpenHands confirmation mode: my search returned nothing relevant.
- A Roo Code license check and a Zed tool-permissions page fetch were not done.

## License, stack and maintenance per candidate

### Takeaway
License precision matters. Open WebUI (branding clause), Dify (multi-tenant and logo conditions), LobeChat (derivative-work restriction) and Zed (GPL/AGPL) all carry strings. MIT/Apache candidates: assistant-ui, AI Elements, LibreChat, Cline, MindRoom, nexor-agora.

### Cited Findings
- Open WebUI: from v0.6.6 the license forbids altering or removing "Open WebUI" branding, except with 50 or fewer users in 30 days, with written permission from a substantive contributor, or with an enterprise license. Code up to and including v0.6.5 stays BSD-3. A Nixpkgs commit marks it non-free. — [license text](https://docs.openwebui.com/license/), [Fossies LICENSE](https://fossies.org/linux/open-webui/LICENSE), [wz-it commentary](https://wz-it.com/en/blog/open-webui-license-branding-business/), [Nixpkgs commit](https://code.ornl.gov/nix/nixpkgs/-/commit/ac3ae15f554aadd84e29c7c0a73b9fa54f568721)
- Dify: Apache-2.0 plus (1) no operating a multi-tenant environment without written authorization (one tenant = one workspace) and (2) no removing the logo or copyright when using its frontend. An OSI license-discuss thread (October 2025) argues it is not open source. — [Dify docs](https://docs.dify.ai/policies/open-source), [OSI list](https://lists.opensource.org/pipermail/license-discuss_lists.opensource.org/2025-October/022425.html)
- LobeChat: the newer LICENSE is the "LobeHub Community License", while some copies say Apache-2.0. Commercial use is allowed unmodified, but a commercial license is needed to develop and distribute derivative works. Only mirrors and summaries were found, not the primary text. — [LICENSE mirror](https://cdn.jsdelivr.net/npm/@lobehub/chat@1.143.3/LICENSE), [Open WebUI comparison](https://docs.openwebui.com/alternatives/lobehub)
- LibreChat: MIT per several secondary sources. Supports MCP agents, a code interpreter and an agent marketplace. One tracker lists a last release of 2026-08-14. Release candidate v0.8.8-rc3 is mentioned. One source says ClickHouse acquired it in November 2025 and another says it joined ClickHouse. — [mintmcp](https://www.mintmcp.com/blog/librechat-open-source-chatgpt-alternatives), [everydev](https://www.everydev.ai/tools/librechat), [ideaproof](https://ideaproof.io/open-source/project/librechat)
- Chainlit: a tracker rated it stable as of May 2026, with a last commit around 2026-04-24, about 199 contributors and about 12.1k stars. CVEs: CVE-2026-45018 (MCP enabled, fixed in 2.12.0) and session hijacking before 2.10.1. License not confirmed. — [beamforai](https://www.beamforai.com/tools/157/status), [NVD](https://nvd.nist.gov/vuln/detail/CVE-2026-45018)
- AutoGen: the official repo says maintenance mode, no new features, and points new users to Microsoft Agent Framework (reported GA 2026-04-02 by a secondary source). Code reported MIT by one secondary source. AutoGen Studio was not covered separately. — [github.com/microsoft/autogen](https://github.com/microsoft/autogen), [futureagi](https://futureagi.com/blog/what-is-autogen-2026/)
- Cline: Apache-2.0 per aggregators; its JetBrains plugin is not open source. Roo Code is a Cline fork, license unconfirmed. — [openapps.pro](https://openapps.pro/apps/cline), [standardcompute](https://standardcompute.com/best-ai-agent/cline-vs-roo-code)
- Zed: the source tree contains GPL, AGPL and Apache licenses. GPUI is Apache-2. ACP is Apache-2.0. — [Fossies zed-1.5.3](https://fossies.org/linux/misc/zed-1.5.3.tar.gz/), [Fossies LICENSE-AGPL](https://fossies.org/linux/zed/LICENSE-AGPL)
- AI Elements Apache-2.0; assistant-ui MIT (see the tool-call question for sources).

### Inferences
- For embedding in a proprietary product, the safest reusable layer is permissive component code (AI Elements, assistant-ui, protocol specs AG-UI and ACP). Whole-app forks of Open WebUI, Dify, LobeChat or Zed bring licensing obligations.

### Gaps
- Not researched or not found: Vercel AI SDK UI hooks details, CopilotKit license, AG-UI repo license, Langflow, OpenHands (license, UI, maintenance), AutoGen Studio license, Claude-Code-style multi-agent UIs, Roo Code license and maintenance, and Open WebUI release cadence in 2026.
- Most license claims here come from secondary sources and need confirmation against each repo's LICENSE file.

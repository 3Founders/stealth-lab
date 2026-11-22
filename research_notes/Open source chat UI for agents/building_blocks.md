# Building blocks and UX patterns for a human + AI-agent group chat

Coverage note: about 11 searches, snippet-level only (no deep page fetches). Many items are gaps; the writer should treat uncited recall as unverified.

## 1. @mention composer with autocomplete and accessibility

### Takeaway
Tiptap Mention (on the ProseMirror-based `@tiptap/suggestion`) is the best-documented option, but it gives lifecycle hooks only: ARIA combobox/listbox wiring is the integrator's job. The W3C APG combobox pattern is the spec to implement.

### Cited Findings
- Tiptap Mention adds @-mention with a suggestion popup and full control over rendering; several trigger characters are supported via `suggestion`/`suggestions` (for example `@` for people, `#` for topics). — [Tiptap docs](https://tiptap.dev/docs/editor/extensions/nodes/mention.md)
- In collaborative sessions the menu can open for remote users; docs suggest a `shouldShow` option checking transaction origin. — [Tiptap Mention API](https://tiptap.dev/api/nodes/mention)
- `@tiptap/suggestion` detects triggers and exposes onStart/onUpdate/onKeyDown/onExit; keyboard handling and ARIA are left to you. — [tessl registry for @tiptap/suggestion](https://tessl.io/registry/tessl/npm-tiptap--suggestion)
- A 2021 grant to "Make Tiptap Accessible" (ARIA API) was submitted and later canceled; completion unconfirmed. — [OpenCollective grant 51204](https://opencollective.com/google-open-source-cms-fund/expenses/51204)
- APG combobox: DOM focus stays on the textbox; `aria-activedescendant` points to the highlighted `option` in a `listbox`; `aria-selected="true"` only on that option; script must scroll the active option into view; Down moves (wraps), Enter accepts, Escape closes keeping focus in textbox. — [W3C APG combobox, autocomplete list](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/examples/combobox-autocomplete-list/), [autocomplete none](https://www.w3.org/WAI/ARIA/apg/example-index/combobox/combobox-autocomplete-none.html)
- Linear treats agents like users: they can be @mentioned and delegated issues; mentioning or delegating creates an AgentSession. — [Linear agents docs](https://linear.app/developers/agents.md)

### Inferences
- Because the Tiptap suggestion layer has no built-in a11y, the mention list should be a small custom component following the APG pattern (textbox + `aria-activedescendant` + listbox), or built on a headless combobox (Ark UI / Radix-based) rather than trusting the editor.
- The mention list should group humans and agents with an "agent" badge (matches Linear's practice below).

### Gaps
- Lexical `lexical-beautiful-mentions`, Slate, react-mentions, cmdk: search returned nothing usable (no license, bundle size, or a11y evidence). Not verified.
- Bundle sizes for Tiptap core/ProseMirror: not found.

## 2. Virtualized auto-scrolling message lists

### Takeaway
TanStack Virtual now documents a chat recipe (`anchorTo: 'end'`, no column-reverse hacks) and is headless; react-virtuoso has `followOutput` and prepend support but points chat/AI UIs to a separate `@virtuoso.dev/message-list`. For a day-divider/sender-grouped list, either works; TanStack is the better fit for fully custom markup.

### Cited Findings
- TanStack Virtual chat guide: use `anchorTo: 'end'`, `followOnAppend`, `scrollEndThreshold`; keep `ref={virtualizer.measureElement}` for dynamic heights; no need for `flex-direction: column-reverse`, inverted transforms, or manual scrollTop prepend compensation. — [TanStack Virtual chat guide](https://tanstack.com/virtual/latest/docs/chat), [Virtualizer API](https://tanstack.com/virtual/latest/docs/api/virtualizer.md)
- TanStack describes itself as lightweight (10-15kb, marketing range, not measured). — [TanStack Virtual](https://tanstack.com/virtual)
- react-virtuoso `followOutput` takes a callback `(isAtBottom) => 'auto' | 'smooth' | boolean`; common pattern only follows when already at bottom. Prepending history uses decreasing `firstItemIndex` with `computeItemKey`. — [Virtuoso docs mirror](https://tessl.io/registry/tessl/npm-react-virtuoso/files/docs/list-virtualization.md)
- The react-virtuoso skill says chat/AI conversation UIs (streaming, stick-to-bottom, prepending) should use `@virtuoso.dev/message-list`. — [virtuoso-skills](https://www.claudepluginhub.com/skills/petyosi-virtuoso-skills-packages-virtuoso-skills/react-virtuoso)
- Open react-virtuoso discussion on preserving scroll position when switching chats. — [discussion 885](https://github.com/petyosi/react-virtuoso/discussions/885)

### Inferences
- Day dividers and sender-group headers should be flattened into the item array (row types: divider, message) so virtualization stays uniform.
- For a few thousand messages per room, virtualization may be unnecessary at first; start simple.

### Gaps
- License and exact bundle size for react-virtuoso and TanStack Virtual: not confirmed (react-virtuoso 4.18.7 existed on npm; license not read). `@virtuoso.dev/message-list` licensing (possibly commercial) not verified.
- React 19 / static-export compatibility: not verified for any library.

## 3. Typing / presence / "agent working" indicators

### Takeaway
Zulip documents a concrete, copyable protocol; Linear gives the clearest agent-specific rule (acknowledge fast, show session state). Slack/Discord/Matrix specifics were not found.

### Cited Findings
- Zulip typing: server is stateless; client sends "start" on typing and every 10 s while typing, "stop" after at least 5 s pause or cancel; displayed indicator expires after 15 s without a new start. Works for DMs and channels. — [Zulip typing indicators](https://zulip.readthedocs.io/en/latest/subsystems/typing-indicators.html)
- Discord users note the indicator shows only in the channel being viewed (feature request for server-wide). — [Discord support forum](https://support.discord.com/hc/fr/community/posts/360069888272-Server-wide-writing-indicator)
- Gizmodo: early typing indicators fired on first keystroke and cleared after 5-10 s without keystrokes; in large channels the indicator signals not to interrupt. — [Gizmodo](https://gizmodo.com/what-the-someone-is-typing-bubbles-in-messaging-apps-ac-1827744443)
- Linear AIG: immediate but unobtrusive feedback on invocation; emit a thought activity within 10 s; session state shown to users (5 or 6 states depending on page, including `stale`); agents carry a small badge; a human stays shown as responsible. — [Linear AIG](https://linear.app/developers/aig.md), [Getting started](https://linear.app/developers/agents.md), [Agent interaction](https://linear.app/developers/agent-interaction.md)
- Linear: issues are assigned to humans, delegated to agents; the human remains primary assignee. — [Linear agents docs](https://linear.app/developers/agents.md)

### Inferences
- Reuse Zulip's 10 s/5 s/15 s timing for humans and a TTL-based "agent working" state (heartbeat that expires), so a crashed agent cannot show "working" forever.
- Show agent status as a state (queued/working/awaiting approval/stale) rather than a generic typing dots.

### Gaps
- Slack and Matrix typing/presence design, and Discord's own docs: no sources found.

## 4. Threads and side panels

### Takeaway
No direct evidence gathered.

### Cited Findings
- Linear's model attaches an agent session to an issue/comment thread via mention or delegation. — [Linear agents docs](https://linear.app/developers/agents.md)

### Inferences
- A side panel can double as the "agent session" view (activity log, state) opened from a thread.

### Gaps
- Slack, Zulip (topics), Element threads/right-panel design write-ups: not searched successfully. Unverified.

## 5. Global pause / circuit breaker and "approval needed" notices

### Takeaway
Agent frameworks document durable approval pauses; no source found for a dedicated kill-switch UI or incident-response analogue.

### Cited Findings
- LangGraph HITL uses interrupts and checkpoints so a paused run survives refresh and can be resumed from another UI component. — [LangChain HITL frontend docs](https://docs.langchain.com/oss/langchain/frontend/human-in-the-loop)
- Mastra distinguishes tool-level approval (gate the call) from workflow-level approval (pause at a step). — [Mastra](https://mastra.ai/blog/hitl-where-to-put-approval-in-agents-and-workflows)
- Blocking (live) vs asynchronous approval (notify via Slack/email, park task). Prompting at every step leads operators to disable the agent. — [Understanding Data](https://understandingdata.com/posts/human-in-the-loop-patterns/), [Sessionize talk](https://sessionize.com/s/sonichigo/approve-before-you-deploy-human-in-the-loop-patter/185444)
- Trust levels "always / sensitive / never". — [Nylas Helix](https://helix.nylas.com/glossary/human-in-the-loop)
- Linear AIG: agents must step back immediately when asked to disengage; final responsibility stays with a human. — [Linear AIG](https://linear.app/developers/aig.md)

### Inferences
- A "max depth reached, human approval needed" notice maps to a persisted interrupt: render as a pinned system message with Approve/Deny, state stored server-side so refresh keeps it.
- A global pause should be a persisted room/workspace flag that agents check before each step, shown as a persistent banner.

### Gaps
- Incident-response UI patterns (PagerDuty/Statuspage banners) and a dedicated kill-switch UI: nothing found.

## 6. Design systems and per-category picks

### Takeaway
Ark UI / Park UI (MIT, Zag.js, framework-agnostic, Panda CSS) and Radix Themes / Mantine (MIT) are all viable; Compound (Element) is AGPL-3.0 with a commercial license, so avoid unless AGPL is acceptable.

### Cited Findings
- Mantine MIT, ships pre-styled; Radix Themes MIT; one comparison lists Radix Themes at 284 kB minified JS (single, unverified source). — [daisyUI compare](https://daisyui.com/compare/radix-vs-mui), [wmtips](https://www.wmtips.com/technologies/compare/mantine-vs-radix-ui)
- Ark UI is MIT and headless, built on Zag.js state machines. — [Ark README](https://cdn.jsdelivr.net/gh/chakra-ui/ark@main/README.md)
- Park UI is built on Ark UI and Panda CSS, MIT per npm listings; the Panda preset listing was marked deprecated (check current status). — [Park UI](https://park-ui.com/react/docs/overview/introduction), [yarn listing](https://classic.yarnpkg.com/en/package/@park-ui/panda-preset)
- Compound Web (Element's React implementation) repo contains AGPL-3.0 and LICENSE-COMMERCIAL files. — [compound-web](https://redirect.github.com/vector-im/compound-web)

### Inferences
- If Tailwind-free and vendorable is the priority: Ark UI headless parts for combobox/popover/menu plus hand-written CSS variables; Radix Themes is the lowest-effort pre-styled option.
- Compound is a useful visual reference, but license risk makes copying code inadvisable.

### Gaps
- shadcn/ui, Chakra, Primer: not searched; Park UI bundle size and Mantine bundle size not found.
- Concrete UX numbers (spacing, grouping window, hover action bar, density, agent vs human colour) from Zulip/Element/Rocket.Chat/Discord: not found. Only Linear's "small agent badge" is sourced.

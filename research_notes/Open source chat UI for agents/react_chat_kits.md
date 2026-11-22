# Open-source React chat UI component kits (for Next.js 15 / React 19 static export, custom CSS, in-memory transport)

## Which can run fully client-side with our own in-memory transport (no vendor backend)?

### Takeaway
Chatscope, shadcn's new chat components, assistant-ui, Blazity shadcn-chatbot-kit and MUI X Chat headless are all transport-agnostic and MIT. Stream's `stream-chat-react` is not usable: it needs the `stream-chat` client and a Stream account, and its license is proprietary and conflicts with open-source use. Virtuoso's message-list is client-only but carries a commercial license.

### Cited Findings
- stream-chat-react: the README says to "Register to get an API key for Stream Chat" and requires the `stream-chat` package alongside; free tier is limited to <=5 team members and <$10k monthly revenue. — [GitHub README](https://github.com/GetStream/stream-chat-react)
- stream-chat-react v14.14.1 npm license is "SEE LICENSE IN LICENSE", and `stream-chat ^9.54.0` is a required peer dependency. — [npm registry](https://registry.npmjs.org/stream-chat-react/latest)
- The Stream LICENSE file is a proprietary "Source Code License Agreement": must be a current Stream customer, cannot "operate a service", cannot combine with open-source components in ways that could subject it to open-source licenses, cannot build a "Competitive Product", and cannot distribute source. (Summary by fetch tool, not legal advice.) — [LICENSE](https://raw.githubusercontent.com/GetStream/stream-chat-react/master/LICENSE)
- @chatscope/chat-ui-kit-react is MIT, v2.1.1; the README example uses `MainContainer`, `ChatContainer`, `MessageList`, `Message`, `MessageInput` with no backend. The companion `@chatscope/use-chat` is a headless state hook with debounced typing indicators and "grouped messages state for many conversations". — [GitHub](https://github.com/chatscope/chat-ui-kit-react), [npm](https://registry.npmjs.org/@chatscope/chat-ui-kit-react/latest), [chatscope features](https://chatscope.io/features/)
- shadcn's June 2026 chat components (MessageScroller, Message, Bubble, Attachment, Marker) explicitly do not own "messages, AI state, transport, persistence, or model state"; you supply the renderer. — [shadcn changelog](https://ui.shadcn.com/docs/changelog/2026-06-chat-components)
- assistant-ui `@assistant-ui/react` is MIT (v0.15.32). Its mention picker needs no backend unless items come from a remote source; adapter methods are synchronous. — [npm](https://registry.npmjs.org/@assistant-ui/react/latest), [mentions guide](https://www.assistant-ui.com/docs/guides/mentions)
- `@mui/x-chat-headless` is MIT, v9.0.0-alpha.20, "Preview"; headless namespaces Chat, ConversationList, Conversation, MessageList, MessageGroup, Message, Composer, Indicators. — [npm](https://registry.npmjs.org/@mui/x-chat-headless/latest), [MUI docs](https://mui.com/x/react-chat/headless/)
- ChatKitty React library hooks talk directly to ChatKitty's hosted backend; CometChat's React kit is a hosted SaaS product. — [search summary](https://github.com/ChatKitty/chatkitty-react-component-library), [CometChat](https://www.cometchat.com/react-chat-components)
- `@virtuoso.dev/message-list` v1.18.3 license is "Commercial" (requires `VirtuosoMessageListLicense`); it is unstyled and client-side. — [npm](https://registry.npmjs.org/@virtuoso.dev/message-list/latest), [docs](https://virtuoso.dev/message-list/)

### Inferences
- With in-memory transport, the cleanest fits are shadcn chat primitives + own composer/mention code, or Chatscope, or assistant-ui's UI layer (though assistant-ui's runtime assumes an LLM-assistant model of messages, not multi-user rooms - unverified).
- Anything Stream/CometChat/ChatKitty is out for a vendored, no-backend static export.

### Gaps
- Did not verify whether assistant-ui's runtime can model multi-party (non-LLM) conversations.
- Ably/Pusher-agnostic kits: no distinct open-source UI kit surfaced in searches; not found.

## Which look modern (Slack/Discord/Linear quality) vs dated?

### Takeaway
shadcn/ui-based options (shadcn chat components, AI Elements, assistant-ui, shadcn-chatbot-kit) match the current modern Tailwind aesthetic; Chatscope and react-chat-elements are the older bootstrap-era look. No Slack-density channel UI exists off the shelf in open source from what I found.

### Cited Findings
- shadcn components "come pre-styled to match shadcn/ui"; announced Aug 2026, >121k stars on shadcn/ui, high community reception. — [shadcn changelog](https://ui.shadcn.com/docs/changelog/2026-06-chat-components), [InfoQ](https://infoq.com/news/2026/08/shadcn-conversational-primitives)
- Vercel AI Elements (Conversation, Message, Prompt Input, Reasoning, Tool, Sources, Task, Queue, code/voice/workflow components) is "moving to shadcn/ui" and will be deprecated at end of November 2026. — [AI Elements](https://elements.ai-sdk.dev/)
- Chatscope: 43 components, themed through ~400 SCSS variables, framework-independent; depends on FontAwesome packages. — [chatscope features](https://chatscope.io/features/), [npm](https://registry.npmjs.org/@chatscope/chat-ui-kit-react/latest)
- Blazity shadcn-chatbot-kit (MIT, 806 stars): Chat, auto-scroll message area, message input with file upload, prompt suggestions, message actions, loading states; built for the Vercel AI SDK and is AI-chatbot oriented. — [GitHub](https://github.com/Blazity/shadcn-chatbot-kit)
- Community alternatives named: ibelick prompt-kit. — [InfoQ](https://infoq.com/news/2026/08/shadcn-conversational-primitives)

### Inferences
- Visual quality ranking (my judgement from dependency/style stack, not screenshots reviewed): shadcn-based > MUI headless (you supply visuals) > Chatscope/react-chat-elements.
- Slack/Discord-like channel density would require composing MessageScroller + Message (grouped) + own sidebar.

### Gaps
- I did not review screenshots/demos of each kit; modernity judgments are from stack, not visual inspection.

## Which are easiest to restyle to a custom design system (custom CSS, no Tailwind)?

### Takeaway
Headless/unstyled layers are easiest: `@shadcn/react` message-scroller, `@mui/x-chat-headless`, and (commercial) Virtuoso. Copy-paste shadcn registries are vendored and fully editable but assume Tailwind classes; Chatscope is CSS-framework-independent but SCSS-variable-driven.

### Cited Findings
- `@shadcn/react` is a new package of unstyled headless components, first primitive `message-scroller`, for Radix and Base UI; the registry versions are pre-styled with Tailwind. — [shadcn changelog](https://ui.shadcn.com/docs/changelog/2026-06-chat-components)
- MUI X Chat headless: visual treatment stays in own CSS, slots, or design-system components. — [MUI docs](https://mui.com/x/react-chat/headless/)
- assistant-ui components are shadcn-style copied files built on `@base-ui/react` and `class-variance-authority`; `TriggerPopover` accepts `className`. — [mentions guide](https://www.assistant-ui.com/docs/guides/mentions)
- `@minchat/react-chat-ui` requires `styled-components >=6` as a peer (v1.5.2, license MIT per registry version records), a poor fit for plain custom CSS. — [npm](https://registry.npmjs.org/@minchat/react-chat-ui/latest), [registry doc](https://registry.npmjs.org/@minchat/react-chat-ui)
- react-chat-elements v12.0.18 (MIT) peers on React ^18.2.0 / react-dom 18.2.0 only (no React 19 declared), and uses hugeicons. — [npm](https://registry.npmjs.org/react-chat-elements/latest)

### Inferences
- For a custom design system with plain CSS, the best approach is `@shadcn/react/message-scroller` (unstyled) plus own markup, or vendor the shadcn components and rewrite Tailwind classes to our CSS.
- Dependence on Tailwind in registry components means a Tailwind build step unless rewritten.

### Gaps
- Not verified: how much Tailwind the shadcn `message`/`bubble` sources use versus CSS variables (did not read source).

## Compatibility, bundle weight, maintenance, and component coverage (React 19 / Next 15 static export)

### Takeaway
React 19 peers are declared by Chatscope, stream-chat-react, assistant-ui, MUI headless, Virtuoso; not by react-chat-elements (React 18 only) or minchat (>=18, no upper bound, so technically allowed). Only Stream (via react-virtuoso) and Virtuoso message-list ship virtualization; shadcn's MessageScroller does not document it. No kit offers mentions + threads + presence out of the box without a backend, except Stream (proprietary).

### Cited Findings
- Peers: Chatscope react ^16.12 .. ^19 — [npm](https://registry.npmjs.org/@chatscope/chat-ui-kit-react/latest); stream-chat-react react 17/18/19 — [npm](https://registry.npmjs.org/stream-chat-react/latest); assistant-ui ^18 || ^19 — [npm](https://registry.npmjs.org/@assistant-ui/react/latest); MUI headless ^17..^19 — [npm](https://registry.npmjs.org/@mui/x-chat-headless/latest); react-virtuoso 4.18.19 MIT, any React >=16 — [npm](https://registry.npmjs.org/react-virtuoso/latest).
- stream-chat-react components: Channel, Window, ChannelHeader, MessageList, MessageComposer, Thread; depends on react-virtuoso, react-markdown, chart.js, emoji-mart (optional), many others (heavy). LGPL lamejs optional for audio. — [GitHub](https://github.com/GetStream/stream-chat-react), [npm](https://registry.npmjs.org/stream-chat-react/latest)
- shadcn chat: MessageScroller handles anchored turns, streamed replies, prepended history, jump-to-message, scroll controls; Message supports avatar, alignment, header, footer, grouped messages; Marker for date separators/system notes; no typing indicator component (suggests `shimmer`); virtualization not mentioned; a GitHub issue reports mount-time anchors never applied. — [shadcn changelog](https://ui.shadcn.com/docs/changelog/2026-06-chat-components), [InfoQ](https://infoq.com/news/2026/08/shadcn-conversational-primitives)
- assistant-ui: Thread component and `@` mention trigger-popover (`composer-trigger-popover`, `directive-text`), directive format `:type[label]{name=id}`. — [Thread docs](https://www.assistant-ui.com/docs/ui/thread.md), [mentions guide](https://www.assistant-ui.com/docs/guides/mentions)
- MUI X chat headless includes MessageGroup, Conversation (thread header), Indicators (typing, unread, scroll affordances); mentions and virtualization not mentioned. — [MUI docs](https://mui.com/x/react-chat/headless/)
- Virtuoso message-list examples: messaging interface, AI chatbot, reactions, date separators, scroll to reply, grouped messages; virtualized. — [docs](https://virtuoso.dev/message-list/)
- Mention textarea options: Dice UI Mention via 21st.dev (needs 21st API key for install), shadcn.io mention block (third party, unaffiliated with shadcn). — [21st.dev](https://21st.dev/@diceui/components/mention.md), [shadcn.io](https://www.shadcn.io/blocks/comments-mentions)
- Maintenance signals: AI Elements deprecation Nov 2026; MUI chat is v9 alpha; shadcn chat Aug 2026 first phase; Chatscope repo 1.8k stars, 110 commits shown, v2.1.1 latest (release date not obtained); shadcn-chatbot-kit 596 commits. — sources above.
- Bundle weight: no measured sizes obtained. Chatscope pulls FontAwesome (4 packages) + classnames; stream-chat-react has ~35 dependencies. — [Chatscope npm](https://registry.npmjs.org/@chatscope/chat-ui-kit-react/latest), [Stream npm](https://registry.npmjs.org/stream-chat-react/latest)

### Inferences
- All client-rendered libs work in a Next 15 static export if used in `"use client"` components; no kit needs SSR. (Not tested.)
- Practical stack for our goal: vendored shadcn `message-scroller/message/bubble/marker` (or `@shadcn/react` headless) + own composer using assistant-ui's trigger-popover pattern or a small custom mention list; add react-virtuoso (MIT, plain list) only if very long histories matter. Typing/presence indicators and threads would be hand-built.
- Chatscope is the only kit with a ready-made classic chat component set (typing indicator, avatars, conversation list) that is MIT and backend-free, but dated look and SCSS.

### Gaps
- No bundlephobia/size data retrieved; no last-commit/release dates verified for Chatscope, minchat, react-chat-elements (npm time field was truncated).
- @minchat/react-chat-ui: license MIT per npm version records; the GitHub repo URL fetched returned 404, so repo health unknown. Looks stale (seen versions around 2023) but unconfirmed.
- Whether react-virtuoso (plain) can serve chat reverse-scroll adequately: not verified here (the chat-specific package is commercial).
- Next.js 15 specific compatibility claims: none of the sources state it explicitly.

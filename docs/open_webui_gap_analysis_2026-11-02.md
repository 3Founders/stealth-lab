# Open WebUI vs our /chat: what we lack (2026-11-02)

**Source of the ideas: Open WebUI (https://github.com/open-webui/open-webui).** We took the list of things people can do in it as
the checklist for our own /chat. No code, text, icons or styling were copied; each feature is built in our own terms and our own
code. Anything shipped from this list should say so in its commit message and docs.

Input: Open WebUI's README, its docs overview and about twenty of its docs pages (chat features, history and search, folders,
chat controls, follow-ups, shortcuts, multi-model, reasoning, sharing, temporal awareness, message queue, code execution,
artifacts, memory, notes, channels, prompts, web search, RAG). The repo itself was not cloned or run: everything here is what
the docs say. Where a docs page was not read (URL parameters, autocomplete, automations, sub-agents, timers, voice mode) the
line is marked *thin*. Our side is judged from the code in `routing_kell_frontend/` and what this session touched; items
marked **(check)** need a look before building because I did not open that part.

**Left out on purpose (we are ahead, or it is not something a person using the chat sees):** Markdown rendering; chat history held
encrypted on the device and synced across devices with an offline queue; people and agents in one room with an approval step for
connected agents; the consent gateway for outside tools; the rupee wallet and plans; routed models and verified procedures. Also
left out: everything that only an operator sees (admin panels, roles, SSO, usage dashboards, banners, API keys, vector-store and
search-provider choices, deployment).

Verdicts: **NOW** = small, fits what we have; **NEXT** = real work, clear value; **LATER** = worth it after launch signals;
**SKIP**.

## 1. Conversation list and organisation

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| Search (Cmd+K) across chats, messages, shared folders, notes; type filter, view filter, preview pane, `tag:` `folder:` `pinned:` `archived:` prefixes | No search **(check)** | **NOW** | Cmd+K palette over the rooms and messages already on the device (decrypt locally, search locally). Filters: room, person, agent, has-image. Enter opens the message and highlights it. Search never leaves the device. |
| Rename a chat, plus "Generate" a title; auto-title setting | Rooms are labels you create | **NOW** | Rename a room in the room menu; optional auto-title from the first exchange, off by default (it costs a model call). |
| Pin chats | None **(check)** | **NOW** | Pin rooms to the top of the list. |
| Archive (hidden from list and search, browsable) | None | **NOW** | Archive a room: hidden, not deleted; "Archived" list in Manage rooms. |
| Mark as unread, Mark all as read | We have unread counts | **NOW** | "Mark as unread" on a room and "Mark all read". |
| Folders (nested), drag to move, folder default model, system prompt, knowledge, background image, sharing | Flat room list | **NEXT** | Folders for rooms with a default agent instance and instructions inherited by new rooms. Skip background images and "clone only" sharing. |
| Tags and auto-tagging | None | **LATER** | Free tags on rooms, searchable. Auto-tagging only if search shows people need it. |
| Multi-select with Shift-click, bulk archive and delete | None | **NEXT** | Same in Manage rooms. |
| Hover preview card on a chat | None | **LATER** | Pointer-only nicety. |
| Time grouping (Today, Yesterday, Previous 7 days) | None | **LATER** | Only once a person has dozens of rooms. |
| Swipe the sidebar on touch | We have a drawer | **LATER** | Swipe to open and close. |
| Delete all chats, export one chat or all as JSON, text or PDF; import JSON | History import and a wipe exist; export formats **(check)** | **NOW** | Per-room export as Markdown (and text); PDF later. Keep the full JSON export. |
| Temporary chat (`/temporary`): not saved, not synced | None | **NEXT** | A room that never leaves memory: no local save, no sync, no notifications body. Matches our privacy position and is cheap given the store boundary. |

## 2. Message box

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| Message queue: keep typing while the AI answers; queued lines send in order; Send now, Edit, Delete; survives chat switching | One answer at a time per agent | **NOW** | Queue above the box while an agent is answering, with Send now, Edit and Delete. Persist per room in memory only. Setting to turn it off (interrupt instead). |
| Expand the message box (full-height editor) | None | **NOW** | An expand button once the text wraps; Esc closes. |
| Per-room drafts | **(check)** | **NOW** | Keep unsent text per room across switches and reloads (local, encrypted with the history). |
| `/` command menu | We just added it for two commands | **NOW** | Grow it (see section 5). |
| `@` menu also switches model and attaches knowledge, files, folders, URLs | `@` mentions people and agents only | **NEXT** | `@` a file or URL to attach it once we have knowledge (section 6). |
| `#` to attach knowledge, files or a URL | None | **NEXT** | Same. |
| `$` to activate a skill | None | **LATER** | Maps to our procedures (section 9). |
| `:` emoji picker | None | **LATER** | |
| Tab to accept ghost-text autocomplete and follow-ups | None | **NOW** | See follow-ups, section 3. |
| Rich text input with a formatting toolbar | Plain text box | **LATER** | Not asked for by anyone yet. |
| Paste large text as a file | **(check)** | **NEXT** | Paste above a size turns into an attachment instead of flooding the box. |
| Compare text (diff two drafts), `/compare` | None | **LATER** | Useful for editing outputs; belongs with the canvas. |
| Voice dictation in the box (Cmd+Shift+L) | None | **NEXT** | Browser speech recognition behind a mic button; no server. |
| Writing blocks (`:::writing`, email variant) | Fenced html blocks only | **LATER** | |
| Reference chats: attach a whole earlier chat as context by drag | None | **NEXT** | "Add a room as context" for an agent (plain text, no retrieval). |

## 3. Messages and responses

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| Regenerate; continue response | Copy and Resend exist for connected-agent tasks | **NOW** | Regenerate and Continue on kell and instance answers. |
| Edit a message and re-ask; ArrowUp edits the last one; Structured edit of tool calls | None | **NOW** | Edit your own message; ArrowUp in an empty box edits the last. Skip the structured JSON editor. |
| Delete a message | **(check)** | **NOW** | Delete your own message (tombstone, synced). |
| Generate message pair, Cmd+Shift+Enter | None | **SKIP** | Developer tool. |
| Follow-up prompts: clickable chips under a reply, with a setting to insert instead of send | None | **NOW** | Three suggestions after a finished kell answer, generated with the same call (one extra line in the prompt, no second request). Click inserts by default; Tab on an empty box takes the first. Off switch in Preferences. |
| Fork a chat from a message (`/fork`, button under each reply) | None | **NEXT** | "Fork room from here" copying history up to that message into a new room. |
| Merge responses (mixture of agents) | None | **LATER** | Only with compare, section 4. |
| Reasoning shown as a collapsible "Thought" block, with configurable tags and an effort control | Streams the text as it comes | **NOW** | Detect think tags in the stream, fold them into a collapsed "Thought" block, kept in the message but hidden until opened. Effort control later. |
| Citations: sources listed per answer, click opens the source | The room agent names the tools it used **(check)** | **NEXT** | A "Sources" row under an answer that used a search or fetch tool, with the URL and the passage. Needs the gateway to return structured results. |
| Message actions: copy last response and last code block shortcuts | Copy on tasks only | **NOW** | Copy on every message and every code block. |
| Safe link rendering allow-list | Markdown renderer already uses safe links | **SKIP** | |
| Response auto-scroll toggle | Auto-follow exists | **NOW** | A Preferences switch to stop following. |
| Chat overview map | None | **SKIP** | |
| Comments on messages | None | **LATER** | Rooms are already conversations. |

## 4. Models and agents (the biggest conceptual gap)

Open WebUI separates **a model preset** (a base model plus system prompt, tools, knowledge and parameters) from the model. We
just made every added agent "another instance of kell", but an instance is only a name today.

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| Model presets: base model, system prompt, bound tools, knowledge, parameters, version history, access control | Instance = a name | **NEXT** | Give an instance a saved set: instructions, default model or routing policy, allowed tools, default knowledge. Edit it from the agent row. Keep versions (a list, set-live). This is what makes instances worth adding. |
| Dynamic variables in prompts (`{{USER_NAME}}`, `{{CURRENT_DATE}}`) and Chat variables / User variables forms | None | **NEXT** | Fill date, time, timezone, name and language into the system prompt server-side. (Models not knowing today's date is a recurring failure.) Chat and user variable forms later. |
| Temporal awareness tools `get_current_timestamp`, `calculate_timestamp` | None | **NOW** | Put the date and the viewer's timezone in the system prompt; add the two tools to the room agent. |
| Per-chat advanced parameters (temperature, top-p, num_ctx, reasoning effort) at chat, account and model levels with precedence | None | **LATER** | Our router picks the model; expose at most "answer style: faster, cheaper, better" per room. |
| Multi-model chat: pick two or more models, get parallel columns, regenerate per column | One agent answers | **NEXT** | For us this is routing made visible: ask the same question of two routes, show answers side by side with cost per answer in kell credits. Strong fit for the product. |
| Model switch mid-chat with `/model` | None | **NOW** | `/model` shows the route in use; `/model <name>` pins it for the room. |
| Sub-agents (parallel, same tools) | Instances exist | **LATER** | One kell instance asking another. Watch cost and the depth limit already at 4. |

## 5. Commands and shortcuts

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| `/compact`, `/status`, `/fork`, `/model`, `/settings`, `/compare`, `/skills:create`, `/temporary` | `/add-agent`, `/stop` | **NOW** | Add `/status` (context used, queued messages, tasks), `/fork`, `/model`, `/settings`, `/temporary`; `/compact` once an agent summarises long rooms. `/skills:create` becomes "turn this chat into a procedure" (section 9). |
| Prompts as slash commands: named templates with typed variables (text, select, number, date, checkbox...), versions, access control, public prompts | None | **NEXT** | A personal prompt library: `/name` inserts a template; `{{var | select:...}}` opens a small form. Share to an org later. |
| 21 configurable shortcuts, record-to-bind, reset, "show shortcuts" overlay (Cmd+/), disable all | A fixed shortcuts list in Settings | **NEXT** | Rebindable shortcuts for: new room, search, toggle sidebar, focus input, copy last answer, stop, regenerate, open settings. Show the overlay on Cmd+/. |
| Tool call allow or deny by shortcut | Consent link in the reply | **NEXT** | Inline Allow and Deny buttons on the consent notice, with shortcuts. |
| URL parameters (`?q=`, model, tools) | None *(thin)* | **NOW** | `/chat?room=&q=` to deep-link and prefill. Useful for the extension and for sharing a prompt. |

## 6. Knowledge, files and the web

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| Attach files; default upload mode: focused retrieval or the entire document, switchable per file | Pictures only | **NEXT** | Attach documents (text, PDF, code). Per file: "use all of it" or "find the relevant parts". Extract on the device first; nothing uploaded unless the person sends it. |
| Knowledge bases: libraries of files an agent retrieves from; hybrid search with reranking; agentic retrieval; file browser with preview | None | **NEXT** | A knowledge list per account, attachable to an instance. Start with full-context for small files, then retrieval. Store encrypted on the device; embeddings are the open question for a hosted agent (**decision needed**). |
| Cloud file pickers (Drive, OneDrive); knowledge sync from GitHub, Jira, Slack, Notion (45+ sources) | None | **LATER** | One connector first (GitHub), via our tool gateway. |
| Web search toggle with sources, confirmation before search, domain filters, save results to knowledge | The router can offer tools through the gateway | **NEXT** | A "search the web" switch in the message box that makes the router prefer a search tool, shows a confirmation, and lists sources (section 3). |
| Web page and YouTube transcript by pasting a URL | None | **NEXT** | Paste a URL to attach its text (public pages only, through the gateway). |
| Image generation and editing | An image tool exists in the router **(check)** | **LATER** | Show generated images inline with a regenerate button. |

## 7. Outputs: artifacts, canvas, code

Open WebUI's artifacts are renderable HTML, SVG and JS in a side window with a version selector, full-screen, copy and sandbox
switches, and a small per-artifact key-value store (personal or shared). Ours are fenced `html` blocks rendered in a sandboxed
frame inside the message. You asked for a canvas with encrypted local storage; this is the same gap.

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| Side-panel artifact window, versions, full-screen, copy | In-message card with Render | **NEXT** | Canvas: a right-hand panel for documents, code and pages the agent produces, with versions, edit-in-place and "ask the agent to change this selection". Stored on the device, encrypted with the same key as history, synced as encrypted blobs. (Separate design note to follow.) |
| Persistent artifact storage (key-value, personal or shared) | None | **LATER** | Only after the canvas exists. |
| Code Interpreter: run Python in the browser (Pyodide) with a file browser, charts captured | None | **NEXT** | A Run button on Python code blocks using Pyodide in a worker; no server. Charts inline. |
| Mermaid rendering | **(check)** | **NOW** | Render fenced `mermaid` blocks. |
| Open Terminal (a real shell and file browser for the agent) | Connected agents run on the person's machine | **SKIP** | Our "onboard your agent" covers this safely. |

## 8. Memory, notes, collaboration

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| Persistent memory: manual list in Settings, plus agent tools to add, update, search and list; user and context types; paths; budgets; background review | None | **NEXT** | A Memory screen (add, edit, delete, clear) and agent tools. Stored on the device, encrypted; only the entries chosen for a message are sent. Off by default, with the data path stated (the rule in our B2B copy notes applies). |
| Notes: a workspace outside chats, rich editor, pages, AI rewrite of a selection, attach to chat, search, export, voice recording, sharing | None | **NEXT** | A separate surface from the canvas (decision 1): your own writing, attachable to a chat, in the same encrypted local store. |
| Channels: public, private, group and DM channels, threads, pins, reactions, `@model` summons, read-only mode, mute, apps, encrypted channels | Rooms with people and agents, mentions, invites | **partly have** | Add: threads, reactions, pins, mute per room (mentions only, all, none), read-only rooms. Skip apps. Rooms already give us the model-in-the-room behaviour channels are built for. |
| Typing indicator and collaborator list in shared chats | **(check)** | **NEXT** | Typing indicator in shared rooms. |
| Share a chat: private, public to signed-in users, open link; static snapshot or live with replies | Rooms are shared by invite | **LATER** | A read-only snapshot link only if people ask. It conflicts with our encrypted-at-rest position unless the snapshot is made explicitly. |
| Calendar, Automations (scheduled prompts), Timers | None | **LATER** | Automations are the interesting one: "run this prompt every morning and post it to a room". Needs the server-side agent plus the wallet cap. |
| Task management: agents keep checklists shown live | Task lifecycle for connected agents | **LATER** | Show an agent's plan as a live checklist in the room. |

## 9. Skills and our own strengths

Open WebUI "Skills" are Markdown instruction sets, with `/skills:create` to turn a conversation into one. Our substrate stores
*verified procedures*, which is the stronger version. **NEXT:** "Save this as a procedure" on a finished thread, filling the
Goal, steps and check for review. This is the one place where adopting their idea makes our own product more visible.

## 10. Notifications, settings, platform

| Open WebUI | Us | Verdict | What to add |
|---|---|---|---|
| Keyboard, Interface, Personalisation settings | We have Preferences, Notifications, Data, Agents and models, Shortcuts, About | **NOW** | Add the settings above as they land (queue, follow-ups, auto-title, auto-scroll). |
| PWA with offline access | Offline queue exists; installable? **(check)** | **NOW** | Web manifest and a service worker so the chat installs and opens offline from the encrypted local store. |
| Responsive layout | Done | **SKIP** | |
| i18n | English only **(check)** | **LATER** | Hindi first, once copy settles. |

## Build order (my recommendation)

1. **NOW batch, one week:** search (Cmd+K), rename and pin and archive rooms, message queue, regenerate/continue/edit/delete, copy on every message and code block, follow-up chips with Tab, collapsed "Thought" blocks, current date and timezone in the agent prompt, `/status` `/model` `/fork` `/temporary`, expand the message box, per-room drafts, `?q=` deep link, per-room Markdown export, PWA manifest.
2. **Agent presets** (section 4): without them "instances of kell" are only names.
3. **Canvas** with encrypted local storage and versions (section 7); **Notes** as its own surface beside it (decision 1).
4. **Attach files and web pages, web search toggle, citations** (section 6).
5. **Memory**, **multi-model compare**, **prompt library**, **rebindable shortcuts**.
6. **Save as procedure** (section 9).

## Decisions (answered 2026-11-02)

1. **Notes and canvas are separate.** Canvas is for what an agent produces in a conversation (document, code, page) with versions;
   Notes is a place of your own writing that you can attach to a chat. Two surfaces, one shared encrypted local store.
2. **Knowledge and memory stay on the device; the server computes embeddings, later.** Until then, small files go in whole and
   retrieval waits. Nothing is built for embeddings yet.
3. **Compare shows model names.**
4. **Temporary rooms and per-room export: both.**

## Status after this round

Built and pushed: only the selected room has the glass effect; names of rooms, people and agents start with a capital; the "you"
row is gone from Agents; a reply that arrives all at once now animates; **file attachments (section 6 of `docs/adding_files.md`, steps
1 and 2)**: any file type, a tray in the message box, a file card in the message, upload with progress, server-side storage with
type sniffing, size cap, executable refusal and download-only serving; **agent presets (first cut)**: an added agent can be given
instructions ("what should it do?", or `--about=` on `/add-agent`), stored on the device and sent with each ask; **per-room Markdown
export** (`/export`). Not built yet from this list: canvas, notes, compare, memory, knowledge, search, queue, follow-ups, temporary
rooms, and the rest of the NOW batch. See the end of this
round's report for what is next.

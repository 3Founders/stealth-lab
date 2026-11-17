# What a desktop (and web) AI app must have, from what people actually use Claude for (2026-11-17)

This replaces the feature list in [reqthings.md](reqthings.md) (2026-11-08, IDE/assistant architecture). That doc stays as the architecture reference; this one is the **feature list**, built from usage evidence and checked against **kell desktop 0.1.25**.

## Sources and method

**Usage evidence**
- Anthropic Economic Index, June 2026 release: the raw data, already downloaded at `.scratch/office_tasks/data/`. It covers Claude chat + Cowork and the 1P API in April–May 2026: the share of each request topic, each output ("artifact") type, the collaboration mode, and work vs personal use.
- The June, March and January 2026 Economic Index reports.
- Anthropic's survey of 81,000 Claude users (April 2026).
- Clio.

**Product evidence.** Found through Exa (via kell) on 2026-11-17:
- Claude: Desktop release notes and changelog (v5.11492, 2026-11-13); Help Center articles on Cowork, Dispatch, computer use, quick entry, voice, memory, incognito, artifacts, Chrome, M365, Meetings, Design, Science, Personal Finance, enterprise configuration; Claude Code Desktop docs.
- Competitors: the ChatGPT desktop app (Chat/Work/Codex, "dots"), Gemini desktop, Copilot, Perplexity Comet.

**Web usage**
- claude.ai traffic: Semrush (October 2026), AlphaMoat (October 2026), Similarweb (August and September 2026).
- Desktop-vs-web comparisons and reviews from 2026.
- kell's website code (`routing_kell_frontend/app`).

**User voice**
- Reddit threads (through a snapshot site): daily use, personal use, limits.
- The problems.fru.dev complaint clusters for Claude.
- Open GitHub feature requests for Claude Desktop.
- Reviews of Cowork.

**kell status** comes from the desktop code at 0.1.25 (`desktop/app`, `desktop/electron`, `desktop/lib`), checked by reading routes and grepping. It is not a full click-through. Status marks:
- ✅ built;
- 🟡 partly built;
- ❌ missing;
- ❔ not checked.

**Priorities**
- **P0:** most users need it in their first week, or it's a trust or safety must.
- **P1:** core to the main jobs below.
- **P2:** valuable, but for a narrower group.
- **P3:** later.

---

## Part A — What people use Claude for (the evidence the list rests on)

### A1. Who and why

| Measure (Claude chat + Cowork, Apr–May 2026) | Value |
|---|---|
| Work | **44%** of conversations |
| Personal | **39%** (from about 35% on weekdays to just under 50% at weekends) |
| Coursework | **16%** |
| Augmentation (working with Claude) vs automation (handing work over) | 51% / 49%. The API is 6% / 94%: apps automate, people collaborate |
| Collaboration patterns | directive 31.5%, task iteration 30.1%, learning 16.6%, feedback loop 16.0%, validation 3.1% |
| Conversations with more than one task | 22.6% |
| Human time saved | Tasks estimated at **4.7 hours** without AI take about **39 minutes** with it |

Weekend and evening use shifts to emotional support, medical questions, investment advice, recipes (around 6 p.m.) and sleep (around 5 a.m.). Work use is business correspondence, marketing copy and slide decks. Tax questions spike before filing deadlines (Economic Index, June 2026).

### A2. What people take away: output types

| Output | Chat + Cowork | API |
|---|---|---|
| Explanation or answer | **16.7%** | 8.1% |
| Document or report | **14.7%** | 5.8% |
| Advice or recommendation | **10.4%** | 2.0% |
| No artifact (talk only) | 6.8% | 15.9% |
| Analysis or summary | 5.5% | **21.6%** |
| Email or message | 4.6% | 2.3% |
| App or website | 4.2% | 2.4% |
| Plan or strategy | 4.0% | 0.9% |
| Code fix / script / config | 3.5 / 3.2 / 1.6% | 4.8 / 1.9 / 2.2% |
| Data or spreadsheet | 3.1% | **13.0%** |
| Educational material | 2.7% | 0.8% |
| Marketing / social content | 2.6% | 2.2% |
| Slides | 2.3% | 0.4% |
| Chart or visualization | 2.3% | 0.2% |
| Résumé / job application | 1.7% | 0.1% |
| Creative writing, maths, academic paper, translation, images, UI mock-ups, recipes, brainstorms, blog posts, SQL, games, video | 0.2–1.5% each | |

**What this means for the app:**
- **Documents and answers are about a third of all outputs.** A desktop app has to render, edit, save and export documents well, not only code.
- **Code is a minority of chat use**, under 10% across fix, script and config. kell today is code-first.

### A3. The biggest request topics (share of all work conversations)

The detail is in [office_procedures.md](office_procedures.md). Outside coding, the largest are:

| Topic | Share of work conversations |
|---|---|
| Business operations | 13.5% |
| Structured extraction | 6.2% |
| Promotional writing | 5.9% |
| Starting a business | 3.3% |
| Company and deal research | 3.2% |
| Workplace writing | 2.7% |
| Taxonomy tagging | 2.5% |
| Customer support | 2.5% |
| Prompt engineering | 2.4% |
| CVs | 2.3% |
| Data analysis | 1.9% |
| Slide decks | 1.7% |
| CRM | 1.5% |
| Meeting intelligence | 1.5% |
| Translation | 1.1% |

### A4. What users say they do and want

- **Daily work (non-coders on Reddit):**
  - data scraping from websites;
  - meeting summaries;
  - PowerPoint creation ("surprisingly good");
  - app prototypes;
  - file and app automation with PowerShell or Python;
  - promotional text, tagging and naming;
  - Excel macros;
  - "giant static HTML reports".
- **Personal (Reddit):**
  - a scheduled rental hunt that runs twice a day and emails a digest;
  - grocery planning, training and finances;
  - **searching all past chats.** People export their data to grep it because the app can't.
- **Survey of 81,000 users:** the biggest gains come from doing *new* tasks (more scope). Worries about job displacement are higher among early-career users.

### A5. What users complain about (problem clusters, posts, reviews)

| Complaint | Evidence |
|---|---|
| Usage limits drain unpredictably and lock people out mid-work | 20+ posts; Anthropic gave everyone one free reset on 2026-11-10 after a limits bug; Reddit threads from Jan, Mar and Aug 2026 |
| Upgrades break working setups | 36 posts from 34 people |
| Runs out of context on real work; drifts in long sessions | 10 posts; Cowork reviews ("lost context fairly quickly") |
| Agent deleted files and said it hadn't | Cowork review, Feb 2026 |
| Connectors, plugins and skills fail about 25% of the time | Cowork review |
| Cowork never starts on Windows 11 Home (needs Hyper-V), with no error shown | LinkedIn review, Mar 2026 |
| Lock-in makes switching costly | 11 posts |
| Hard to tell which of many parallel tasks needs me | GitHub feature requests #108324 (decision queue), #110330 (layouts, agent status board), #109940 (a desktop "pet" status companion) |
| Chat column too narrow on wide monitors | #39712 |
| No multiple windows | #90885 |
| Invalid structured output | 10 posts |
| Confidently wrong answers; buggy code that's hard to review | 7 and 11 posts |
| Personalisation weaker than ChatGPT's | Reddit, Nov 2026 |

### A6. What the latest apps ship (the bar, November 2026)

**Claude Desktop v5.11492 (2026-11-13):**
- **One Claude:** chat and Cowork merged into one composer, so there's no mode choice. The Code tab is kept as its own space.
- **Cowork:** works in folders and apps you choose; shows its plan first; runs long tasks in a cloud VM, and keeps going after you close the laptop.
- **Scheduled tasks** showing run status ("Last run failed", "Needs input").
- **Dispatch:** sends tasks from your phone to your desktop.
- **Computer use** (beta). It tries tools in order: connectors first, then the browser, then the screen.
- **Claude in Chrome:** a side panel that acts on logged-in sites, works across multiple tabs, can be scheduled, and reads console logs.
- **Connectors and the marketplace:**
  - the most-used: Drive, Gmail, Calendar, Canva, Notion, M365, Figma, Slack, Atlassian, HubSpot;
  - interactive connectors that open the tool's real editor full-screen;
  - 342 plugins, including role plugins for Sales, Legal, Finance, Data, Support, PM, Marketing and HR.
- **Skills and Projects.**
- **Memory:**
  - built from chats, which you can review and edit;
  - import from ChatGPT and other assistants;
  - search across past chats;
  - incognito chats.
- **Artifacts** with templates (Docs, Slides, Design, Data, Sheets, Whiteboards, Motion), shareable, with on-canvas editing.
- **Quick entry** on Mac: double-tap Option opens a floating window that can take a screenshot or window capture, and Caps Lock starts dictation.
- **Voice:** dictation and voice mode.
- **Claude for Excel, PowerPoint, Word and Outlook**, sharing one context across the four apps.
- **Claude Meetings:** a bot that joins Google Meet calls and builds docs during the call.
- **Claude Design and Claude Science**, the latter with reproducible artifacts and a background reviewer.
- **Personal Finance:** read-only bank links through Plaid.
- **Health records** (HIPAA-ready).
- **A monthly "Reflect" recap**, break reminders and quiet hours.
- **Enterprise:**
  - MDM and policy configuration, folder allowlists;
  - SSO and SCIM, audit logs, the Compliance API, inference hooks;
  - blocking personal accounts on managed computers;
  - spend tracked by cost code.
- **Code tab:**
  - parallel sessions in worktrees, a pane layout, terminal and file editor;
  - side chats, diff review, app preview, PR and CI monitoring;
  - auto-continue when limits reset.

**ChatGPT desktop:**
- Chat, Work and Codex in one app;
- "dots": always-on project agents with their own computer;
- scheduled and watch tasks; images; Sites.

**Gemini desktop:**
- Alt+Space to summon over any app;
- dictation into *any* app;
- background tasks you approve with one tap;
- Google apps built in; image and video generation.

**Perplexity Comet:** parallel background browser assistants, scheduled, with @-tab context.

### A7. Claude on the web (claude.ai)

**How the data covers the web.** The usage figures in A1–A3 are for "Claude chat + Cowork", which Anthropic reports as *one* number across web, desktop and mobile. So they already include what people do on claude.ai, but there is no web-only split. What the web adds is scale and a different way of working:

- **The web is where most Claude use happens:**
  - claude.ai had about **1.15–1.25 billion visits in October 2026** (Semrush, AlphaMoat), up ~18–24% month on month and ~540% year on year (Similarweb, August).
  - The average visit lasts **12 minutes** at 5.6 pages; the bounce rate is 31%.
  - About **95% of web visits come from desktop computers**, not phones, so people with a desktop app installed still work in a browser tab.
  - 74% of visits are direct; 98% of search clicks are for the brand name. People come back on purpose; they don't find it by searching.
- **Claude users write the longest prompts and hold the longest conversations** of any major assistant (Similarweb, September 2026). The web has to handle long documents and long threads well.
- **For comparison:** the mobile app had 21 million downloads in 30 days; the Chrome extension has 21–23 million users. Claude had about 245 million monthly active users in June 2026 (a secondary estimate).

**Why people stay on the web** (reviews, Reddit, comparison posts, 2026):
- no install, and it works on locked-down work computers;
- it's safer: a browser tab can't reach local files, so a prompt injection can do less damage;
- the desktop app's frequent auto-updates get in the way;
- history, Projects, memory, skills, web search, artifacts and cloud connectors (Drive, Gmail) work the same on the web.

**What only the desktop app can do** (Anthropic's comparison and reviews):
- folders on the computer;
- local MCP servers;
- computer use;
- browser control;
- the system-wide hotkey.

**What the web has in November 2026:**
- one merged chat-and-Cowork composer;
- a model picker and extended thinking;
- web search with citations; research;
- uploads (PDF, Office, images, CSV);
- custom styles;
- Projects; memory and chat search; incognito;
- artifacts with templates (Docs, Slides, Design, Data, Sheets, Whiteboards, Motion), editable on the canvas, **shared by link (private by default), co-edited with the team**, and exported to PowerPoint, Google Slides, Excel, CSV, PDF, MP4 and HTML;
- Google Docs, Sheets and Slides edited live in a pane beside the chat;
- cloud connectors and plugins; interactive connectors that open the tool's real editor;
- Cowork in the cloud, which keeps running after you close the tab;
- scheduled tasks; voice mode and dictation;
- Personal Finance; health records; the Reflect recap;
- Claude Meetings.

**Takeaway for kell.** The web is where people spend their time, and it's the safer, zero-install surface. The desktop app is for the work that has to touch the computer. kell's website today is rooms, Ways and an MCP console, not an assistant you can just ask. See B20.

---

## Part B — The requirement list

Columns: **Requirement** · **Why** (evidence above) · **Claude Desktop, Nov 2026** · **kell 0.1.25** · **Priority**.

### B1. Getting in and first use

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Installers for Win/Mac/Linux; Windows ARM64 | Every platform; Windows Home breakage is a top complaint | ✅ (Linux beta) | 🟡 Windows NSIS only | P1 |
| Clear system-requirement check before setup, with an error instead of a silent hang | Cowork hung on Windows Home | 🟡 | ❔ | P0 |
| Sign in once; the same account on web and mobile | Work follows you across devices | ✅ | ✅ one sign-in for coding lane and rooms | P0 |
| Auto-update with release notes, and rollback | Upgrades break setups | ✅ | 🟡 auto-update, no rollback | P1 |
| **Start without a folder**: plain chat, ask anything | Answers, advice and explanations are 34% of outputs; personal use is 39% | ✅ | ❌ "kell works in one folder at a time" | **P0** |
| Onboarding that shows 3–5 real tasks for *this* person's job, not a blank box | Users "don't know what to use it for" | 🟡 Academy, templates | ❌ | P1 |
| Import chats and memory from other assistants | Lock-in; Claude's import tool | ✅ memory import | 🟡 imports Claude Code / Codex / opencode chats; no memory import | P1 |

### B2. Conversation core

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Streaming replies with stop at any moment | Basic trust | ✅ | ✅ `/api/stop` | P0 |
| Edit and resend a message; regenerate; branch | Task iteration is 30% of collaboration | ✅ | 🟡 fork session ✅; edit/regenerate ❔ | P0 |
| Side question without derailing the task (`/btw`) | Keeps the main thread clean | ✅ side chats | ✅ `/api/btw` | P2 |
| Markdown, tables, code, LaTeX maths, Mermaid diagrams | Maths, charts and docs outputs | ✅ | 🟡 Markdown ✅; LaTeX/Mermaid ❔ | P1 |
| Copy a message; copy a code block; copy as rich text | Constant use | ✅ | ✅ MessageActions | P0 |
| **Search across all past chats** | Users export their data to grep it | ✅ RAG search | ❌ no search | **P0** |
| Chat list with rename, pin, archive, delete, folders and projects | Many parallel chats | ✅ | 🟡 sessions per project; no search, pin or folders | P1 |
| Incognito / temporary chat (no memory, no history) | Privacy; health and finance questions | ✅ | ❌ | P1 |
| Export a chat (Markdown/PDF) and all data | Lock-in complaint; ownership | ✅ | 🟡 project export ✅ | P2 |
| Wide-screen layout; adjustable reading width | GitHub #39712 | 🟡 | ❔ column widths saved | P2 |
| Multiple windows; split view of sessions | #90885, #110330 | ✅ split view (Code) | ❌ | P2 |

### B3. Files, documents and artifacts (the biggest output group)

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Attach files by drag and drop or paste: PDFs, images, Office files, CSV | Inputs to most tasks | ✅ | 🟡 attach by path (metadata); reads only inside the project | P0 |
| **Create real files: .docx, .xlsx, .pptx, .pdf, .csv, .md**, saved to a folder you choose | Documents 14.7%, slides 2.3%, spreadsheets 3.1% (13% via the API) | ✅ skills + Cowork | ❌ only what the coding engine writes as text | **P0** |
| **Render outputs beside the chat**: document preview, spreadsheet grid, slide preview, chart, HTML page | "See the work"; artifacts | ✅ artifacts with templates | ❌ | **P0** |
| Edit the artifact in place (on-canvas edits, "change this section") | Iteration is 30% of collaboration | ✅ | ❌ | P1 |
| Version history of an artifact, with restore | Long iterations | ✅ | 🟡 per-turn undo for project files | P1 |
| Share an artifact as a link or file | "Put in front of someone" | ✅ | ❌ (Rooms canvas partly) | P2 |
| Charts from data (native chart objects, not images) | Charts 2.3%; data analysis 1.9% | ✅ | ❌ | P1 |
| Read scanned PDFs and images (OCR, vision) | Structured extraction 6.2% | ✅ | ❔ engine model-dependent | P1 |
| Templates the person or company supplies (deck template, letterhead, style guide) | "Use my template" | ✅ | ❌ | P2 |

### B4. Doing the work: agentic tasks

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Plan shown before acting, and editable | Users want control | ✅ | 🟡 delegation plan and Way graph ✅; no editable plan | P1 |
| Live step list with what it's reading, running and deciding | "See the work as it happens" | ✅ | ✅ tool chips and model steps | P0 |
| Work in a chosen folder; can't reach outside it | Folder is the safety boundary | ✅ | ✅ project folder | P0 |
| **Sandboxed execution** (VM or container) for code the agent runs | Safety; deleted-files story | ✅ cloud/local VM | ❌ runs on the host | **P0** |
| **Deletion protection and undo for every file change** | "It deleted my files" | 🟡 asks before delete | ✅ snapshots + Undo of the last turn | P0 |
| Runs while the laptop is closed (cloud runs) | "Claude works when you don't" | ✅ | ❌ local only | P2 |
| Parallel tasks and a status board (working / needs you / done) | #108324, #110330 | 🟡 sidebar statuses | 🟡 sessions list, Rooms "My runs" | P1 |
| **One decision queue** across all tasks: approvals, questions, reviews | #108324 | ❌ (requested) | ❌ | P1 (differentiator) |
| Goal / completion condition checked after each turn (`/goal`) | Long tasks drift | ✅ Code | ✅ `/api/goal` | P2 |
| Reusable procedures (Ways / skills) with a picker and a graph | Repeatable work | ✅ skills, plugins | ✅ Ways, procedure graph, Submit a Way | P1 (kell strength) |
| Ask-before-acting modes: ask every time / ask for risky / auto with a classifier | Trust | ✅ | 🟡 once / always / reject per permission | P0 |
| Budget and limit awareness *before* a long task (cost estimate, "this may stop at step 6") | Limits are the top complaint | 🟡 | ✅ low-balance warning, paused-run message (0.1.25) | P0 |
| Auto-continue after a limit resets | Code changelog | ✅ | ❌ (run resumes on "continue") | P2 |

### B5. Tools, connectors and the web

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| **Built-in web search and fetch with citations** | Research is 8% of work; news in the morning | ✅ | 🟡 via kell tools (Exa / search MCP) when routed; not a visible feature | **P0** |
| Connectors for the top 10: Drive, Gmail, Calendar, Notion, M365, Slack, Atlassian, HubSpot, Canva, Figma | Marketplace "most popular" | ✅ | 🟡 tool routing (MCP registry, `via_kell` servers); no one-click connectors | P1 |
| Custom MCP servers (local and remote) | Power users, companies | ✅ | ✅ engine MCP plus kell MCP | P1 |
| Tool suggestions when a task needs one ("add Gmail?") | Discoverability | 🟡 | ✅ tool cards | P2 (kell strength) |
| **Browser the agent can drive** (logged-in sites, forms, multi-tab) | Internal dashboards, vendor portals; 23M Chrome extension users | ✅ Chrome extension | 🟡 plain browser pane, not agent-driven | P1 |
| Computer use (screen, mouse, keyboard) as a last resort | Apps with no API | ✅ beta | ❌ | P2 |
| Open the connected tool's real editor beside the chat | Interactive connectors (Nov 2026) | ✅ | ❌ | P3 |

### B6. Always at hand: desktop presence

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| **Global hotkey for a floating quick window** over any app | Gemini Alt+Space, Claude double-tap Option | ✅ (Mac only) | ❌ no globalShortcut | **P0** |
| Screenshot or share a window into the chat | Visual context | ✅ | ❌ | P1 |
| Tray / menu-bar icon; runs in the background | Long tasks, schedules | ✅ | ❌ | P1 |
| **Native notifications**: done, needs you, failed, with deep links | Parallel tasks | ✅ | ❌ no Notification use | **P0** |
| Launch at login (optional) | Background schedules | ✅ | ❌ | P2 |
| Keep the computer awake during a task | Long runs | ✅ | ❌ | P2 |
| Open files and links in the right native app | Daily | ✅ | 🟡 `shell.openExternal` | P1 |
| Drag files out of the chat into Finder/Explorer and other apps | Daily | ✅ | ❔ | P2 |

### B7. Voice and other ways in

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Dictation in the composer | Long prompts; accessibility | ✅ | ❌ | P1 |
| Dictation into *any* app (system-wide) | Gemini "think out loud to any app" | ❌ | ❌ | P3 |
| Voice conversation mode | Hands busy | ✅ | ❌ | P2 |
| Image input (paste a screenshot) | Visual questions | ✅ | 🟡 attach images by path | P1 |
| Image generation | Claude doesn't do it; ChatGPT and Gemini do; 0.8% of outputs | ❌ | ❌ (route to a tool) | P3 |

### B8. Memory, personalisation and projects

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| **Memory across chats** that you can see, edit and turn off | "ChatGPT knew me better" | ✅ | 🟡 kell memory notes (`memory_search` tool); no visible memory UI | **P0** |
| Projects: instructions plus knowledge files shared by every chat in the project | Re-explaining context | ✅ | 🟡 project folder + AGENTS.md; no knowledge upload | P1 |
| Personal profile: name, role, tone, language | Personalised answers | ✅ | ❌ | P1 |
| Memory scoped per project, with no bleed between clients | Claude memory design | ✅ | ❔ | P1 |
| Monthly recap, break reminders, quiet hours | Wellbeing (Reflect) | ✅ | ❌ | P3 |

### B9. Scheduling and routines

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| **Scheduled tasks** (daily digest, weekly report, watch a page) | Reddit rental hunt; ChatGPT, Comet and Claude all have them | ✅ with run status | ❌ | **P1** |
| Run status, retries and "needs input" on each schedule | Claude v5.1231 | ✅ | ❌ | P1 |
| Triggers (a new email, a file dropped in a folder) | "Agents on triggers" (Reddit) | 🟡 | ❌ | P2 |
| Results delivered by notification, email or room | Digest pattern | ✅ | 🟡 rooms | P2 |

### B10. Phone and remote

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Send a task from your phone to your desktop | Dispatch | ✅ | 🟡 Rooms → desktop sessions (db/208), web only | P1 |
| See progress and approve from the phone | Approvals anywhere | ✅ | 🟡 rooms page | P1 |
| Continue a desktop chat on another device | "Your work follows you" | ✅ | ❔ | P2 |

### B11. Office-app work

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Edit open Excel, Word and PowerPoint files with formulas, tracked changes and templates kept | M365 add-ins; workplace writing, slides | ✅ add-ins | ❌ | P2 (via files first, add-ins later) |
| Email: triage, draft replies that wait for you to send | Business correspondence 2.0%; Outlook add-in | ✅ | ❌ | P2 |
| Meeting notes and action items from a transcript or recording | Meeting intelligence 1.5% | ✅ Meetings bot | ❌ (a Way can do it from a transcript) | P1 via Way |

### B12. Collaboration

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Shared rooms or channels with people and agents | Team work | 🟡 Meetings, Slack | ✅ Rooms (kell's own) | P1 (kell strength) |
| Share a chat or artifact by link, with permissions | Hand-offs | ✅ | ❌ | P2 |
| Shared projects and skills for a team | Consistency | ✅ | 🟡 Ways are shared publicly | P2 |

### B13. Coding (kell's current strength)

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Parallel sessions in worktrees | Code tab | ✅ | 🟡 sessions; no worktree isolation per session | P1 |
| Diff review per turn, with undo | Trust | ✅ | ✅ diff + undo + other changes | P0 |
| Terminal, browser preview, file view | Basics | ✅ | ✅ (0.1.x bottom panel) | P1 |
| PR and CI monitoring | Code tab | ✅ | ❌ | P2 |
| Model routing to cheaper models when they suffice | Cost; limits | ❌ (manual) | ✅ delegate / routing (kell's core) | P0 (kell strength) |

### B14. Trust, safety and privacy

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Prompt-injection defence for web, email and docs content | Agents read untrusted text | ✅ classifiers | 🟡 kell flags injections in tool results | P0 |
| Approval for send, pay, delete, publish and share — never silent | Cowork default | ✅ | ✅ permission cards | P0 |
| Clear data policy in the app: what is sent where, training opt-out | Health and finance questions | ✅ | 🟡 | P1 |
| Secrets never in chats or logs; key storage in the OS keychain | Basic security | ✅ | ❔ | P0 |
| Local-only mode / your own key / your own model | Lock-in; privacy | 🟡 enterprise 3P | ✅ own-key lane, local models via the engine | P2 (kell strength) |

### B15. Usage, limits and cost (the top complaint)

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| **Always-visible usage meter** (what's left, when it resets) | Top complaint cluster | 🟡 Settings › Usage | 🟡 wallet / credit | **P0** |
| Cost per task shown after it runs | Predictability | ❌ | ❔ | P1 (differentiator) |
| Warning before a long or expensive task | Lock-outs mid-work | ❌ | ✅ (0.1.25, long Ways) | P1 |
| Explain why usage jumped (model, context size, caching) | "Used 4 hours of limit in one prompt" | ❌ | ❌ | P1 (differentiator) |

### B16. Reliability

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| History that survives engine and app upgrades | "Upgrades break setups"; Office add-in lost history | 🟡 | ✅ own chat store (localdb) | P0 |
| Context management: auto-compact, a context meter, "start fresh with a brief" | Drift, lost context | ✅ | ❌ no context meter | P1 |
| Visible errors with a fix suggestion (no silent hang) | Windows Home story | 🟡 | 🟡 | P0 |
| Resume after crash or reboot | Long tasks | ✅ | 🟡 runs resumable via kell | P1 |

### B17. Accessibility, language and comfort

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Full keyboard use; screen reader labels; focus order | Accessibility | ✅ | ❔ | P1 |
| Font size and zoom; high contrast; light/dark/system theme | Comfort | ✅ | 🟡 theme + font menu | P1 |
| Many interface languages; answers in the user's language | Global users (Clio language patterns) | ✅ | ❌ English UI | P2 |
| Keyboard shortcut sheet | Power users | ✅ | 🟡 a few accelerators | P2 |

### B18. Personal-life uses (39% of chats; about 50% at weekends)

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| Health questions with optional records, private by default | Medical questions top the weekend list | ✅ health records | ❌ | P3 (regulated) |
| Personal finance, read-only | Investment advice at weekends; Personal Finance beta | ✅ US | ❌ | P3 |
| Learning mode (explain, quiz, study plans) | 16% coursework; learning is 16.6% of collaboration | ✅ | ❌ | P2 |
| Emotional-support safeguards (crisis resources, no dependency) | Weekend spike | ✅ | ❌ | P1 if chat opens to everyone |

### B19. Admin and company rollout (later, for team plans)

| Requirement | Why | Claude | kell | Pri |
|---|---|---|---|---|
| SSO/SCIM, audit log, data retention | Enterprise | ✅ | ❌ | P3 |
| MDM policies, folder allowlist, blocking personal accounts | Managed computers | ✅ | ❌ | P3 |
| Spend by user, team and project | Cost codes | ✅ | ❌ | P3 |

### B20. The web app (claude.ai vs kell's website)

kell's website (`routing_kell_frontend`) today has:
- Rooms: group chat for people and agents;
- Submit a Way and My runs;
- the MCP console: install kell into your tools, model keys, try a model, watch the router;
- account pages (billing, connections, projects, profile);
- docs, download and pricing.

Columns: **Claude web** · **kell web**.

| Requirement | Why | Claude web | kell web | Pri |
|---|---|---|---|---|
| **A personal assistant chat in the browser:** ask anything, no install | Most Claude use is on the web (≈1.2B visits a month, 12-minute visits); locked-down work computers | ✅ | 🟡 Rooms chat is group chat with agents; no one-to-one assistant | **P0** |
| Same account, history and memory on web, desktop and phone | "History is tied to the account, not the interface" | ✅ | 🟡 one sign-in; desktop chats live in the desktop's local store and don't show on the web | **P0** |
| Long documents and long threads stay fast | Claude users write the longest prompts and threads | ✅ | ❔ | P1 |
| Uploads: PDF, Office, images, CSV | Inputs to most tasks | ✅ | 🟡 Rooms file upload (`room_file`) | P1 |
| Web search with citations; research | Research is 8% of work | ✅ | ❌ on the web | P1 |
| Model picker; model routing visible to the person | Choice and cost | ✅ picker | 🟡 console "try a model" and router view; not in a chat | P1 |
| Artifacts with a share link (private by default), co-editing, export to Office/PDF/HTML | Hand-offs; the web is where sharing happens | ✅ | ❌ (Rooms canvas partly) | P1 |
| Edit Google Docs, Sheets and Slides in a pane beside the chat | Docs live in the cloud | ✅ | ❌ | P2 |
| Cloud connectors added once, usable on any surface | Drive and Gmail work on the web too | ✅ | 🟡 `account/connections` lists allowed MCP clients; no connector catalogue for the person | P1 |
| Run Ways from the browser, with person steps and My runs | kell's Ways | ✅ (skills, plugins) | ✅ Submit a Way, Run, My runs | P1 (kell strength) |
| Cloud runs that continue after the tab is closed | Long tasks; scheduled work | ✅ Cowork in the cloud | 🟡 headless Way runs are single model calls without tools | P1 |
| Scheduled tasks managed on the web | Digests and reports | ✅ | ❌ | P1 |
| Incognito chat; data controls; delete and export my data | Privacy | ✅ | ❔ | P1 |
| Works in any browser, responsive down to tablet; keyboard and screen reader | Reach; accessibility | ✅ | ❔ | P1 |
| Installable PWA, or "open in desktop app" hand-off | People switch surfaces | 🟡 | ❌ | P3 |
| Usage and cost meter on the web too | Top complaint | 🟡 Settings › Usage | 🟡 billing page | P0 |

---

## Part C — The gaps that matter most for kell, in order

Every P0 that is ❌ or 🟡 for kell:

1. **Chat without a folder.** A third of use is answers, advice and explanations, and about 40% is personal. Today kell can't be used for any of it.
2. **Make and show real documents:**
   - create .docx, .xlsx, .pptx and .pdf files;
   - preview them beside the chat;
   - export them.

   This covers the largest output group: documents, reports, spreadsheets and slides.
3. **Search across all past chats.** kell already has its own chat store (localdb), so this is close.
4. **Global hotkey and quick window, plus native notifications and a tray icon.** These make it a desktop app rather than a window.
5. **Visible web search with citations** as a built-in, not only when routed.
6. **Memory you can see and edit**, with a personal profile.
7. **Sandboxed execution** for anything the agent runs, so code and scripts don't run on the bare host.
8. **An always-visible usage and cost meter**, with cost per task and an explanation when usage jumps. This is the single biggest complaint about Claude, and kell's routing and wallet are well placed to beat it.

**On the web (B20):**
- **A personal assistant chat on kell's website**, sharing account, history and memory with the desktop. Desktop chats live only in the desktop's local store today.
- **Web search, artifacts with share links, and cloud runs** that keep going after the tab closes.

The web is where most Claude use happens (about 95% of it from desktop browsers). So a kell that exists only as a desktop app plus rooms misses the main surface.

**Then the P1s:**
- scheduled tasks with run status;
- phone-to-desktop tasks and approvals (from Rooms);
- the decision queue;
- connectors for the top 10 tools;
- an agent-driven browser;
- dictation;
- screenshot capture;
- projects with knowledge files;
- a context meter and compaction;
- accessibility.

**Where kell is already ahead and should lean in:**
- model routing, so cheaper models handle what they can;
- Ways: reusable, tested procedures with a graph and approvals that can say no;
- tool suggestions;
- Rooms, for people and agents together;
- the low-balance and paused-run handling;
- history that survives upgrades;
- an own-key / local-model lane.

**What to avoid (from the complaints):**
- silent hangs;
- limits that run out without warning;
- deleting without a gate;
- upgrades that lose history;
- "done" notifications for runs that skipped a step;
- a narrow chat column on wide screens.

# Open problems, 2026-11-05

Everything that came up in the chat and agent work of 2026-11-04 and 2026-11-05 that is not finished, with what is known about each. "Fixed, not deployed" means the
change is in `origin/main` but the Railway service still runs the older build (last deploy `5a4d8ac6`). Nothing below was deployed.

## A. From the 2026-11-05 basics check (a signed-in browser run of 17 checks; 6 worked, 5 broken, 2 not tested)

| # | Problem | Status | What is known |
| --- | --- | --- | --- |
| 1 | Stop did not stop the work; kell finished the essay and posted it later as a canvas | Fixed, not deployed (`c30277fa`) | The server keeps an answer running after the browser disconnects (so a closed tab does not lose a paid-for reply), and Stop only closed the connection. Now `POST /v1/rooms/{id}/agent/stop` cancels the person's own running answers. Not seen working in a real browser. |
| 2 | First message in a new room got "This room is still syncing" and was never answered | Fixed, not deployed (`c30277fa`) | The ask now waits up to 20 s for the room's id, nudging the sync. Not seen working in a real browser. |
| 3 | A LaTeX formula showed as raw `$$A = \pi r^2$$` | Fixed, not deployed (website) | `$$..$$`, `\[..\]`, `$..$`, `\(..\)` go through KaTeX. |
| 4 | The code block had no Copy button | Fixed, not deployed (website) | Language label and a Copy button. |
| 5 | Checklist items showed as bullets with `[]` | Fixed, not deployed (website) | Disabled checkboxes. |
| 6 | PDF and image attachments were not read (kell saw only the file name) | Fixed by another session (`147ad779`), not deployed | PDF, Word and text files are read into the agent's instructions; a picture goes to the model as an image, with a text-only fallback. Needs a backend deploy. |
| 7 | "Instructions for every chat" had no effect ("What is 2+2?" got "4", no BANANA) | Fixed by another session (`ae298e06`), not confirmed | The setting is kept on the device and sent with every ask. Re-run the check after the deploy. |
| 8 | Image generation: kell said it drew a fox logo but nothing appeared | Fixed, not deployed (`460d472d`); real image never seen | The room agent dropped every picture a tool returned ("images stay out of a room message for now") while the model still claimed success. Pictures are now stored as room files and their card is in the answer. Tested with fakes only. The image provider's balance or key could still make the real call fail. |
| 9 | "Thinking" with no text for over a minute on a long essay | Partly fixed, not deployed (`460d472d`) | The live bubble now says "Writing it now. A long piece takes about a minute." The wait itself is unchanged: a long piece is one non-streaming cheap-model call. |
| 10 | Slow first words: about 10 s on "What is today's date?", 9 to 18 s on short replies | **Open** | See "Slow first words" below. |
| 11 | A Python function was put in a canvas instead of the chat | **Open** | See "Code in a canvas" below. |
| 12 | The web search answer's link text did not match the headline quoted, and the link looked like plain text | **Open, not reproduced** | A bare web address renders as a clickable link in a test. The mismatch is the model quoting one story and linking another; nothing in kell checks that a link supports the claim. |
| 13 | Room name shown as "Basics-check-c" although typed lowercase | **Open, minor** | Display capitalisation. |
| 14 | A reply ("ok") was mixed in with the late essay replies | Probably a consequence of #1; re-test after the deploy | |
| 15 | The browser extension stopped responding mid-test | **Open, cause unknown** | A canvas that kept rewriting itself to about 2,000 words may have loaded the page. A guess. |
| 16 | Tool sign-in card and "runs again once" could not be tested (Scite was already connected) | **Not tested** | Another session changed this (`9ecbb0ac`: one card per request, listing each tool). Test with a tool that really needs a sign-in. |
| 17 | Memory across rooms, and the desktop app | **Not tested** | Memory was off; the app was not checked. |

### Slow first words (open)

Before the first word is streamed, `_prepare` in `backend/app/services/room_agent.py` waits for the tool router and for the triage judge (up to 5 s) at the
same time. Then, for most messages of three or more words, the first model round is forced to be a tool search, which is a whole extra model call plus the
search, before the real answer starts. For "What is today's date?" none of that helps (the date is in the system prompt).

Plan that was about to be applied (not yet written): a conservative rule `plainly_answerable(text)`: a short message starting with what / who / when / why /
how to / explain / define / translate / convert / calculate / summarise / rewrite / "show a ..." / "write a function ...", and containing none of: news,
headlines, latest, current, weather, prices, search, links, files, pdf, calendar, email, apps, pictures, sources, "right now", tools, canvas, slides,
document. For those, skip the router, the judge and the forced search. Everything else keeps the full path. Needs tests that "what is one top news headline in
India today?" still searches first. Measure before and after on the deployed service; the 10 s figure is from one run.

### Code in a canvas (open)

Most likely cause (not confirmed, because the model's tool calls were not captured): the model called `call_model` for the code and gave it a `canvas_title`;
`_run_call` then puts any call_model result with a title (or over 700 characters) in a canvas. Plan: when the request is not a "make something" request and the
result is short (about 1,200 characters or less) and not a page, return the text to the model to include in its chat answer instead of making a canvas. Needs
the "is this a make-something request" flag passed into `_run_call`. The new Copy button in chat code blocks already covers the copy need.

## B. Things decided or built that still need a person

| Item | State |
| --- | --- |
| Railway deploy of everything pending | Needs the owner's explicit yes. Pending: `de3612f8` (rooms-first server instructions), `db23f877` (`read_earlier`), `c30277fa`/`fb611880` (Stop, first message), `460d472d` (pictures, writing hint), the other session's `147ad779` (files, other rooms), `9ecbb0ac`, `075a4df7` (migration 181), `04984df6` (migration 180), `b9d0f6c0` (3-minute tool calls). |
| The kell stand-in for an offline Claude (`658a2820`) | Pushed; the owner rejected the behaviour ("we wanted claude to answer even when it is unavailable"). Should be taken out before the deploy, or the deploy carries it. |
| Claude answering while not running | Only possible as a kell-run Claude on the person's own Anthropic key (stored encrypted, never a Claude subscription). Waiting on a yes or no. |
| Tool OAuth (kell signs in to tool servers for the person) | `TOOL_GATEWAY_OAUTH=on` and `ORG_KEY_ENCRYPTION_KEY` are set on Railway, but the running server was last deployed before they were set. Migrations 177 and 178 not confirmed on production. Security review still needed before it is on for everyone. |
| Team subscription (Teams, 1,800 rupees per seat a month) | Not ready to sell. The plan card says "Not available online yet". The team admin pages are behind `NEXT_PUBLIC_ORG_ADMIN` and work locally against the real routes, but those routes (`/v1/orgs/...`) are only in the full backend app, not in the hosted server (production answers 404). Needs the hosted server to mount them, the site flag, a test-mode Razorpay purchase of three seats, GST and terms. Role changes and member removal are not built ("isn't available yet" on the page). |
| Local demo of the team pages | Running at `http://localhost:3111/org` with a temporary sign-in edit in `routing_kell_frontend/lib/session.ts` (marked `TEMP-LOCAL-DEMO`, uncommitted). Must never be committed. Revert it and stop the local servers when done. |
| Exa MCP server | Added to the user config with a key in the URL. If that key was pasted into chat, rotate it. |
| Claude in Chrome | The extension was installed but its tools were not available to the Claude Code session; the checklist was run by pasting it into the extension's own panel. |

## C. Design work not started

- **Leases, heartbeats and a reaper for delegated tasks** (`delegate_tasks`): a claimed task has no lease, so a Claude session that stops leaves it stuck.
- **Per-task delivery states and claims on canvases** for concurrent agents (from AgentRoom and agent-chat-room).
- **Webhook push for agents that have a public URL**, with signed deliveries and polling as the fallback.
- **Marketplace** (pay once in kell, creators list tools and get paid): ledger, creator onboarding through Razorpay Route linked accounts, a tool review pipeline, per-tool spending limits and confirmation for tools that act for the user, refunds and payouts, GST and terms. A design doc has not been written.
- **End-to-end encrypted private rooms** (MLS-style, agents as key-holding members): design only; kell-run `@kell` and server-side memory would not work in those rooms.
- **Server-side memory with background "dreaming"**, notes and knowledge: designed in the plan, built in part by another session; not verified here.
- **HTN, semantic web and open peer review** (goal-procedure-claim records exported to nanopublications, RO-Crate and verifiable credentials): research only.
- **A search dashboard of tools and models** exists (`docs/dashboard/tools-models.html`); it does not search kell's own tool catalogue.

## D. Not verified in this work

- Sync between two real devices.
- Whether the "clear this device" reset now re-downloads everything (the reset exists in the code; not tried).
- Any of the fixes in section A on the live site: all of them need the deploy first.
- Real image generation, real PDF and image reading, and the new Stop button against production.

# kell on Claude web, end to end

Two parts. Part A (lookup) can be tried as soon as sign-in works. Part B (delegating from our site to a Claude chat) needs the relay described in `.scratch/prompts/delegator_pull_brief.md`, which is not built yet; this page says what you will see once it is.

Server: `https://mcp.kell.org.in/mcp`
Site: `https://www.kell.org.in` (`/connect` shows the same address and the instruction below)

## Before you start (check each once)

1. Supabase: Google and GitHub providers work (done 2026-10-28), authorization path is `/oauth/consent`, Site URL is `https://www.kell.org.in`.
2. The site is built with the Supabase URL, its public key and `NEXT_PUBLIC_KEL_MCP_URL`. Check: `https://www.kell.org.in/oauth/consent` must not say "Sign-in isn't connected in this build".
3. A first sign-in has worked from Claude Code: `/mcp` -> `kell-hosted` -> Authenticate -> Allow, then `kell-hosted` shows Connected.
4. claude.ai: a paid plan. On Team or Enterprise an owner adds the connector for the organization first.

## Part A: add kell to claude.ai and use the lookup

1. claude.ai -> Customize (left sidebar; Settings -> Connectors now only points there) -> Connectors -> Add -> Add custom connector.
2. Name: `kell`. MCP server URL (the full address INCLUDING `/mcp`; the bare domain answers 405 and the check fails): `https://mcp.kell.org.in/mcp`. Continue. claude.ai finds the sign-in itself: keep "Sign in now" and "Register automatically" (both marked Detected), then Add.
3. Choose Connect. A kell page opens: sign in with Google or GitHub, then press Allow. The connector page then lists four tools: Find proven ways, Delegate to a cheap model, Find more tools, Run a discovered tool.
4. On that page, set the tool permissions to **Always allow** for kell (both groups). Otherwise every call waits for a click in the chat.
5. In a new chat: + -> Connectors -> check that `kell` is switched on.
6. Settings -> Account -> **Instructions for Claude**: paste this, then Save changes, so the model uses kell without being asked:

   > When I start a new task, first call the kell tool find_ways with a self-contained statement of the task and installed = the names of my enabled connectors. Then follow its reply. If it has a "tools" field: use my connector if one is listed; otherwise do the task as well as you can and offer me the connector_url to add (never claim a tool I don't have). If it has a "delegate" field, or a part of the task is a self-contained summary, explanation, draft, extraction or reformatting, call the kell delegate tool with the task as I put it plus the context it needs (not an outline of the answer) and use the answer only if its check says good; say in one line that it was delegated. Do it yourself for deep multi-step reasoning or anything with private, secret or personal data. Skip kell for thanks and small talk.

7. Try it: "What's the difference between OAuth access tokens and refresh tokens? Keep it short." You should see find_ways, then "Delegate to a cheap model", then an answer that says it was delegated and checked. For a task that needs a tool you lack ("Which open issues on omni-us/jsonargparse mention dataclasses?"), the answer ends by offering a connector with its URL.

Tested end to end on 2026-10-31: six prompts (an explanation, a React 19 summary, GitHub issues, a CVE check, an email draft, "Thanks!") all went as described; the delegated ones ran on glm-5.3-flash, checked by gemma-4-31b-it, about $0.0005 each.

What you should see: a tool call to `find_ways` in the chat, then an answer built from what it returned. The library is nearly empty today, so a `no_match` result is normal; that is not a failure of the connection. If Claude never calls the tool, say "call the kell find_ways tool" once and check the tools menu.

If sign-in fails, note the page it stops on and send it over: a Google/GitHub error is a provider setting; "Sign-in isn't connected in this build" is a site build; a 404 is a path setting in Supabase.

## Part B: delegate from our site to a Claude chat (needs the relay)

Design: the site never starts or controls your Claude. Your Claude chat pulls tasks when YOU ask it to, in your own session.

1. On the site -> Delegator -> Connect an agent -> name it (for example `web-chat`). You get a handle like `@you/web-chat`.
2. On the site, in a room, send: `@you/web-chat summarize the open risks in the file src/auth.ts`. The task shows as queued.
3. In your claude.ai chat (kell turned on), say: "Check my kell inbox for web-chat, do the tasks, and post the results back." Claude calls `delegate_inbox`, works on the task in the chat, then calls `delegate_result`.
4. On the site the task moves to done and the result appears in the room, live.

What you will not see: the site pushing work into Claude on its own. A claude.ai chat acts only when you send it a message. That is also why this stays within Anthropic's terms: it is you, using Claude normally.

Status (2026-10-28): the relay and the two tools are BUILT and live on the hosted server (`docs/delegate_relay_api.md`). `delegate_inbox` was checked through a signed-in claude.ai connector. What is still missing is the Delegator UI against the relay (steps 1 and 2 on the site), which is the Frontend session's work. Until it exists, a task can be sent with a website-session token by calling `POST /v1/delegate/agents` and `POST /v1/delegate/messages` directly (see the API page).

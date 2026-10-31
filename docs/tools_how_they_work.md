# How tools work in kell today (2026-10-31)

How kell finds an outside tool for a task, and what happens next in each case: a tool you already have, kell's own,
one kell runs for you, a free one to add, one that needs a sign-in, a package, a paid one, and a tool that acts for you.
Counts and examples are from the catalog as loaded on 2026-10-31. Design detail:
[tool_routing_plan.md](tool_routing_plan.md), [tool_gateway_design.md](tool_gateway_design.md). What waits:
[revisit.md](revisit.md).

## 1. From a request to a short list

1. **Does this task need a tool, and of which kind?** kell's triage reads the request and names one of 25 goals: `web`,
   `browser`, `docs`, `vcs`, `code_search`, `database`, `cloud`, `ci_cd`, `packages`, `observability`, `design`,
   `messaging`, `files_media`, `payments`, `data`, `generate`, `security`, `tickets`, `workspace`, `analytics`, `crm`,
   `commerce`, `social`, `sandbox`, `ai_models`. Most requests need none ("rename foo", "thanks").
2. **Search the catalog** (48,124 servers, mostly from the official MCP Registry):
   - word search plus meaning search (embeddings);
   - a reranker asked "which server gives a coding agent the ability to <goal>, for this task";
   - then weighting:
     - popularity (GitHub stars);
     - liveness: live, then sign-in needed, then not probed; dead servers are dropped;
     - bulk publishers count less;
     - **outcomes**: how often the tool actually helped on that goal (section 5).
3. **Keep 4**, in this order:
   1. tools you already have;
   2. tools kell can run for you (`via_kell`);
   3. remote servers to add by URL;
   4. packages to install.
4. **Hand them to the model:**
   - **Claude Code:** the hook adds a short "kell tool router" note to the prompt, naming each tool, when to use it, and
     its install command.
   - **claude.ai:** `find_ways` returns a `tools` field. The instruction in the user's profile says what to do with it.

## 2. The cases

What the catalog holds, by how a tool can be reached:

| Case | Servers | Who can use it, and how | Example |
|---|---:|---|---|
| **Already installed** by the user | (theirs) | Used directly; listed first. | Playwright you added to Claude Code; GitHub connector on claude.ai |
| **kell's own** (`kell/images`) | 1 | Anyone signed in. Free to the user, 30 images a day, no consent step. FLUX-1-schnell on DeepInfra, paid by kell. | "make me a logo for this project" |
| **Through kell, no install** (gateway) | 176 | Claude Code and claude.ai: `use_tool(server=..., name=..., arguments=...)`. First use per server shows a one-time consent link. | Context7 (library docs), Compiler Explorer, Firecrawl, GitDiagram |
| **Remote, free, no sign-in**, not on the gateway's list | 20,362 | The user adds it by URL. claude.ai: Customize → Connectors → Add custom connector. Claude Code: the `claude mcp add --transport http ...` command kell shows. | Most registry servers; many are niche data feeds |
| **Remote, needs a sign-in** | 9,827 | The user adds it by URL and signs in with the vendor when adding (OAuth). kell can't run these for them yet (phase 2). | GitHub (official), PostHog, Stripe, Atlassian, Supabase, Apify |
| **Package** (runs on the user's machine) | 14,679 | **Claude Code only** (claude.ai can't run local programs): `claude mcp add <name> -- npx -y <package>`. Often needs the user's own API key in an environment variable. New servers usually appear in the next session. | Chrome DevTools, Figma Context, Serena, Repomix |
| **No way to add** (no URL, no install) | 3,076 | Kept for search but almost never shown. | |
| Dead at the last probe | 4,089 | Dropped from the catalog. | |

### Paid tools

The catalog has **no price field**. A paid tool shows up as "needs a sign-in" or as a package that needs an API key: the
user signs in to (or pays) the vendor, and the vendor bills them. **kell never pays for an outside tool**, and the
gateway doesn't run tools that need an account. The one thing kell pays for is its own image generation, with a daily
cap. Paid tools through kell, charged to the user's kell credits, are on [revisit.md](revisit.md).

### Tools that act for the user (send, post, pay, delete)

- **The gateway refuses them.** A tool is refused when it is marked destructive, or when its name looks like a write:
  send, post, publish, delete, pay, order, update, edit, write, upload, merge, push, and similar.
- **A tool the user installed themselves is theirs to use.** Claude Code and claude.ai ask the user before each call
  (unless they chose "Always allow").
- Write tools through kell, with a confirmation step, are on [revisit.md](revisit.md).

### The gateway's rules

A server qualifies for the gateway (176 today) only if all of these hold:
- it is remote;
- the probe found it live with no sign-in;
- its publisher's namespace is verified in the registry;
- the publisher has at most 30 servers;
- it has 20+ GitHub stars;
- it is not archived and not on kell's deny list.

Each call goes through these checks:
1. **Consent:** the user's one-time consent for that server. Only the SHA-256 of the link's token is stored, and
   consent can be revoked.
2. **Public data only** (`data_class` `PUBLIC_SOURCE`).
3. **Secret scan:** the arguments are scanned for keys and tokens.
4. **Network rules:** https only, public IP addresses only, no redirects, 20 s and 1 MB at most.
5. **Rate limits:** 60 calls an hour per user, 120 a minute per server.
6. **Audit:** every call is recorded in `tool_gateway_calls`.

## 3. Representative tools in the catalog

Picked from the catalog to show its range. ★ = GitHub stars of the linked repository.

**Through kell, no install (gateway):**

| Server | ★ | What it's for |
|---|---:|---|
| `io.github.upstash/context7` | 64k | Current docs for a library or framework |
| `io.github.compiler-explorer/compiler-explorer` | 19k | Compile a snippet and see the assembly or output |
| `com.gitdiagram/gitdiagram` | 19k | A diagram of a GitHub repository's structure |
| `io.github.firecrawl/firecrawl-mcp-server` | 7.7k | Read a web page as clean text |
| `com.cloudflare.mcp/mcp` | 4.4k | Cloudflare's docs and public tools |
| `io.github.tolgee/tolgee` | 4.1k | Translation and localisation help |
| `io.github.timescale/pg-aiguide` | 1.9k | PostgreSQL docs and best practices |
| `io.github.Nutlope/inspo` | 0.9k | Real production sites: palettes, fonts, layouts |
| `io.github.salemalem/npmscan` | 0.5k | Check an npm package for security problems |
| `com.browser-use/browser-use` | 120k | Cloud browser agents; its read-only tools only |
| `io.github.tldraw/tldraw` | 51k | Diagrams on a tldraw canvas |
| `kell/images` | (kell's own) | Generate an image from a description |

**Remote, needs a sign-in (add it, sign in with the vendor):**

| Server | ★ | What it's for |
|---|---:|---|
| `io.github.github/github-mcp-server` | 34k | Issues, pull requests, code on GitHub |
| `io.github.PostHog/mcp` | 41k | Product analytics, feature flags |
| `com.stripe/mcp` | 2k | Customers, products, payments in Stripe |
| `com.atlassian/atlassian-mcp-server` | 1.1k | Jira and Confluence |
| `com.supabase/mcp` | 3k | Supabase projects and databases |
| `com.apify/apify-mcp-server` | 14k | Thousands of ready-made scrapers |
| `io.github.netdata/mcp-server` | 81k | Server and infrastructure monitoring |
| `io.github.Skyvern-AI/skyvern` | 23k | Browser automation for web tasks |
| `dev.openstatus/mcp` | 9k | Uptime and status pages |

**Packages (Claude Code only):**

| Server | ★ | What it's for |
|---|---:|---|
| `io.github.ChromeDevTools/chrome-devtools-mcp` | 54k | Drive and inspect a real Chrome |
| `io.github.D4Vinci/Scrapling` | 90k | Scraping that copes with modern sites |
| `io.github.oraios/serena` | 31k | Symbol-level code navigation and edits |
| `io.github.yamadashy/repomix` | 29k | Pack a repository into one file for a model |
| `io.github.GLips/Figma-Context-MCP` | 16k | Figma designs into code |
| `io.github.DayuanJiang/next-ai-drawio` | 37k | Draw.io diagrams |
| `com.microsoft/azure` | 3.8k | Azure resources |
| `io.github.firebase/firebase-mcp` | 4.5k | Firebase projects |
| `io.github.t8y2/dbx` | 28k | Query databases through saved connections |

**Catalog size by goal** (a server can serve several):

| Goal | Servers | | Goal | Servers |
|---|---:|---|---|---:|
| vcs | 26,566 | | payments | 3,722 |
| web | 25,150 | | commerce | 3,309 |
| docs | 7,788 | | database | 3,172 |
| workspace | 6,728 | | analytics | 2,851 |
| security | 5,103 | | crm | 2,618 |
| generate | 5,023 | | code_search | 2,354 |
| observability | 4,986 | | ci_cd | 1,974 |
| data | 4,836 | | design | 1,917 |
| messaging | 4,481 | | social | 1,916 |
| files_media | 4,289 | | ai_models | 1,596 |
| | | | browser | 1,420 |
| | | | sandbox | 1,346 |
| | | | packages | 973 |
| | | | cloud | 908 |
| | | | tickets | 630 |

**What the catalog data gets wrong.** These are found when browsing it; the ranking at request time mostly corrects them:
- **Stars can mislead.** A card gets the stars of whatever repository it links, so servers that link a big shared
  repository inherit its stars. One publisher has dozens of servers showing the same 7.4k, and some cards show
  100k+ for a small tool.
- **Goal labels on cards are loose.** A shell-theme validator is tagged `database`; a music-trend feed is tagged
  `vcs`. A request is matched by meaning and reranked, not only by these labels.
- **Official vendors with few stars rank low.** ElevenLabs is an example (on [revisit.md](revisit.md)).

## 4. Examples

**Claude Code, "upgrade our Next.js app to the latest version and fix what breaks"**
1. Triage: goal `docs`.
2. The note names Context7, through kell, first.
3. Claude calls `use_tool(server="io.github.upstash/context7", name=..., arguments={"libraryName": "next.js", ...})`.
   The first time, kell answers with a consent link. The user opens it and presses Allow; the call then returns the
   current migration guide.
4. Claude makes the change with today's API rather than one from its training data.

**Claude Code, "check that the login page still renders in a real browser"**
- Goal `browser`.
- If Playwright or Chrome DevTools is installed, the note names it first, and Claude uses it.
- If not, the note offers `claude mcp add chrome-devtools-mcp -- npx -y chrome-devtools-mcp`. Claude says so and does
  what it can without it; the tool is there from the next session.

**claude.ai, "make me a hero banner for my landing page: a mountain lake at sunrise, wide"**
- `find_ways` returns goal `generate` with `kell/images` first.
- Claude calls `use_tool(server="kell/images", name="generate_image", arguments={"prompt": "...", "size": "1280x720"})`.
- The image appears in the chat in about a second. Tested live on 2026-10-31.

**claude.ai, "which open issues on omni-us/jsonargparse mention dataclasses?"**
- Goal `vcs`. GitHub's official server needs a sign-in, so kell can't run it.
- Claude answers as well as it can and offers the connector URL
  `https://api.githubcopilot.com/mcp/` to add under Customize → Connectors, where the user signs in with GitHub once.

**Claude Code, "take a payment link for this invoice in Stripe"**
- Goal `payments`. Stripe needs a sign-in, and creating a payment link is a write.
- kell suggests adding Stripe's server: `claude mcp add --transport http stripe https://mcp.stripe.com`. The user signs
  in to Stripe, and Claude Code asks before each Stripe call.
- kell would refuse to run it itself, both because it needs a sign-in and because it's a write.

**Either, "is the package left-pad-ng safe to add?"**
- Goal `security`.
- NPMScan through kell (gateway) checks it; an installed scanner (Snyk, Socket) would come first.

**Either, "rename foo to bar in utils.py" or "thanks!"**
- No tool goal, so no note and no tool calls.

## 5. Learning which tools actually help

- **Claude Code:** the capture hooks see which MCP and web tools a prompt used, and whether its tests passed. At the
  end they send `tool_goal, tool, used, suggested, outcome` to `/tools/outcome`, and the library entry is tagged
  `tool:<ref>`.
- **claude.ai:** there are no hooks, so the model calls `report_tools` after a task that used tools.
- **Gateway:** calls are recorded automatically.
- **Ranking:** these rows feed a "proven" term:
  - it moves a tool by up to ±0.1, by smoothed pass rate × confidence (n / (n + 5));
  - a tool that keeps failing sinks, and one that keeps helping rises;
  - all users count together today; per-team weighting is on [revisit.md](revisit.md).

## 6. Not yet (all on [revisit.md](revisit.md))

- Gateway phase 2: kell signs in to a vendor's server once for the user, so sign-in tools run without adding them.
- Phase 3: Claude Code auto-install with a one-time opt-in.
- Paid tools charged through kell credits.
- Write tools with a confirmation step.
- More of kell's own tools (voice, video).
- A per-server terms review.
- Better stars and goal labels.

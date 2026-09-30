# Hosted MCP Provider Practices (auth, reliability, rate limits, distribution, versioning) — as of Sept 2026

Research date: 2026-09-27. Primary sources preferred; secondary/aggregator sources are flagged as such.

## 1. Provider-by-provider practices (transport, auth, scopes, read-only, tool design, rate limits, security docs)

### Takeaway
The convergent pattern among major hosted MCP servers is: one Streamable HTTP endpoint at `/mcp` (SSE kept only as a deprecated fallback with a dated sunset), OAuth 2.1 with Dynamic Client Registration as the default plus a bearer-token/API-key fallback for headless/CI clients, a URL- or header-selectable read-only mode, and aggressive tool-count reduction (toolsets, per-tool selection, or a few "meta" tools). Security docs universally warn about prompt injection and recommend human confirmation; few providers publish rate limits or SLAs for MCP specifically.

### Cited Findings

**GitHub (`https://api.githubcopilot.com/mcp/`)**
- Hosted remote server, "no local setup or runtime required"; configuration via headers `X-MCP-Toolsets`, `X-MCP-Tools`, `X-MCP-Readonly`, `X-MCP-Lockdown`, `X-MCP-Insiders`, and equivalent URL paths `/readonly`, `/insiders`, `/x/all`, `/x/{toolset}`, combinable (e.g. `/x/{toolset}/readonly/insiders`); 25+ toolsets; remote-only toolsets such as `copilot_spaces` and `github_support_docs_search` — [github-mcp-server docs/remote-server.md](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)
- Auth: OAuth (no PAT needed) or PAT. Classic PATs: server discovers token scopes at startup and hides tools the token lacks scopes for; OAuth uses scope challenges to request authorization when needed (Jan 28, 2026 changelog: "OAuth scope filtering") — [GitHub Changelog 2026-01-28](https://github.blog/changelog/2026-01-28-github-mcp-server-new-projects-tools-oauth-scope-filtering-and-new-features/); [GitHub Docs setup](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/set-up-the-github-mcp-server)
- "Dynamic toolset discovery" lets the host list/enable toolsets on demand, to avoid the model being confused by the number of tools — [GitHub Changelog 2026-01-28](https://github.blog/changelog/2026-01-28-github-mcp-server-new-projects-tools-oauth-scope-filtering-and-new-features/) (via search summary)
- Dec 10, 2025: per-tool selection (`X-MCP-Tools` / `--tools`) claimed to cut context use "60–90%" vs defaults; migrated from community mark3labs/mcp-go to the official Go MCP SDK "enabling faster alignment with evolving MCP specifications"; existing config options stay compatible — [GitHub Changelog 2025-12-10](https://github.blog/changelog/2025-12-10-the-github-mcp-server-adds-support-for-tool-specific-configuration-and-more/)
- Insiders channel (`/insiders` or `X-MCP-Insiders`) gives early-access/experimental features — a de facto opt-in beta channel on the same endpoint — [remote-server.md](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)

**Stripe (`https://mcp.stripe.com`)**
- Auth: OAuth for interactive clients; "agent API keys" (restricted keys with an Agent badge) as bearer tokens for autonomous clients. **From 31 Oct 2026 Stripe MCP stops accepting full-access secret keys or restricted keys without the Agent tag**; unsupported keys get `401` with an OAuth discovery challenge (a dated auth-deprecation with a machine-actionable failure mode) — [Stripe MCP docs](https://docs.stripe.com/mcp)
- OAuth consent lets the user grant one or more live accounts or sandboxes with different permissions per environment; admins can enable/disable MCP access team-wide separately for live and sandbox; users and admins can list and revoke OAuth sessions in the Dashboard — [Stripe MCP docs](https://docs.stripe.com/mcp)
- Tool design: ~10 tools only. Generic `stripe_api_search`, `stripe_api_details`, `stripe_api_read` (any GET), `stripe_api_write` (any POST/PATCH/PUT/DELETE) expose a large allowlisted API surface "without increasing the context window unnecessarily" — [Stripe MCP docs](https://docs.stripe.com/mcp)
- Server-side human-in-the-loop: certain writes (refunds, outbound payments) return a URL the human must approve; approval yields a token the agent retries with; approvals expire after 24 h — [Stripe MCP docs](https://docs.stripe.com/mcp)
- Security doc: "Enable human confirmation of tools and exercise caution when using the Stripe MCP with other servers to avoid prompt injection attacks" — [Stripe MCP docs](https://docs.stripe.com/mcp)
- Distribution: recommends `stripe agent setup` (npm-installed CLI that detects which agents you use and configures MCP + skills, "automatically ... up-to-date"); also provides a Cursor deeplink, VS Code redirect link, `claude mcp add --transport http stripe https://mcp.stripe.com/`, `codex mcp add stripe --url ...`, Codex TOML `bearer_token_env_var`, Claude directory listing, ChatGPT plugin — [Stripe MCP docs](https://docs.stripe.com/mcp). **This is the closest existing analogue to the startup's planned npm installer.**
- MCP tool-call logs visible in Stripe Workbench — [Stripe MCP docs](https://docs.stripe.com/mcp)

**Linear (`https://mcp.linear.app/mcp`)**
- Streamable HTTP primary; `https://mcp.linear.app/mcp/readonly` read-only endpoint; `/sse` retained as deprecated fallback. Auth: OAuth 2.1 with DCR, or Linear API key / bearer token in `Authorization`. Read-only also achievable by requesting only the `read` OAuth scope or a restricted API key — [Linear Docs: MCP](https://linear.app/docs/mcp)
- No rate limits or tool changelog published on the MCP page — [Linear Docs: MCP](https://linear.app/docs/mcp)

**Supabase (`https://mcp.supabase.com/mcp`)**
- Default auth OAuth with DCR; PATs via `Authorization` header for CI; manual OAuth apps for clients needing explicit client ID/secret. URL query params: `read_only=true` (queries run as a read-only DB user — enforced at the data layer, not just by hiding tools), `project_ref=<id>` (scope to one project), `features=<groups>` (tool groups; storage off by default) — [Supabase MCP docs](https://supabase.com/docs/guides/getting-started/mcp)
- Security section: prompt injection via untrusted data in the database; recommends not connecting production, project scoping, read-only, limiting feature groups, manual approval of tool calls, and not exposing it to customer-facing apps — [Supabase MCP docs](https://supabase.com/docs/guides/getting-started/mcp)

**Atlassian (`https://mcp.atlassian.com/v1/mcp`)**
- HTTP+SSE at `/v1/sse` deprecated with a published sunset of 30 June 2026 — [Atlassian Community: HTTP+SSE Deprecation Notice](https://community.atlassian.com/forums/Atlassian-Remote-MCP-Server/HTTP-SSE-Deprecation-Notice/ba-p/3205484)
- Post-sunset reality: a GitHub issue reports `/v1/sse` still worked after 30 June 2026, with a deprecation banner repeated on every tool call and "no machine-actionable migration path" — [atlassian/atlassian-mcp-server issue #212](https://github.com/atlassian/atlassian-mcp-server/issues/212). (Lesson: in-band deprecation text inside tool results pollutes agent context; prefer structured signals.)

**Sentry (`https://mcp.sentry.dev/mcp`)**
- Hosted, OAuth for every connection — [Scalar: Remote MCP servers](https://scalar.com/learn/mcp/remote-mcp-servers) (secondary source; primary Sentry docs not fetched)

**Notion (`mcp.notion.com`)**
- Hosted Notion MCP is OAuth-only (no bearer tokens); rate limit reported as 180 requests/min per user, search 30/min — [StackOne: Notion MCP deep dive](https://www.stackone.com/blog/notion-mcp-deep-dive/) (secondary; not verified against Notion's own docs). Notion also publishes a guide for building an MCP client against it — [Notion Docs: Build an MCP client](https://developers.notion.com/guides/mcp/build-mcp-client)

**Vercel (`mcp.vercel.com`)**
- Vercel MCP only accepts AI clients reviewed and approved by Vercel (client allowlist: Claude Code, Claude.ai/Desktop, ChatGPT, Codex CLI, Cursor, VS Code Copilot, Devin, Raycast, Goose, Windsurf, Gemini Code Assist/CLI) — [Vercel docs: Use Vercel's MCP server](https://vercel.com/docs/agent-resources/vercel-mcp) (via search summary). Third parties must file public allowlist requests — [Vercel Community allowlist request](https://community.vercel.com/t/request-allowlist-myclaw-for-vercel-mcp-mcp-vercel-com-oauth/49351)

**Cross-provider**
- GitHub, Stripe, Sentry, Linear, Atlassian, Supabase and Notion all host their own endpoints; all ship OAuth — [Scalar: Remote MCP servers](https://scalar.com/learn/mcp/remote-mcp-servers) (secondary)

### Inferences
- Minimum credible launch surface for a 2026 hosted MCP: Streamable HTTP only at `/mcp`, OAuth 2.1 (DCR + CIMD) plus a scoped "agent key" bearer fallback for CI/headless, a `/readonly` URL variant, and toolset selection via URL path or header. Adding SSE at launch is unnecessary for a new entrant (no legacy clients) and Anthropic's directory rejects it.
- Read-only should be enforced server-side at the data layer (Supabase pattern), not merely by hiding tools.
- Small tool count is the norm for large APIs (Stripe ~10 tools; GitHub per-tool selection). For a procedural-memory service, a handful of well-annotated tools is aligned with practice.
- Stripe's dated key-type deprecation with `401` + OAuth challenge is a good template for migrating credential types without breaking agents silently.
- Vercel's client allowlist is a stricter posture than most; it costs third-party-client reach.

### Gaps
- Published MCP-specific rate limits: only Notion's (secondary) found. GitHub, Stripe, Linear, Supabase, Atlassian MCP docs fetched here did not state MCP rate limits (they presumably inherit the underlying API limits — unverified).
- MCP-specific SLAs/status-page components: not found for any provider in this session. Did not verify whether status.github.com / status.stripe.com list MCP as a separate component.
- Cloudflare's own first-party MCP servers, Sentry and Notion primary docs were not fetched.

## 2. Cloudflare remote MCP platform (Agents SDK, workers-oauth-provider, McpAgent) and fronting a Python server

### Takeaway
Cloudflare now steers new servers to stateless `createMcpHandler()` (Streamable HTTP) and marks `McpAgent` (Durable Object–backed, stateful) as deprecated; auth is via `@cloudflare/workers-oauth-provider` (the Worker acts as OAuth 2.1 server to MCP clients and OAuth client to an upstream IdP), Cloudflare Access, or BYO providers. A Worker acting as auth/proxy in front of a Python origin is an established pattern (there is an official proxy example PR), but Cloudflare's docs don't document it as a first-class path.

### Cited Findings
- Cloudflare's remote MCP guide lists `createMcpHandler()` for stateless tools, `McpAgent` "now deprecated", and `createLegacyMcpHandler()` for migration compatibility; Streamable HTTP transport; auth via Cloudflare Access or third-party OAuth (GitHub, Google, Slack, Auth0, WorkOS, Stytch) — [Cloudflare Agents: Remote MCP server guide](https://developers.cloudflare.com/agents/guides/remote-mcp-server/)
- Four auth approaches: Cloudflare Access; third-party OAuth where "the MCP Server generates its own token after exchanging the third-party authorization code"; BYO OAuth (Stytch, Auth0, WorkOS, Descope); self-hosted via Workers OAuth Provider Library. Auth context in tools: `this.props` (McpAgent, deprecated) or `getMcpAuthContext()` / `context.http.authInfo` (createMcpHandler). Two permission patterns: check inside handler, or conditionally register tools so the model never sees inaccessible tools — [Cloudflare Agents: Authorization](https://developers.cloudflare.com/agents/model-context-protocol/authorization/)
- `workers-oauth-provider` handles token issuance, refresh and scopes; the Worker stores an encrypted upstream access token in Workers KV and issues its own token to the MCP client — [cloudflare/workers-oauth-provider](https://github.com/cloudflare/workers-oauth-provider); [Cloudflare blog: Remote MCP servers](https://blog.cloudflare.com/remote-model-context-protocol-servers-mcp/)
- An upstream PR adds `proxy-mcp-server` and `separate-authorization-server` examples to workers-oauth-provider — [workers-oauth-provider PR #314](https://github.com/cloudflare/workers-oauth-provider/pull/314)
- Python-side equivalent: FastMCP ships an OAuth Proxy (issues its own tokens, handles dynamic client callbacks against fixed upstream callback; OIDC proxy auto-discovers endpoints) — [FastMCP: OAuth Proxy](https://gofastmcp.com/servers/auth/oauth-proxy)

### Inferences
- A Python MCP server (e.g. FastMCP/uvicorn) can sit behind a Cloudflare Worker that terminates OAuth (workers-oauth-provider) and forwards authenticated requests with identity context to the origin; alternatively, do the OAuth proxying in Python with FastMCP and use Cloudflare only as CDN/WAF. Given the startup's backend is Python and its MCP server has in-process state (`--workers 1`, in-memory TasksExtension store per the repo's CLAUDE.md), the Cloudflare stateless model does not remove the need for a shared session/task store on the origin.
- Cloudflare's own deprecation of the stateful McpAgent mirrors the ecosystem shift toward stateless Streamable HTTP servers that scale horizontally.

### Gaps
- Did not confirm Cloudflare's current recommended way to forward to an external origin (docs fetched don't address it); PR #314 merge status not verified.
- Did not check Cloudflare Python Workers' MCP support.
- No Cloudflare-published scaling numbers for MCP.

## 3. Distribution: official MCP Registry, third-party directories, Anthropic connector directory, deeplinks, Claude Code plugins

### Takeaway
Publish once to the official MCP Registry (metadata-only, still "preview") with a `remotes` entry and DNS-verified reverse-DNS namespace; several directories ingest from it. Separately submit to Anthropic's Connectors Directory (any paid plan; OAuth; tool annotations mandatory; auto-scanned then listed as Community). Offer Cursor/VS Code one-click links and a Claude Code plugin that bundles the MCP config and skills.

### Cited Findings

**Official MCP Registry**
- "The MCP Registry is currently in preview. Breaking changes or data resets may occur before general availability"; hosts metadata only, not artifacts — [MCP Registry Quickstart](https://modelcontextprotocol.io/registry/quickstart)
- Flow: `mcp-publisher init` → edit `server.json` (schema `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`) → `mcp-publisher login github` (device code; grants `io.github.<user>/*`) → `mcp-publisher publish`; verify via `GET https://registry.modelcontextprotocol.io/v0.1/servers?search=...`. npm packages must include `mcpName` in package.json equal to `server.json` `name`. DNS authentication enables custom-domain prefixes; GitHub Actions publishing supported — [MCP Registry Quickstart](https://modelcontextprotocol.io/registry/quickstart)
- Namespace is reverse-DNS + `/` + identifier (e.g. `com.example/acme-analytics`) — [MCP Registry Quickstart](https://modelcontextprotocol.io/registry/quickstart); [Microsoft Learn .NET publish quickstart](https://learn.microsoft.com/en-us/dotnet/ai/quickstarts/publish-mcp-registry)
- Hosted servers use `remotes: [{ "type": "streamable-http", "url": ... }]`; SSE is deprecated ("publish an `sse` remote only to support existing clients"); URL template variables (`{tenant_id}`, with `choices`, `default`, `isSecret`); `headers` array (e.g. `X-API-Key`, `isSecret: true`); `remotes` can coexist with `packages` (e.g. an npm stdio package) so hosts pick — [MCP Registry: Remote servers](https://modelcontextprotocol.io/registry/remote-servers)
- Official registry requirements doc — [registry official-registry-requirements.md](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/official-registry-requirements.md)

**Third-party directories (secondary sources)**
- Smithery and PulseMCP ingest from the official registry, so one publish can cascade — [Tallyfy guide](https://tallyfy.com/how-to-list-mcp-server-registry-smithery-glama-pulsemcp/); [OpenHelm guide](https://openhelm.ai/blog/mcp-registry-directory-guide)
- Glama claims 37,800 indexed servers, ~6,000 hosted connectors (self-reported mid-2026) — [ThinkNEO comparison, 2026-07-14](https://thinkneo.ai/blog/mcp-registries-compared-20260714) (secondary, self-reported figures)
- Smithery supports deep links — [Smithery docs: Deep linking](https://smithery.ai/docs/use/deep-linking)

**Anthropic Connectors Directory**
- Submit via developer portal `claude.ai/directory/manage` → "MCP connector"; **any paid Claude plan** can submit. Requirements: remote HTTPS URL; OAuth 2.0 for authenticated services (or none for public data); every tool needs `title` and `readOnlyHint` or `destructiveHint`; tested in Claude as a custom connector or via MCP Inspector; docs URL, privacy policy URL, support contact, icon; reviewer test account with populated data; seven policy acknowledgments (directory guidelines, first-party API usage, financial transactions, AI media generation, prompt injection, conversation data collection, public documentation). Local servers/MCPB desktop-extension listings deprecated — local servers must ship inside a plugin. Listing: name ≤100 chars, one-liner ≤200, description ≤2,000, 1–5 categories, permanent slug. After submit: automated policy scan and by default listed as **Community**; some get human review; escalations to `mcp-review@anthropic.com` — [Claude Docs: Submit a connector](https://claude.com/docs/connectors/building/submission)
- Note: older secondary guides said Team/Enterprise org was required and Streamable HTTP only (SSE rejected) — [Tallyfy](https://tallyfy.com/how-to-list-mcp-server-anthropic-claude-connectors/), [sunpeak Aug 2026](https://sunpeak.ai/blogs/claude-connector-directory-submission/); current primary doc says any paid plan (conflict resolved in favor of primary source).
- Auth specifics for Claude clients: supported `oauth_dcr` and `oauth_cimd` by default; `oauth_anthropic_creds`, `custom_connection` via `mcp-review@anthropic.com`; `static_headers` (API key/bearer) is beta for limited orgs; no `client_credentials` M2M. Stricter-than-spec points: must return `401` with `WWW-Authenticate: Bearer resource_metadata=...`; only first `authorization_servers` entry used; CIMD used only if AS advertises `client_id_metadata_document_supported: true` and `none` in `token_endpoint_auth_methods_supported`; PKCE S256 required; Claude Code uses loopback redirect on any port, must match both `localhost` and `127.0.0.1` port-agnostically; hosted apps redirect `https://claude.ai/api/mcp/auth_callback`; 10 s timeout for discovery/registration/token endpoints, 30 s for refresh; refresh proactively up to 5 min before expiry; return `invalid_grant` for dead refresh tokens; rotate refresh tokens for public clients; `/token` must accept form-urlencoded. **For high-traffic servers prefer CIMD over DCR** because DCR registers a new client on every fresh connection. Never accept tokens in query strings. Anthropic egress `160.79.104.0/21` — [Claude Docs: Authentication for connectors](https://claude.com/docs/connectors/building/authentication)
- After publishing: tool changes are deployed directly to your server with no resubmission; tool names shown on the listing are updated via a reviewed listing edit; slug permanent; connectors delisted by email; health badge and usage metrics provided — [Claude Docs: After publishing](https://claude.com/docs/connectors/building/after-publishing)

**Client install links**
- Cursor: `cursor://anysphere.cursor-deeplink/mcp/install?name=$NAME&config=$BASE64_ENCODED_CONFIG` — [Cursor Docs: MCP Install Links](https://cursor.com/docs/context/mcp/install-links)
- VS Code: `https://vscode.dev/redirect/mcp/install?name=...&config=<URL-encoded JSON>` (Stripe's live example), config `{"type":"http","url":...}` — [Stripe MCP docs](https://docs.stripe.com/mcp); VS Code uses URL-encoded JSON vs Cursor base64 — [DanyWalls guide](https://danywalls.com/create-one-click-mcp-installation-links-cursor-vscode) (secondary)
- Cursor forum reports deeplinks broken on some platforms (e.g. Debian) — [Cursor forum](https://forum.cursor.com/t/cant-install-mcp-servers-with-deeplinks-on-debian/114195)
- Codex: `codex mcp add <name> --url <url>`; TOML `[mcp_servers.x] url=..., bearer_token_env_var=...`; shared config across ChatGPT desktop, Codex CLI and IDE extension — [Stripe MCP docs](https://docs.stripe.com/mcp)
- Claude Code: `claude mcp add --transport http <name> <url>` then `/mcp` to OAuth; project `.mcp.json` supports `${ENV}` in headers so keys aren't on the command line — [Stripe MCP docs](https://docs.stripe.com/mcp)

**Claude Code plugins / marketplaces**
- A marketplace is `.claude-plugin/marketplace.json` in a git repo; plugins bundle skills, agents, commands, hooks and MCP servers (`.mcp.json` or inline in `plugin.json`) — [anthropics/claude-plugins-official marketplace.json](https://github.com/anthropics/claude-plugins-official/blob/main/.claude-plugin/marketplace.json); [claudefa.st plugins distribution](https://claudefa.st/blog/tools/mcp-extensions/plugins-distribution) (secondary)
- Listed plugins update by pushing to the tracked branch/tag; each version re-runs validation and security scan; bump `version` in `plugin.json` so installs detect updates; failing versions don't take down the listing; plugin Usage tab shows installs, version share, MCP error rate and latency — [Claude Docs: After publishing](https://claude.com/docs/connectors/building/after-publishing)
- Vendors are being asked by users to ship Claude Code plugins (PostHog, monday, Notion issues) — [makenotion/notion-mcp-server #137](https://github.com/makenotion/notion-mcp-server/issues/137)

### Inferences
- Use a DNS-verified namespace (e.g. `com.<domain>/<name>`) rather than `io.github.<user>` for a company server; the registry's "preview/data reset" status means keep your own install docs authoritative.
- Anthropic recommends CIMD over DCR at scale; since Claude Code itself uses a hosted CIMD, supporting CIMD + DCR covers every Claude surface.
- The startup's npm installer duplicates what Stripe's `stripe agent setup` does; it should also emit per-client deeplinks and a Claude Code plugin so users aren't dependent on one installer path. Writing config files directly is fragile against client config-format changes; prefer each client's CLI (`claude mcp add`, `codex mcp add`) where available.

### Gaps
- Anthropic human-review timelines not published ("vary with queue volume").
- Verified vs Community label criteria not fetched ([verification page](https://claude.com/docs/connectors/verification) exists).
- PulseMCP/Glama/mcp.so submission specifics came only from secondary sources.

## 4. Release / versioning practices (tool changes, list_changed, protocol version negotiation)

### Takeaway
No provider publishes formal semantic versioning for tool schemas; the proven practices are additive changes, opt-in beta channels (GitHub `/insiders`), config backward compatibility, dated deprecations with structured failures (Stripe `401` + OAuth challenge; Atlassian SSE sunset), and relying on per-request protocol-version negotiation. Anthropic's directory lets you change tools by deploying, with only listing text re-reviewed.

### Cited Findings
- Spec 2025-11-25 key changes: OAuth Client ID Metadata Documents as a recommended registration mechanism; OpenID Connect Discovery support; icons for tools/resources/prompts; incremental scope consent via `WWW-Authenticate`; URL-mode elicitation; tool calling in sampling; experimental tasks (durable requests with polling) — [MCP spec 2025-11-25 changelog](https://modelcontextprotocol.io/specification/2025-11-25/changelog)
- Over Streamable HTTP, after version negotiation each request carries `MCP-Protocol-Version` (required since 2025-06-18) — [MCP spec version timeline](https://hidekazu-konishi.com/entry/mcp_specification_version_timeline.html) (secondary summary); spec changelog above
- MCP Registry server.json schema itself is date-versioned (`schemas/2025-12-11/server.schema.json`) — [MCP Registry Quickstart](https://modelcontextprotocol.io/registry/quickstart)
- GitHub: new features ship first behind Insiders; Dec 2025 release stressed that existing configuration options remain compatible; SDK migration to official Go SDK for spec alignment — [GitHub Changelog 2025-12-10](https://github.blog/changelog/2025-12-10-the-github-mcp-server-adds-support-for-tool-specific-configuration-and-more/); [remote-server.md](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)
- GitHub OAuth path uses scope challenges (incremental consent) rather than failing tools upfront — [GitHub Changelog 2026-01-28](https://github.blog/changelog/2026-01-28-github-mcp-server-new-projects-tools-oauth-scope-filtering-and-new-features/)
- Anthropic directory: tool add/change/remove = deploy to server, no resubmission; listing tool names updated through reviewed edit — [Claude Docs: After publishing](https://claude.com/docs/connectors/building/after-publishing)
- Atlassian's SSE sunset communicated via community post and in-band deprecation banners in tool output; criticized for missing machine-actionable migration and non-enforcement after the date — [Atlassian notice](https://community.atlassian.com/forums/Atlassian-Remote-MCP-Server/HTTP-SSE-Deprecation-Notice/ba-p/3205484); [issue #212](https://github.com/atlassian/atlassian-mcp-server/issues/212)
- Stripe: dated credential deprecation (31 Oct 2026) with a spec-conformant `401` + OAuth discovery challenge on failure — [Stripe MCP docs](https://docs.stripe.com/mcp)

### Inferences
- For a new entrant: treat tool names and input schemas as a public API — only add optional params; never rename a tool without keeping the old name as an alias for a deprecation window; announce removals with dates; use a separate URL path (`/beta` or `/insiders`) for experimental tools; declare `tools.listChanged` capability and emit `notifications/tools/list_changed` when the tool set changes mid-session (spec feature; client support varies — not verified in this session).
- Server should support at least the two most recent protocol versions (2025-06-18 and 2025-11-25) and validate `MCP-Protocol-Version`.

### Gaps
- Did not find a provider that publicly documents a tool-schema versioning policy or a deprecation window length for tools.
- Did not verify which clients (Claude Code, Cursor, Codex) honor `notifications/tools/list_changed` for remote servers.
- Did not confirm whether a newer spec revision than 2025-11-25 was released by Sept 2026.

## 5. Incidents and postmortems involving hosted MCP servers

### Takeaway
The two canonical incidents are (a) Asana's MCP cross-tenant exposure (logic flaw in tenant isolation; server offline ~2 weeks; all connections reset) and (b) Invariant Labs' GitHub MCP "toxic agent flow" (prompt injection via public issue leaks private repo data), which GitHub later answered with default content sanitization and an opt-in Lockdown mode — both explicitly best-effort, not authorization boundaries.

### Cited Findings
- **Asana (2025)**: opt-in MCP server launched early May 2025; bug found June 4, server taken offline; MCP disabled June 5–17; ~1,000 customers potentially exposed; users in one org could see project names, task descriptions and metadata from other tenants; cause: flawed tenant isolation / incomplete access-control enforcement, not an external attack; remediation included resetting all MCP connections (users had to reconnect) — [BleepingComputer](https://www.bleepingcomputer.com/news/security/asana-warns-mcp-ai-feature-exposed-customer-data-to-other-orgs/); [The Register, 2025-06-18](https://www.theregister.com/security/2025/06/18/asana-mcp-server-back-online-after-plugging-a-data-leak-hole/1199951); [UpGuard](https://www.upguard.com/blog/asana-discloses-data-exposure-bug-in-mcp-server). Note: some secondary summaries describe the exposure window as June 5–17, which is actually the downtime window; the exposure window per reports ran from the May launch until the June 4 discovery — sources conflict/are garbled; check BleepingComputer/Asana's notice before citing exact dates.
- **GitHub MCP (Invariant Labs, May 2025)**: malicious issue in a public repo hijacks an agent using the GitHub MCP server, causing it to read private repos and leak contents via a PR; Invariant: "This vulnerability cannot be resolved through server-side patches" — a structural toxic-flow issue — [Invariant Labs blog](https://invariantlabs.ai/blog/mcp-github-vulnerability)
- GitHub response (Dec 10, 2025): content sanitization on by default (Unicode invisible-char filtering, HTML sanitization, code-fence filtering) and `X-MCP-Lockdown` restricting public-repo content to authors with push access — [GitHub Changelog 2025-12-10](https://github.blog/changelog/2025-12-10-the-github-mcp-server-adds-support-for-tool-specific-configuration-and-more/). GitHub's own docs describe lockdown as best-effort, "not an authorization boundary" — [server-configuration.md](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md) (via search summary)
- July 2026: a public GitHub issue could trick GitHub Agentic Workflows into leaking private repo data (same class of issue persists) — [The Hacker News, 2026-07](https://thehackernews.com/2026/07/public-github-issue-could-trick-github.html) (headline only; not fetched)
- Anthropic now makes prompt injection one of seven mandatory policy acknowledgments for directory listing — [Claude Docs: Submit a connector](https://claude.com/docs/connectors/building/submission)

### Inferences
- For a multi-tenant procedural-memory service, the Asana failure mode (tenant isolation bug in a new MCP layer) is the most relevant risk: MCP handlers must reuse the core API's tenant-scoping path (in this repo, `scope_predicates()` / `tenant_transaction` / RLS), never a parallel query path, and a kill-switch plus "reset all connections" capability should exist before launch.
- Memory returned to agents is itself an injection vector (stored procedures written from untrusted traces); GitHub's sanitization + provenance-based trust filtering (lockdown = author trust) maps directly onto provenance-gated retrieval.

### Gaps
- No public outage postmortems (availability incidents) for hosted MCP servers found in this session.
- Did not locate Asana's own primary disclosure text.
- Did not research other 2026 incidents (e.g., mcp-remote CVE, Smithery hosting incidents) due to budget.

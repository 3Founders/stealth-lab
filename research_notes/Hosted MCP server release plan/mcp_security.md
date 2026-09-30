# Security for a hosted, multi-tenant remote MCP server (as of Sept 2026)

Scope: a Python Streamable-HTTP MCP server that returns verified procedures to coding agents (Claude Code, Cursor, Codex, Claude.ai connectors). Reads are anonymous. Writes (`report_discovery`, `submit_way`, `report_model_run`) need a user token. Postgres with RLS. Procedure text is a prompt-injection surface. There is an optional local executor that runs agent CLIs in git worktrees.

Research date: 2026-09-27. The latest MCP spec revision is **2026-07-28**. Every modelcontextprotocol.io quote below comes from that revision unless marked 2025-11-25.

---

## 1. What the current MCP authorization spec requires (2026-07-28)

### Takeaway
The MCP server is an OAuth 2.1 **resource server**. It MUST publish RFC 9728 Protected Resource Metadata, MUST check that each token's audience is this server (RFC 8707), and MUST NOT accept or forward any other token. Revision 2026-07-28 made two changes that matter here:
- **Protocol-level sessions (`Mcp-Session-Id`) are gone.** Any state is carried in explicit handles that must be bound to the user.
- **Dynamic Client Registration is deprecated** in favour of Client ID Metadata Documents (CIMD).

Clients bear most of the flow's MUSTs (PKCE S256, `resource` parameter, `iss` validation). The server and its AS own audience checks, metadata, scope challenges, refresh-token rotation, and Origin validation.

### Cited Findings

**Framing and roles**
- Authorization is OPTIONAL. When supported, HTTP-transport implementations "SHOULD conform to this specification", and STDIO implementations "SHOULD NOT follow this specification, and instead retrieve credentials from the environment." — [MCP Authorization (2026-07-28)](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- Standards it builds on: OAuth 2.1 draft-13, RFC 6750, RFC 8414, RFC 7591, RFC 8707, RFC 9728, RFC 9207 (issuer identification), draft-ietf-oauth-client-id-metadata-document-00, and OIDC Discovery/Registration. — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- "A protected MCP server acts as an OAuth 2.1 resource server." The AS "may be hosted with the resource server or a separate entity." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- "Authorization servers MUST implement OAuth 2.1 with appropriate security measures for both confidential and public clients." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)

**Protected Resource Metadata (RFC 9728) and discovery**
- "MCP servers MUST implement OAuth 2.0 Protected Resource Metadata (RFC9728)." The PRM document "MUST include the `authorization_servers` field containing at least one authorization server." — [Authorization Server Discovery](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery)
- Servers MUST implement at least one discovery mechanism:
  - a `WWW-Authenticate` header with `resource_metadata` on 401, or
  - a well-known URI, either path-inserted (`/.well-known/oauth-protected-resource/public/mcp`) or at the root.

  Clients MUST support both. — [Authorization Server Discovery](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery)
- The AS MUST provide RFC 8414 metadata or OIDC Discovery. Clients try the endpoints in a fixed order: `oauth-authorization-server` with path insertion, then `openid-configuration` with path insertion, then `openid-configuration` appended to the path. Clients MUST reject metadata whose `issuer` differs from the URL used to fetch it. — [Authorization Server Discovery](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery)

**Scopes and step-up**
- "MCP servers SHOULD include a `scope` parameter in the `WWW-Authenticate` header." `scopes_supported` "is intended to represent the minimal set of scopes necessary for basic functionality." Extra scopes are requested through step-up. — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- On insufficient scope at runtime, the server "SHOULD respond with HTTP 403 Forbidden" and `WWW-Authenticate: Bearer error="insufficient_scope", scope="...", resource_metadata="..."`. Servers "SHOULD include all scopes required for the current operation in a single challenge." "Servers MUST account for scope hierarchies." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- Status codes: 401 when authorization is required or the token is invalid, 403 for invalid or insufficient scopes, 400 for a malformed request. "Invalid or expired tokens MUST receive a HTTP 401 response." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- MCP servers "SHOULD NOT include `offline_access` in `WWW-Authenticate` scope or Protected Resource Metadata `scopes_supported`." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)

**Client registration: CIMD vs pre-registration vs DCR**
- CIMD is a SHOULD for both clients and authorization servers. DCR is a MAY and "is deprecated and retained for backwards compatibility." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization); [Client Registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration)
- Client priority order: (1) pre-registered, (2) CIMD if the AS advertises `client_id_metadata_document_supported`, (3) DCR if there is a `registration_endpoint`, (4) prompt the user. — [Client Registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration)
- AS obligations for CIMD:
  - "MUST validate that the fetched document's `client_id` matches the URL exactly"
  - "MUST validate redirect URIs presented in an authorization request against those in the metadata document"
  - "SHOULD cache metadata respecting HTTP cache headers"

  — [Client Registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration)
- CIMD creates SSRF risk at the AS, which fetches a URL chosen by an unknown client. The spec says to block private IP ranges and use egress proxies. — [Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)
- Localhost redirect impersonation: CIMD "cannot prevent `localhost` URL impersonation by themselves." Authorization servers "SHOULD display additional warnings for `localhost`-only redirect URIs" and "MUST clearly display the redirect URI hostname during authorization." — [Authorization Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)

**PKCE, redirect URIs, and issuer validation**
- Clients "MUST implement PKCE", "MUST use the `S256` code challenge method when technically capable", and MUST refuse to proceed if `code_challenge_methods_supported` is missing from AS metadata. "Authorization servers providing OpenID Connect Discovery 1.0 MUST include `code_challenge_methods_supported`." — [Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)
- "All authorization server endpoints MUST be served over HTTPS. All redirect URIs MUST be either `localhost` or use HTTPS." "Authorization servers MUST validate exact redirect URIs against pre-registered values." — [Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)
- New in 2026-07-28 (RFC 9207 mix-up defence): the AS "SHOULD include the `iss` parameter in authorization responses". An AS that does so "MUST advertise" `authorization_response_iss_parameter_supported: true`. Clients MUST validate `iss` against the issuer they recorded. The spec expects a future revision to raise this from SHOULD to MUST. — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)

**Resource indicators and audience binding (RFC 8707)**
- Clients MUST send `resource`, set to the MCP server's canonical URI, in both authorization and token requests, "regardless of whether authorization servers support it." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- "MCP servers MUST validate that access tokens were issued specifically for them as the intended audience." "MCP servers MUST only accept tokens that are valid for use with their own resources." "MCP servers MUST NOT accept or transit any other tokens." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- Tokens go in the `Authorization: Bearer` header. Authorization "MUST be included in every HTTP request". "Access tokens MUST NOT be included in the URI query string." — [MCP Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)

**No token passthrough**
- "If the MCP server makes requests to upstream APIs … The MCP server MUST NOT pass through the token it received from the MCP client." — [Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)
- The listed risks of passthrough: security-control circumvention, loss of the audit trail, broken trust boundaries, and future-compatibility problems. The mitigation: "MCP servers MUST NOT accept any tokens that were not explicitly issued for the MCP server." — [Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)

**Token lifetime and theft**
- "Authorization servers SHOULD issue short-lived access tokens." "For public clients, authorization servers MUST rotate refresh tokens." Clients and servers "MUST implement secure token storage." — [Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)

**Sessions and state handles**
- Revision 2026-07-28 removed "the GET stream endpoint" and "protocol-level sessions". A server that sees an `Mcp-Session-Id` from an older client should "ignore it, and do not mint or echo session IDs". It should answer GET/DELETE with 405. — [Streamable HTTP (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- State handle hijacking (2026-07-28):
  - "MCP servers MUST NOT treat possession of a state handle as authentication."
  - Handles "SHOULD" come from a secure RNG.
  - Servers "SHOULD bind handles server-side to the authenticated user, for example by keying stored state as `<user_id>:<handle>` where the user ID is derived from the verified token."

  — [Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)
- The 2025-11-25 equivalent, for servers that still speak older revisions: "MCP servers that implement authorization MUST verify all inbound requests. MCP Servers MUST NOT use sessions for authentication. MCP servers MUST use secure, non-deterministic session IDs … SHOULD bind session IDs to user-specific information … `<user_id>:<session_id>`." — [Security Best Practices 2025-11-25](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)

**Streamable HTTP transport security**
- "Servers MUST validate the `Origin` header on all incoming connections to prevent DNS rebinding attacks. If the `Origin` header is present and invalid, servers MUST respond with HTTP 403 Forbidden." When running locally, servers "SHOULD bind only to localhost (127.0.0.1)". "Servers SHOULD implement proper authentication for all connections." — [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- New in 2026-07-28: every POST MUST carry `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` (for tools/call). Servers that process the body "MUST reject requests where the values specified in the headers do not match the corresponding values in the request body" with 400 and `-32020 HeaderMismatch`. The reason given is to stop "a load balancer routing on the header value while the MCP server executes based on the body value". Intermediaries that rate-limit per tenant using mirrored headers SHOULD reject older or absent versions. — [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- Server-to-client requests (sampling, elicitation) now come back embedded in results (MRTR, SEP-2322). They are no longer separate requests on SSE. Resumable SSE via `Last-Event-ID` is not supported. — [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)

**Python SDK caveats**
- CVE-2025-66416 / GHSA-9h52-p55h-vw2f: the MCP Python SDK did not enable DNS-rebinding protection by default for localhost HTTP servers. The fix landed in 1.23.0. — [GitHub Advisory](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-9h52-p55h-vw2f); [GitLab GLAD](https://advisories.gitlab.com/pypi/mcp/CVE-2025-66416/)
- Open issue #3562 (opened 2026-09-22, still open): when `security_settings` is omitted, `TransportSecurityMiddleware` defaults to `enable_dns_rebinding_protection=False`. Protection turns on automatically only for loopback binds, so a `0.0.0.0` or reverse-proxy deployment ships **without Host/Origin validation** unless it is configured explicitly. — [python-sdk #3562](https://github.com/modelcontextprotocol/python-sdk/issues/3562)
- The configuration surface is `TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[...], allowed_origins=[...])`. — [MCP Python SDK transport_security](https://py.sdk.modelcontextprotocol.io/api/mcp/server/transport_security/)

### Inferences
- The design has anonymous reads and authenticated writes. That maps onto a 401 challenge issued only on protected calls: Claude's "lazy authentication" pattern, plus a minimal `scopes_supported` (e.g. `procedures:write`, `runs:write`) and step-up 403s. Anonymous `tools/call` on read tools should never require a token.
- Since 2026-07-28 is stateless, the current `--workers 1` in-memory TasksExtension store (noted in CLAUDE.md) is a scaling and correctness constraint. It is not a security feature. Any task or workflow ID returned to clients must be a user-bound handle, keyed `<user_id>:<handle>` from the verified token. For anonymous callers there is no user to bind to, so anonymous handles should be unguessable and should grant no write capability.
- The spec requires Origin validation on *all* connections, including hosted ones. Because of the SDK default above, the server must pass explicit `allowed_hosts` and `allowed_origins`. Relying on the default is not enough.

### Gaps
- I did not fetch the 2026-07-28 changelog page itself. Which revision first introduced which requirement is only partly verified. CIMD appears in 2025-11-25 per Claude's docs, and DCR deprecation, stateless transport and RFC 9207 `iss` appear in 2026-07-28 per the pages above.
- How many clients have implemented 2026-07-28 (HeaderMismatch headers, no sessions) is unknown. Supporting 2025-11-25 alongside it is probably needed for backward compatibility.

---

## 2. What Claude.ai, Claude Code, ChatGPT/OpenAI, Cursor and VS Code expect

### Takeaway
Claude supports authless, OAuth (DCR or CIMD), Anthropic-held client credentials, and static headers (beta). It is stricter than the spec in several places:
- sign-in starts only from a 401;
- only the first `authorization_servers` entry is used;
- CIMD needs `token_endpoint_auth_methods_supported` to include `none`;
- the loopback redirect must match on any port;
- endpoints have 10 s and 30 s timeouts;
- traffic comes from 160.79.104.0/21.

OpenAI recommends CIMD and supports DCR and predefined clients. VS Code supports CIMD and DCR. For a small team, the plan should be a spec-compliant AS that serves CIMD, keeps DCR for legacy clients, and uses exact-match redirect allowlists.

### Cited Findings

**Claude (claude.ai, Desktop, mobile, Cowork, Claude Code)**
- Supported auth types:
  - `oauth_dcr`, `oauth_cimd`, `none`: supported by default
  - `oauth_anthropic_creds`, `custom_connection`: by request via mcp-review@anthropic.com
  - `static_headers`: beta, limited organizations

  "The same authentication infrastructure backs claude.ai, Claude Desktop, Claude mobile, Claude Code, and Cowork." — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- Where Claude is stricter than the spec:
  - "A `401` is required to start sign-in, and Claude ignores a `WWW-Authenticate` header on a `200`"
  - it "uses only the first entry in your metadata's `authorization_servers` list"
  - CIMD is used "only when your authorization server metadata advertises both `client_id_metadata_document_supported: true` and `none` in `token_endpoint_auth_methods_supported`", and otherwise falls back to DCR
  - `client_credentials` is not supported

  — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- The PRM `resource` "must equal the URL as the user enters it in Claude, including any path component." The AS metadata host must be reachable from Anthropic's egress range, and "a WAF in front of your identity provider can break the flow." — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- Callback URLs:
  - Hosted apps: exactly `https://claude.ai/api/mcp/auth_callback`. A third-party guide advises also allowlisting `https://claude.com/api/mcp/auth_callback` in case it changes. — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication); [Claude Help Center (via search)](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
  - Claude Code: an RFC 8252 loopback redirect on an ephemeral port. Its CIMD at `https://claude.ai/oauth/claude-code-client-metadata` declares `http://localhost/callback` and `http://127.0.0.1/callback`, "so match both with the port component ignored." — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- Claude always sends PKCE S256. The AS should advertise `"code_challenge_methods_supported": ["S256"]`. Claude requests the `scope` from the 401, or else `scopes_supported`, and appends `offline_access` if the AS lists it. — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- Token endpoint behaviour:
  - Claude refreshes on a 401 and proactively up to 5 minutes before expiry.
  - Return `invalid_grant` for dead refresh tokens.
  - "Rotate refresh tokens for public-client connections" (DCR and CIMD clients are public).
  - `/token` must accept `application/x-www-form-urlencoded`.
  - Timeouts: 10 s for discovery, registration and token; 30 s for refresh.

  — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- For high traffic, "prefer CIMD or `oauth_anthropic_creds` over DCR. DCR causes Claude to register a new client on every fresh connection." — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- Static header credentials: never accept them in query params, and "treat it as the organization's credential, not a person's." — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- "Anthropic's outbound traffic to your server originates from `160.79.104.0/21`." — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)

**OpenAI / ChatGPT**
- Supported clients "use Client ID Metadata Documents (CIMD), dynamic client registration (DCR), predefined OAuth clients, and PKCE". OpenAI "recommends CIMD when available". CIMD works with `none` or `private_key_jwt` token auth. — [OpenAI Plugins Authentication (search summary)](https://developers.openai.com/plugins/build/auth); [OpenAI MCP docs](https://developers.openai.com/api/docs/mcp)
- Community threads report friction over DCR requirements for Codex custom connectors. — [OpenAI community](https://community.openai.com/t/dynamic-client-registration-should-be-optional-for-custom-connectors/1356365)

**VS Code / Cursor**
- VS Code's MCP client supports both CIMD and DCR. — [den.dev: CIMD in VS Code](https://den.dev/blog/cimd-vs-code-mcp/); [Speakeasy CIMD vs DCR](https://www.speakeasy.com/blog/cimd-vs-dcr-mcp-oauth)
- GitHub Copilot CLI had an open request for CIMD support. — [copilot-cli #1305](https://github.com/github/copilot-cli/issues/1305)

### Inferences
- Minimum interop set:
  - PRM at both `/.well-known/oauth-protected-resource/mcp` and the root, and in the 401 header;
  - RFC 8414 AS metadata with `code_challenge_methods_supported: ["S256"]`, `client_id_metadata_document_supported: true`, `token_endpoint_auth_methods_supported` including `none`, a `registration_endpoint` (DCR fallback), and `authorization_response_iss_parameter_supported: true`;
  - redirect allowlist `https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`, loopback `localhost`/`127.0.0.1` on any port, plus each other client's documented callbacks.
- Do not IP-allowlist the MCP endpoint itself. It has anonymous reads and many clients (Claude Code and Cursor run from users' machines). Use the Anthropic range only for WAF exceptions.

### Gaps
- I did not get primary documentation of Cursor's current MCP OAuth callback URI or registration method.
- The OpenAI findings rely on a search summary of developers.openai.com. I did not fetch the full page.

---

## 3. Known MCP attack classes, standards, and incidents

### Takeaway
For this product the dominant risk is **prompt injection through tool results**, since procedure text is executed by agents. It is followed by cross-tenant leakage (Asana), supply-chain and poisoned-content writes (the submit path is effectively a "rug pull" vector), and SSRF in any server-side fetch. OWASP now publishes both the LLM Top 10 (2025) and an MCP Top 10 (beta). About 40 MCP CVEs were disclosed between January and April 2026, per secondary aggregators.

### Cited Findings

**Frameworks**
- OWASP MCP Top 10 (2025, beta / "Phase 3"):
  - MCP01 Token Mismanagement & Secret Exposure
  - MCP02 Privilege Escalation via Scope Creep
  - MCP03 Tool Poisoning
  - MCP04 Supply Chain & Dependency Tampering
  - MCP05 Command Injection & Execution
  - MCP06 Prompt Injection via Contextual Payloads
  - MCP07 Insufficient AuthN/AuthZ
  - MCP08 Lack of Audit and Telemetry
  - MCP09 Shadow MCP Servers
  - MCP10 Context Injection & Over-Sharing

  — [OWASP MCP Top 10](https://owasp.org/www-project-mcp-top-10/)
- OWASP Top 10 for LLM Applications 2025:
  - LLM01 Prompt Injection
  - LLM02 Sensitive Information Disclosure
  - LLM03 Supply Chain
  - LLM04 Data & Model Poisoning
  - LLM05 Improper Output Handling
  - LLM06 Excessive Agency
  - LLM07 System Prompt Leakage
  - LLM08 Vector & Embedding Weaknesses
  - LLM09 Misinformation
  - LLM10 Unbounded Consumption

  — [OWASP GenAI](https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/) (list via [Security Boulevard](https://securityboulevard.com/2026/03/the-owasp-top-10-for-llm-applications-2025-explained-simply/))

**Attack classes, as named in the MCP spec's Security Best Practices**
- **Confused deputy**: an MCP proxy with a static upstream client ID, DCR and consent cookies. Mitigation: per-client consent registry; consent UI showing client name, scopes and redirect_uri; CSRF protection; `frame-ancestors`/XFO DENY; `__Host-` Secure HttpOnly SameSite=Lax cookies; exact redirect match; single-use `state` with about 10 minutes TTL, set only after consent. — [Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)
- **Token passthrough**: see section 1.
- **SSRF**: during discovery, and against an AS that fetches CIMD URLs. Mitigations:
  - HTTPS only;
  - block 10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, fc00::/7, fe80::/10;
  - validate each redirect hop;
  - use an egress proxy such as Stripe Smokescreen;
  - pin DNS between check and use;
  - "Avoid implementing IP validation manually."

  — [Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)
- **State handle / session hijacking**: see section 1.
- **Scope minimization**. Common mistakes named: "Publishing all possible scopes in `scopes_supported`", wildcard scopes, and "Treating claimed scopes in token as sufficient without server-side authorization logic." — [Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)
- **Also named**: local server compromise, OAuth authorization-URL XSS/command injection (client side), stdio proxy escalation, mix-up attacks, localhost redirect impersonation, and CIMD trust policies. — [Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)

**Tool poisoning, rug pulls and shadowing (Invariant Labs, 2025-04-01)**
- Hidden instructions in tool descriptions are "invisible to users but visible to AI models".
- A rug pull is a server changing a tool description after approval.
- Shadowing is one server's descriptions altering how the agent uses another server's tools.
- Mitigations: show full descriptions, pin tools and packages by hash, add cross-server dataflow controls.

— [Invariant Labs](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)

**Incident timeline (curated by AuthZed, with a primary source for each)**
- **2025-04, WhatsApp MCP**: tool poisoning exfiltrated chat history. — [Invariant Labs](https://invariantlabs.ai/blog/whatsapp-mcp-exploited)
- **2025-05, GitHub MCP server**: prompt injection in a public issue plus an over-privileged PAT leaked private repo contents. — [Invariant Labs](https://invariantlabs.ai/blog/mcp-github-vulnerability)
- **2025-06, Asana MCP server**: a cross-tenant access-control flaw made one org's data visible to others. The bug was reportedly live for 34 days. — [UpGuard](https://www.upguard.com/blog/asana-discloses-data-exposure-bug-in-mcp-server); 34-day figure via [DEV summary](https://dev.to/razashariff/9-real-mcp-security-breaches-cves-data-leaks-and-why-the-protocol-needs-a-cryptographic-identity-ff6)
- **2025-06, CVE-2025-49596, MCP Inspector**: an unauthenticated localhost proxy gave RCE (CVSS 9.4). — [NVD](https://nvd.nist.gov/vuln/detail/CVE-2025-49596); [The Hacker News](https://thehackernews.com/2025/07/critical-mcp-remote-vulnerability.html)
- **2025-07, CVE-2025-6514, mcp-remote**: OS command injection via a malicious `authorization_endpoint`. CVSS 9.6, 437k+ downloads, affected 0.0.5–0.1.15, fixed in 0.1.16. — [JFrog](https://jfrog.com/blog/2025-6514-critical-mcp-remote-rce-vulnerability/); [GHSA-6xpm-ggf7-wc3p](https://github.com/advisories/GHSA-6xpm-ggf7-wc3p)
- **2025-08, Anthropic Filesystem MCP**: sandbox escape and symlink bypass (CVE-2025-53109/53110). — [Cymulate](https://cymulate.com/blog/cve-2025-53109-53110-escaperoute-anthropic/)
- **2025-09, postmark-mcp clone**: a malicious npm package added a BCC to every email (a supply-chain rug pull). — [IT Pro](https://www.itpro.com/security/a-malicious-mcp-server-is-silently-stealing-user-emails)
- **2025-10, Smithery registry**: path traversal in build config exfiltrated Docker credentials and a Fly.io token controlling 3,000+ apps. — [GitGuardian](https://blog.gitguardian.com/breaking-mcp-server-hosting/)
- **2025-10, Figma/Framelink MCP**: command injection. — [The Hacker News](https://thehackernews.com/2025/10/severe-figma-mcp-vulnerability-lets.html)
- **Patched 2025-12, disclosed 2026-01, Anthropic mcp-server-git**: CVE-2025-68143/68144/68145. Prompt-injected arguments (such as `git_init` on arbitrary paths) could lead to RCE. Reported June 2025, patched 2025-12-17. — [SecurityWeek](https://www.securityweek.com/anthropic-mcp-server-flaws-lead-to-code-execution-data-exposure/); [The Register](https://www.theregister.com/2026/01/20/anthropic_prompt_injection_flaws/)
- **2026-01, CVE-2026-0755, gemini-mcp-tool**: command injection. — [CVE.org](https://www.cve.org/CVERecord?id=CVE-2026-0755)
- **2026-03, CVE-2026-33032, nginx-ui**: auth bypass on the MCP endpoint, 2,600+ exposed instances. — [NVD](https://nvd.nist.gov/vuln/detail/CVE-2026-33032)
- **2026-04**: STDIO-transport command execution across Letta, LangFlow and Windsurf. — [OX Security](https://www.ox.security/blog/the-mother-of-all-ai-supply-chains-technical-deep-dive/)
- Timeline source: [AuthZed timeline](https://authzed.com/blog/timeline-mcp-breaches)
- The "40+ CVEs Jan–Apr 2026" and "30 CVEs in 60 days" figures come from secondary aggregators, not an authoritative count. — [DEV](https://dev.to/piiiico/mcp-security-vulnerabilities-in-2026-40-cves-and-counting-4pco); [agent-wars](https://agent-wars.com/news/2026-03-13-mcp-security-2026-30-cves-in-60-days-what-went-wrong)

### Inferences
- **Content injection is the core product risk.** A write path (`submit_way`, `report_discovery`) that lands attacker-authored text in procedures served to other tenants' agents is a stored tool-result injection. It is a worse version of the GitHub-MCP pattern. Required controls:
  - no cross-tenant serving of unverified submissions;
  - the existing redaction/V0 gate plus an injection classifier on writes;
  - serving procedures as clearly delimited *data*, with provenance and version pinning (a content hash, so a later edit cannot silently "rug pull" a procedure an agent already trusts);
  - keeping imperative instructions that address the agent ("ignore previous", "run curl … | sh") out of procedure bodies, or flagging them;
  - never including secrets, URLs to fetch-and-execute, or shell pipelines without an explicit risk label.
- Tool descriptions should be static and versioned. `notifications/tools/list_changed` should not be used to silently change semantics, since clients that pin by hash will flag it.
- The Asana incident is the direct analogue for a multi-tenant RLS system. It argues for automated cross-tenant leak tests in CI (section 5).

### Gaps
- No primary CVE count exists.
- I found no public incident write-up specifically about a "procedure/memory" MCP server being poisoned. The analogy to stored prompt injection is an inference.

---

## 4. Token lifecycle and managed identity providers

### Takeaway
The spec wants short-lived, audience-bound access tokens, rotated refresh tokens for public clients, and least-privilege, step-up scopes. Several IdPs now ship MCP-ready authorization servers (PRM, DCR, CIMD, PKCE) with free tiers large enough for a startup. Cloudflare's workers-oauth-provider is a free, MIT-licensed option if the AS is self-hosted on Workers. Pricing figures below are mostly from aggregators and should be re-checked.

### Cited Findings
- Spec: short-lived access tokens (SHOULD); rotate refresh tokens for public clients (MUST); secure token storage (MUST). — [Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)
- Claude-specific: return `invalid_grant` on a revoked or expired refresh token, and return the new refresh token in the same response that invalidates the old one. — [Claude connectors: Authentication](https://claude.com/docs/connectors/building/authentication)
- **WorkOS AuthKit**: "OAuth 2.1 flows, tool permissions, PKCE, scopes" for MCP. — [WorkOS MCP](https://workos.com/mcp)
  - Free up to 1,000,000 MAU. — [WorkOS changelog](https://workos.com/changelog/introducing-authkit-and-user-management)
  - $2,500/month per additional million; SSO connections from $125/month each. — aggregator [IDSync](https://idsync.com/guides/workos-pricing); [WorkOS pricing](https://workos.com/pricing)
- **Auth0**: "Auth for MCP" is GA. — [Auth0 blog](https://auth0.com/blog/auth0-auth-for-mcp-servers-generally-available/)
  - DCR is a tenant toggle, and Auth0 recommends CIMD over DCR for production. — [Auth0 DCR docs](https://auth0.com/ai/docs/mcp/guides/registering-your-mcp-client-application/dynamic-client-registration); summary via search
  - Free to 25,000 MAU. Essentials is $35/month (B2C) or $150/month (B2B); Professional is $240/month or $800/month. — aggregator [costbench](https://costbench.com/software/identity-access-management/auth0/)
- **Stytch Connected Apps**: turns the app into an OAuth 2.1/OIDC provider "with full DCR and CIMD support", handling consent screens and token lifecycle. Free for the first 10,000 active users and agents. — [Stytch Connected Apps](https://stytch.com/connected-apps); [Stytch MCP guide](https://stytch.com/docs/guides/connected-apps/mcp-server-overview)
- **Descope** (Agentic Identity Hub): an OAuth-compliant layer for MCP covering PKCE, client registration, consent and token issuance. — [Descope vs Stytch](https://www.descope.com/blog/post/descope-vs-stytch) (vendor source)
- **Clerk**: supports both CIMD and DCR. When DCR is enabled, "the OAuth consent screen is automatically enforced and cannot be disabled". — [Clerk MCP docs](https://clerk.com/docs/guides/ai/mcp/connect-mcp-client); [Clerk OAuth improvements](https://clerk.com/changelog/2025-06-13-oauth-improvements)
- **Cloudflare workers-oauth-provider** (MIT):
  - implements OAuth 2.1 with PKCE, CIMD, DCR (RFC 7591), RFC 8414, RFC 9728, RFC 8707 and RFC 7009 revocation;
  - "Tokens, codes and secrets are stored only as hashes" in KV, with user props encrypted by a key only the token holder has;
  - CIMD needs the `global_fetch_strictly_public` compatibility flag, which acts as SSRF protection.

  — [GitHub cloudflare/workers-oauth-provider](https://github.com/cloudflare/workers-oauth-provider)

### Inferences
- Recommended token model for this product:
  - access tokens with a 5–15 minute lifetime and `aud` equal to the canonical MCP URL;
  - rotating refresh tokens with reuse detection, which revokes the family;
  - scopes `procedures:read` (optional, since reads are anonymous), `procedures:submit`, `runs:report`, and never a wildcard;
  - per-token and per-user rate limits in addition to per-IP limits for anonymous reads (the repo already has an H3 rate limiter);
  - a revocation endpoint and a user-visible "connected apps" page.
- For a Python backend with 2–4 people, an external AS is simplest: WorkOS (largest free tier) or Stytch/Auth0. The MCP server then only validates JWTs: signature via JWKS, `iss`, `aud`, `exp`, `scope`. Hand-rolling an AS is the higher-risk path.
- Personal access tokens (the kind Claude "static headers", CI or the local executor might use) should be prefixed (e.g. `slk_…`) so GitHub secret scanning custom patterns can catch them. They should be stored hashed, scoped, and expiring.

### Gaps
- I did not verify current official pricing pages for Auth0, Stytch, Descope or Clerk (only aggregators and vendor marketing).
- Google Identity Platform's MCP support (DCR/CIMD) was not researched. My unverified belief is that it lacks native DCR/CIMD; confirm before relying on it.
- Default token TTLs and rotation settings per vendor were not collected.

---

## 5. Multi-tenant isolation with Postgres RLS

### Takeaway
RLS is a strong backstop only if:
- the application connects as a non-owner, non-superuser, non-BYPASSRLS role;
- `FORCE ROW LEVEL SECURITY` is set where owners might connect;
- tenant context is set transaction-locally (`SET LOCAL` / `set_config(..., true)`), never with plain `SET` on pooled connections;
- views, SECURITY DEFINER functions and constraints are audited.

The repo's `tenant_transaction()` pattern already binds `app.tenant_id` transaction-locally. The remaining work is role hygiene and automated leak tests.

### Cited Findings
- "Superusers and roles with the BYPASSRLS attribute always bypass the row security system." Table owners bypass RLS unless `ALTER TABLE … FORCE ROW LEVEL SECURITY`. — [PostgreSQL docs: Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- RLS enabled with no policy means default deny. Permissive policies combine with OR and restrictive ones with AND. — [PostgreSQL docs](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- Referential-integrity checks (FK, unique, PK) bypass RLS and can act as covert channels. — [PostgreSQL docs](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- Policies with sub-SELECTs can race under concurrency. — [PostgreSQL docs](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- `SET row_security = off` raises an error instead of silently filtering, which is useful for detecting unexpected filtering in backups and tests. — [PostgreSQL docs](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- PgBouncer in transaction mode does not run `DISCARD ALL` between clients, so a plain `SET app.tenant` leaks to the next client. Use `SET LOCAL` or `set_config(..., true)`. Other session state (search_path, prepared statements, advisory locks) leaks the same way. — [Seedfast: PgBouncer transaction mode](https://seedfa.st/blog/pgbouncer-transaction-mode); [Patotski: RLS footguns](https://patotski.com/blog/postgres-row-level-security-multi-tenant/)
- Connect as "a dedicated non-owner, non-superuser role". — [Patotski](https://patotski.com/blog/postgres-row-level-security-multi-tenant/)

### Inferences
- Checklist:
  1. The app role has `NOLOGIN` owner separation, `NOBYPASSRLS`, and is not a superuser. Migrations run as a separate owner role.
  2. `ENABLE` + `FORCE ROW LEVEL SECURITY` on every tenant table.
  3. Policies use `current_setting('app.tenant_id', true)`, with the missing-setting case resulting in deny, not `TRUE`.
  4. Views over tenant tables are created `WITH (security_invoker = true)` (PG15+), because by default views run with the owner's rights and bypass the caller's RLS.
  5. Audit every `SECURITY DEFINER` function: set a fixed `search_path` and filter tenants inside it.
  6. Avoid tenant-revealing unique constraints (e.g. a global unique on user-supplied slugs).
  7. pgvector/ANN queries must still pass RLS. Watch for leaks of filtered-out rows through result counts or timing.
- Leak tests to run in CI:
  - seed two tenants and assert that every read tool and SQL path, run under tenant A, returns zero B rows;
  - a property/fuzz test over the tool inputs;
  - a test that asserts `current_setting('app.tenant_id')` is empty on a freshly borrowed pool connection;
  - a `pg_roles` check that asserts the app role lacks `rolbypassrls` and `rolsuper`;
  - a catalog query listing tables in tenant schemas where `relrowsecurity` or `relforcerowsecurity` is false.
- For the procedure commons, "anonymous read" means the anonymous path needs its own explicit scope (e.g. `TenantScope.commons()`). A test should prove that anonymous callers never see private-tenant rows.

### Gaps
- I did not fetch primary PostgreSQL docs on `security_invoker` views or leakproof functions. Item 4 above is from background knowledge; verify it against the [CREATE VIEW docs](https://www.postgresql.org/docs/current/sql-createview.html).

---

## 6. Supply chain and repo hygiene for a 2–4 person team

### Takeaway
Realistic baseline:
- GitHub push protection and secret scanning (free on public repos; $19 per active committer per month on private);
- gitleaks as a pre-commit hook and in CI;
- Dependabot or pip-audit with pinned, hashed lockfiles;
- GitHub artifact attestations for the container image and the `stealthlab-connect` wheel, which give SLSA Build L2, or L3 via reusable workflows;
- cosign-verified images, plus Binary Authorization if on GKE or Cloud Run.

Full SLSA L3 and hermetic builds are optional at this stage.

### Cited Findings
- On 2025-04-01 GitHub split Advanced Security into **Secret Protection** ($19/month per active committer, includes push protection) and **Code Security**. Both became available to Team-plan orgs. — [GitHub changelog 2025-03-04](https://github.blog/changelog/2025-03-04-introducing-github-secret-protection-and-github-code-security/); [GitHub Docs pricing](https://docs.github.com/code-security/securing-your-organization/understanding-your-organizations-exposure-to-leaked-secrets/choosing-github-secret-protection)
- Push protection for public repos is free. — [GitHub Secret Protection](https://github.com/security/advanced-security/secret-protection) (via search summary)
- SLSA v1.0 build levels: L1 means provenance exists; L2 means a hosted build platform with signed provenance; L3 means isolated runs and signing keys unreachable from user steps. — [GitHub Docs: artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations)
- GitHub "Artifact attestations by itself provides SLSA v1.0 Build Level 2". Reaching L3 means running the build in reusable workflows. Provenance is produced with `actions/attest-build-provenance`. — [GitHub Docs: SLSA v1 Build L3](https://docs.github.com/actions/security-guides/using-artifact-attestations-and-reusable-workflows-to-achieve-slsa-v1-build-level-3); [GitHub blog](https://github.blog/enterprise-software/devsecops/enhance-build-security-and-reach-slsa-level-3-with-github-artifact-attestations/)
- Supply-chain incidents in the MCP ecosystem: the postmark-mcp malicious package, the Smithery registry credential exfiltration, and a cloned Oura MCP repo spreading StealC. — [AuthZed timeline](https://authzed.com/blog/timeline-mcp-breaches)

### Inferences
- A two-week "realistic" list:
  - push protection, secret scanning and Dependabot alerts enabled;
  - a gitleaks pre-commit hook;
  - `pip install --require-hashes` from a lockfile (uv/pip-tools);
  - Actions pinned to commit SHAs with `permissions: {}` defaults;
  - OIDC federation to the cloud, with no long-lived deploy keys;
  - signed image and wheel attestations;
  - publishing `stealthlab-connect` via PyPI Trusted Publishing (PyPI attestations);
  - branch protection with required review;
  - secrets held in a secret manager and never in `.env` inside images.
- The repo's `.gitignore` of `backend/.env` and `FundingGrants/` is necessary but not sufficient. The untracked `backend/.env.bak.*` in git status shows how backup files can slip past patterns; widen the ignore to `.env*`.

### Gaps
- I did not fetch cosign/Sigstore or Google Binary Authorization docs or pricing. The recommendation rests on general knowledge.
- I did not verify whether PyPI Trusted Publishing attestations currently count toward SLSA levels.

---

## 7. Security of the local executor (agent CLIs with auto-approve in git worktrees)

### Takeaway
Running `claude --dangerously-skip-permissions` (or the equivalent in Codex or Cursor) against procedures from a remote server gives an injected procedure host code execution with the user's credentials. Treat the executor as running untrusted code:
- container or VM isolation, or at least Claude Code's OS sandbox in strict mode;
- a network egress allowlist, knowing that domain fronting remains possible;
- no ambient credentials;
- worktrees as the only writable area;
- human review before merge.

### Cited Findings
- Claude Code's sandbox uses Seatbelt on macOS and bubblewrap on Linux/WSL2. "Native Windows is not supported." Sandboxed commands can write only to the working directory, a per-user temp directory, and added directories. — [Claude Code: sandboxing](https://code.claude.com/docs/en/sandboxing)
- In a linked **git worktree**, the sandbox also allows writes to the main repo's shared `.git`, but "Writes to `hooks/` and `config` inside that directory remain denied". `.claude` settings, `.mcp.json`, shell rc files and `.git/hooks` are always write-protected. — [Claude Code: sandboxing](https://code.claude.com/docs/en/sandboxing)
- `"allowUnsandboxedCommands": false` makes Claude Code ignore the `dangerouslyDisableSandbox` escape hatch ("Strict sandbox mode"). — [Claude Code: sandboxing](https://code.claude.com/docs/en/sandboxing)
- Documented limits:
  - "Sandboxing reduces risk but is not a complete isolation boundary."
  - The proxy "does not terminate or inspect TLS", and "code running inside the sandbox can potentially use domain fronting".
  - "Allowing broad domains such as `github.com` can create paths for data exfiltration."
  - The default read policy still allows `~/.aws` and `~/.ssh` unless `sandbox.credentials` entries are added.

  — [Claude Code: sandboxing](https://code.claude.com/docs/en/sandboxing)
- "Effective sandboxing requires both filesystem and network isolation." — [Claude Code: sandboxing](https://code.claude.com/docs/en/sandboxing)
- Devcontainers make `--dangerously-skip-permissions` workable for unattended runs. However, "devcontainers do not prevent a malicious project from exfiltrating anything accessible in the devcontainer including Claude Code credentials". Anthropic recommends them only for trusted repositories. — [Claude Code: devcontainer](https://docs.anthropic.com/en/docs/claude-code/devcontainer)
- Real incidents from agent-driven tool calls with attacker-controlled arguments: mcp-server-git `git_init` on arbitrary paths, and Filesystem MCP symlink escape. — [SecurityWeek](https://www.securityweek.com/anthropic-mcp-server-flaws-lead-to-code-execution-data-exposure/); [Cymulate](https://cymulate.com/blog/cve-2025-53109-53110-escaperoute-anthropic/)
- MCP spec guidance for local HTTP servers: bind to 127.0.0.1, require an auth token, or use Unix sockets/IPC. — [Security Best Practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)

### Inferences
- Executor controls:
  1. Run each job in a disposable container or microVM as a non-root user, with only the worktree mounted.
  2. Default-deny egress through a proxy, allowing only the model API and package registries.
  3. Inject short-lived, narrowly scoped API keys; mount no `~/.ssh`, cloud credentials or `gh` tokens.
  4. On native Windows, which this dev machine is and where the Claude sandbox is unsupported, require WSL2 or Docker.
  5. Pin procedure versions by content hash, and log which procedure drove which commands.
  6. Never auto-push or auto-merge; require human review of the diff (this matches the repo's "named human approves" product rule).
  7. Cap runtime, CPU, memory and disk.
  8. Any local status/HTTP surface (e.g. the status page on 127.0.0.1:8766) binds loopback only, validates Host/Origin, and requires a token.

### Gaps
- I did not research Codex CLI's or Cursor's sandbox and auto-approve modes.
- I found no primary data on real-world exfiltration through domain fronting from agent sandboxes. It is a documented theoretical limitation.

---

## Consolidated control list (for the report writer)

**Transport and HTTP**
- Explicit `TransportSecuritySettings` with `allowed_hosts` and `allowed_origins`; 403 on a bad Origin.
- HTTPS only, HSTS, a request-body size cap.
- 405 on GET/DELETE (2026-07-28); ignore `Mcp-Session-Id`.
- Validate `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` against the body (HeaderMismatch -32020).

**AuthN/AuthZ**
- PRM at the well-known path and in the 401 `WWW-Authenticate`.
- `resource` in PRM exactly equal to the public MCP URL.
- JWT validation of signature, `iss`, `aud`, `exp`, `nbf` and `scope` on every request.
- Reject tokens for other audiences; never forward client tokens upstream.
- Per-tool scope checks server-side, with 403 `insufficient_scope` step-up.
- Anonymous read tools; lazy 401 on write tools.

**Authorization server (external IdP preferred)**
- S256 PKCE advertised.
- CIMD (with `none` auth method) plus a DCR fallback.
- Exact-match redirect URIs, including the Claude callbacks and loopback on any port.
- `iss` in the authorization response.
- Short access tokens and rotating refresh tokens with `invalid_grant` semantics.
- A consent screen showing the redirect hostname.
- SSRF-safe CIMD fetching.

**Data and tenancy**
- A non-owner, NOBYPASSRLS app role; FORCE RLS.
- Transaction-local tenant GUC; `security_invoker` views; audited SECURITY DEFINER functions.
- A CI cross-tenant leak suite.
- User-bound handles.

**Content and injection (the product-specific surface)**
- Write-path redaction plus injection screening.
- Unverified submissions quarantined from other tenants.
- Procedures served as delimited data with provenance, version and content hash.
- Static, versioned tool descriptions.
- Output limits.

**Abuse and ops**
- Per-IP limits for anonymous reads; per-token and per-user limits for writes; cost caps on LLM-spending paths.
- Structured audit logs with correlation IDs and scope-elevation events, and no tokens in logs.
- An egress proxy for any server-side URL fetch.

**Supply chain**
- Push protection, secret scanning, gitleaks and Dependabot.
- Hashed lockfiles; SHA-pinned Actions; OIDC deploys.
- Build attestations (SLSA L2 to L3); cosign-signed images; PyPI Trusted Publishing.

**Local executor**
- Container or VM; default-deny egress; no ambient credentials.
- Strict sandbox (`allowUnsandboxedCommands: false`); worktree-only writes.
- Human merge gate; loopback-only, authenticated local servers.

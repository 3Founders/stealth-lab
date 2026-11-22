# Ways a signed-in person can connect their own model access to a web app (beyond pasting a key), as of 2026-10-28

## Which two or three flows to build first, and in what order

### Takeaway
Build, in order: (1) OpenRouter "Connect" via PKCE, which is fully browser-only and yields a user-owned, spend-limitable key; (2) .env-block import, which is browser-only and covers every provider that has no OAuth; (3) local models (Ollama/LM Studio) detection, with a documented permission step. Hugging Face OAuth (scope `inference-api`) is a good fourth if you want a second real "connect" button. Never build anything that collects Claude.ai or ChatGPT consumer logins or session tokens.

### Cited Findings
- OpenRouter PKCE needs no client registration or backend in the documented flow; it ends with a user-controlled API key returned from a POST — [OpenRouter OAuth docs](https://openrouter.ai/docs/guides/overview/auth/oauth), [exchange endpoint reference](https://openrouter.ai/docs/api/api-reference/oauth/exchange-authorization-code-for-api-key.md)
- Hugging Face supports public OAuth apps (no secret) using PKCE, with scope `inference-api` ("make inference requests to Inference Providers on behalf of the user"); apps can be auto-registered via a CIMD document at `/.well-known/oauth-cimd` — [HF OAuth docs](https://huggingface.co/docs/hub/oauth)
- No documented OAuth "connect" flow was found for Anthropic API, OpenAI API, Groq, Together, Fireworks, DeepInfra, Mistral or Cohere in this research. This is absence of evidence from limited searching, not confirmation (see Gaps).
- Google: Gemini CLI's FAQ says piggybacking on Gemini CLI's OAuth in third-party tools violates terms and recommends a Vertex AI or AI Studio API key instead — [Gemini CLI FAQ via mintlify mirror](https://www.mintlify.com/google-gemini/gemini-cli/resources/faq) (secondary mirror).

### Inferences
- Order rationale: OpenRouter gives one-click connect with no backend and access to many models; .env import is almost free and covers Anthropic/OpenAI/Groq users who cannot OAuth; local models serve privacy-minded users but have a browser-permission step that varies.
- Everything in 1-3 can stay "keys remain in the browser" and the server mode can be optional.

### Gaps
- Not verified: whether Together, Groq, Fireworks, DeepInfra, Mistral, Cohere offer any OAuth/connect flow; only absence in search.

## OpenRouter OAuth PKCE: CORS, redirect URI, state/PKCE handling

### Takeaway
The exchange endpoint allows browser requests: I tested it directly. A pure client-side app can complete the flow. Use S256, a random verifier kept in sessionStorage, a random `state` checked on return, an exact-path HTTPS callback, and remove `code` from the URL immediately.

### Cited Findings
- Step 1: redirect to `https://openrouter.ai/auth` with `callback_url`, `code_challenge` (optional but recommended), `code_challenge_method` (`S256` recommended or `plain`), optional `key_label`, `workspace_id`, `required_workspace_id`, and `state` (echoed back unchanged for CSRF protection; not returned if the user denies) — [OpenRouter OAuth docs](https://openrouter.ai/docs/guides/overview/auth/oauth)
- S256 challenge = base64url(SHA-256(code_verifier)) — same docs.
- Step 2: `POST https://openrouter.ai/api/v1/auth/keys` with JSON `{code, code_verifier, code_challenge_method}`; response `{key, user_id}` — [endpoint reference](https://openrouter.ai/docs/api/api-reference/oauth/exchange-authorization-code-for-api-key.md)
- Codes are single-use and expire after 10 minutes; errors: 400 method mismatch, 403 invalid code/verifier or expired, 405 wrong method — [OpenRouter OAuth docs](https://openrouter.ai/docs/guides/overview/auth/oauth)
- Localhost callbacks work on any port; localhost apps are labelled `localhost:PORT` and not listed in the marketplace; use a public URL for production — same docs.
- CORS (my own test, 2026-10-28, from this machine): `OPTIONS /api/v1/auth/keys` with `Origin: https://example.com` returned 204 with `Access-Control-Allow-Origin: *`, `Allow-Methods: GET,OPTIONS,PATCH,DELETE,POST,PUT`, and `Allow-Headers` including Content-Type and Authorization. A `POST` with that Origin returned 400 (bad code, as expected) and also carried `Access-Control-Allow-Origin: *`. The official docs say nothing about CORS; this is an empirical observation and could change.
- The docs also show a headless mode (no `callback_url`, code shown on screen, `code_challenge` required) — OpenRouter OAuth docs.
- Community guidance (not official) suggests sessionStorage for the verifier — [openrouter-oauth skill](https://openrouter.ai/skills/openrouter-oauth).

### Inferences
- Credential held: a standard OpenRouter API key owned by the user (revocable at openrouter.ai). The app can store it in browser storage (default) or send to the server in server mode. Treat it like a pasted key: XSS exposes it.
- Recommended handling (my synthesis; general OAuth practice per RFC 7636/OAuth security BCP, not fetched this session): generate 32+ random bytes verifier and separate random `state` with `crypto.getRandomValues`; store both in sessionStorage; callback_url a fixed route like `https://app.example/connect/openrouter` with no wildcard; on return verify `state` equality first, exchange, then `history.replaceState` to strip `code`/`state`, delete verifier; set `Referrer-Policy: no-referrer` on the callback page; use S256 only (never `plain`); label the key via `key_label`.
- After exchange, prompt user to set a credit limit on the key in OpenRouter (key limits not documented on the OAuth page; see Gaps).
- Terms: OpenRouter documents this flow explicitly for third-party apps, so it is permitted by design.

### Gaps
- Key scopes, default spending limits and revocation behavior of OAuth-created keys are not stated on the pages fetched.
- Whether OpenRouter restricts callback_url hosts beyond the localhost/public guidance: not documented in fetched pages.
- CORS is observed, not documented; add a fallback (server-side exchange route) if it ever changes.

## Provider terms: flows that are NOT allowed

### Takeaway
Do not offer Claude.ai or ChatGPT consumer login, session-token capture, or routing through someone's subscription via a web app. Anthropic prohibits it explicitly. OpenAI has a new, partner-gated "Sign in with ChatGPT" program, which is not open to arbitrary apps as far as verified.

### Cited Findings
- Anthropic: "Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users. Moreover, developers may not collect, store, or intermediate Claude.ai credentials or session tokens." Developers should use API keys via Claude Console or a supported cloud provider — [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
- The same page allows users to configure their own API keys or 3P provider credentials, provided usage is billed to the key owner and not resold or intermediated — same source.
- Press coverage dates the clarification to February 2026 — [Winbuzzer](https://winbuzzer.com/2026/02/19/anthropic-bans-claude-subscription-oauth-in-third-party-apps-xcxwbn/), [Gigazine](https://gigazine.net/gsc_news/en/20260220-anthropic-third-party-block) (secondary).
- OpenAI business terms bar making account credentials available to third parties and reselling account access — [OpenAI business terms](https://openai.com/policies/business-terms) (as summarized by search; I did not read the full text). Consumer-terms equivalent only seen via a third-party blog; treat as unverified.
- OpenAI "Sign in with ChatGPT" for third-party apps: reported (secondary press) as available for Plus and Pro users with 16 launch partners (Devin, Notion, Vercel, OpenCode, etc.); commercial/remote-server apps reportedly face additional requirements; dates conflict between outlets — [ProPakistani](https://propakistani.pk/2026/10/23/your-chatgpt-subscription-can-now-work-in-other-apps/), [IT Brief](https://itbrief.co.uk/story/openai-launches-chatgpt-sign-in-for-16-partner-tools), [The New Stack](https://thenewstack.io/sign-in-with-chatgpt/)
- Google: using Gemini CLI OAuth from third-party tools is stated as a terms violation — [Gemini CLI FAQ mirror](https://www.mintlify.com/google-gemini/gemini-cli/resources/faq)

### Inferences
- Treat "Sign in with ChatGPT" as unavailable to this team until OpenAI publishes a developer path; the only sanctioned route is partner onboarding. Do not scrape tokens from Codex or other CLIs.
- Any pasted-token field labelled "Claude session token" or "ChatGPT cookie" must not exist.

### Gaps
- I did not read OpenAI's primary developer documentation for Sign in with ChatGPT, nor the Anthropic Consumer Terms text itself.

## Other flows: .env import, local helper, BYO cloud, local models

### Takeaway
.env import and local models are feasible browser-only. A local helper works but adds install friction. Bedrock/Vertex credentials should not be pasted into a web app; use a server-side or user-owned-gateway pattern.

### Cited Findings
- .env import (3): no external source needed; this is a client-side parse. Anthropic's terms allow users to supply their own API keys (see Claude Code legal page above). Variable names to detect: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY` (names are provider conventions; I did not verify each in docs this session).
- Ollama: default binds 127.0.0.1:11434; allows cross-origin from 127.0.0.1 and 0.0.0.0 by default; other origins require `OLLAMA_ORIGINS` — [Ollama FAQ](https://docs.ollama.com/faq)
- Chrome Local Network Access: Chromium 142+ requires user permission when a public origin fetches a local/loopback address; Chrome 145 split the setting into `loopback-network` ("Apps on device") and `local-network`; blocked requests fail with a network error; enterprise policy `LocalNetworkAccessAllowedForUrls` can pre-allow — [Dynamsoft note](https://www.dynamsoft.com/web-twain/docs/faq/chromium-142-local-network-access-issue.md), [VertiGIS](https://support.vertigis.com/hc/en-us/articles/32555952561682-Local-Network-Access-permissions-in-Chrome-and-Edge), [OpenReplay](https://blog.openreplay.com/chrome-local-network-access-lna-permission/) (secondary sources)
- Earlier Chrome PNA blog: private-network requests require secure contexts since Chrome 94; PNA rollout "on hold" at that time — [Chrome blog 2024-03](https://developer.chrome.com/blog/private-network-access-update-2024-03)

### Inferences
- Local models from an HTTPS site: the origin must be allowed by Ollama (`OLLAMA_ORIGINS=https://yourapp.example`), and Chrome will show a permission prompt. Offer a "Test connection" button and copy-paste setup instructions. Firefox/Safari behavior unverified.
- Local helper (4): a loopback server (random port, bearer token pasted once, strict Origin allowlist, CORS only for your origin, bind 127.0.0.1) avoids keys in the browser, but needs the same LNA permission and an install; effort medium-high. Not built in the first three.
- BYO cloud (5): Bedrock and Vertex use AWS SigV4/Google OAuth with long- or short-lived credentials; pasting these into a browser app is higher risk than API keys. Safer options: user creates a narrowly scoped IAM user/service key with only model-invoke permission, or server mode with a stored encrypted secret. Not verified this session; no sources fetched for Bedrock/Cognito/Vertex.

### Gaps
- No primary Chrome LNA documentation was read (only secondary sources); localhost-to-localhost exemption claim is from a single blog.
- No sources fetched for Azure OpenAI, Bedrock STS/Cognito, Vertex, LM Studio CORS settings, or Firefox/Safari behavior.
- Search was US-only and limited to roughly 15 calls.

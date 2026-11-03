# Authentication and payment protocols for kell

Research note, 3 November 2026. Scope: how kell should sign people in, how it should get access to third-party
tools, and how it could let a person pay through an agent without turning an access token into a blank cheque.
Sources are listed at the end; the standards are the authority, the papers are early evidence.

## The short version

1. **Identity and payment are different questions.** OpenID Connect (Google, Apple, Microsoft) proves who a person
   is. It does not prove that they agreed to a payment. kell must never treat a Google sign-in, or an OAuth access
   token, as payment authorisation.
2. **Use OAuth 2.1 with PKCE S256 for every third-party tool**, and ask for the smallest scope that does the job.
   Prefer Client ID Metadata Documents for MCP clients; keep Dynamic Client Registration as the fallback while
   servers still require it. This is what the current kell gateway already does, with DCR.
3. **For low-security actions, a normal OAuth grant is enough**: read a profile, list files, draft a document.
   Ask for more only when the person asks for the action that needs it (incremental authorisation).
4. **For payments, use a payment protocol, not a login.** A signed, scoped mandate (AP2), a shared payment token
   (Stripe/OpenAI ACP), or a network agentic token (Visa Intelligent Commerce, Mastercard Agent Pay) is the thing
   that authorises money. The person approves the exact merchant, amount, currency and time window.
5. **Every payment needs a human-visible confirmation** of what will be charged, a spend limit, an expiry, an
   idempotency key, a receipt, and a one-step revoke. Strong Customer Authentication (PSD2 SCA, 3-D Secure) is the
   fallback when the issuer or the law asks for it.
6. **Keep tokens server-side and short-lived.** A desktop or chat client should hold a kell session, never a
   third-party refresh token in plain text. Tokens are sealed at rest and revoked when the person disconnects.

## 1. The layers, and what each one is for

| Layer | Protocol | Proves | Use in kell |
| --- | --- | --- | --- |
| Who the person is | OpenID Connect 1.0 (Google, Apple, Microsoft) | The person authenticated to an identity provider | kell account sign-in only |
| What kell may do for them at a service | OAuth 2.1 + PKCE, scopes, resource indicators | A delegated, limited right to call an API | Third-party MCP tools, low-risk actions |
| What a specific action may do | Rich Authorization Requests (RFC 9396) | The exact object, amount and actions consented to | Payment initiation, high-impact actions |
| That money may move | AP2 mandates, ACP shared payment tokens, network agentic tokens | A signed instruction bound to a cart/checkout | Payments, never a login token |
| That the payer was present/verified | PSD2 SCA, 3-D Secure | Strong customer authentication | Step-up when required |

A useful rule: **authentication answers "who", authorisation answers "what", a mandate answers "how much and to
whom".** Mixing them is the classic agent-payments bug.

## 2. Sign-in with Google and OpenID Connect

- OIDC returns an **ID token** (a signed statement about the authentication) and usually an access token. The ID
  token is for identity. It is not a permission to spend.
- Google's own guidance is **incremental authorisation**: request the sign-in scopes first (`openid`, `profile`,
  `email`), then request Calendar, Drive, Gmail or other scopes only when the person asks for an action that needs
  them. Requesting everything up front trains people to click through consent and makes a breach much worse.
- Handle a denied scope by disabling that feature and explaining why, not by re-prompting in a loop.
- For kell: keep the Supabase/OIDC session as the account identity. Store third-party grants separately, per
  account, with the scope text shown in Settings. The existing "tools that need a sign-in" preference is the right
  shape: one switch, per account, changeable in chat or in Settings, default on.

**What Google sign-in is good for:** a low-risk action where the worst case is an unwanted read or a draft, for
example "look at this file" or "put this on my calendar". **What it is not good for:** moving money, deleting an
account, changing a password, or granting a long-lived token to an agent that acts unattended.

## 3. OAuth 2.1 and MCP tool authorisation

The MCP authorisation specification (2026-07-28) expects an OAuth 2.1 authorisation server and a protected
resource. The parts that matter for kell:

- **PKCE with S256 is mandatory for public clients** and recommended for confidential ones (RFC 9700, RFC 7636).
  The kell gateway already uses S256.
- **Client registration, in priority order:** pre-registered client, then **Client ID Metadata Documents** (an
  HTTPS URL that is the client ID and serves the client metadata), then **Dynamic Client Registration** (RFC 7591)
  as a compatibility fallback. The spec now calls DCR deprecated for new implementations. kell should add CIMD
  when a server advertises `client_id_metadata_document_supported`, and keep DCR for the servers that only do DCR
  (Kinsy, MagicSlides, Plus AI and Presentations.AI all issued DCR clients in the check on 3 November 2026).
- **Resource indicators (RFC 8707)** bind a token to one resource server, so a token minted for one MCP server
  cannot be replayed at another.
- **Discovery:** protected-resource metadata at `/.well-known/oauth-protected-resource` (and the path variant),
  then authorisation-server metadata. Some servers publish the AS metadata at the host root even when the issuer
  has a path (Presentations.AI is one); kell's discovery now tries both shapes.
- **Native and desktop clients** should use the system browser with a loopback redirect (RFC 8252), never an
  embedded web view. The kell desktop app already sends people to the browser and comes back to the callback.
- **Token handling:** short-lived access tokens, rotating refresh tokens, sealed at rest, revoked on disconnect,
  and never written to logs. kell's `oauth_client.py` already seals tokens and audits calls without arguments.

### What "just after kell says" means

The person should not have to repeat the request. After the callback, kell should run the pending request again
automatically, once. The chat now remembers the request that hit the sign-in wall, opens the sign-in link in a new
tab, and re-sends the request when the person returns to the chat. If the sign-in was cancelled, the request fails
once and the person can retry normally.

### When a tool is not working

A server that fails a real call (timeout, reset, broken answer, tool error) is left out of `discover_tools`
results for fifteen minutes, then tried again. This is separate from the sign-in memory: a sign-in wall is not a
failure, and a success clears the failure at once. The goal is that the agent does not keep offering the same dead
end in every turn.

## 4. Payments: mandates, tokens and step-up

### AP2 (Google, September 2025)

AP2 is an open, payment-agnostic protocol that extends A2A and MCP. It uses **mandates**: tamper-proof,
cryptographically signed digital contracts backed by verifiable credentials.

- **Intent Mandate** captures what the person asked for. For a real-time purchase it is the auditable context; for
  a delegated task it carries the rules (price limit, timing, conditions).
- **Checkout/Cart Mandate** is signed when the person approves the exact cart, so "what you see is what you pay".
- **Payment Mandate** is bound to the checkout (by a hash of the signed checkout JWT) and proves to the credential
  provider, network and processor that this agent may pay for this checkout.
- A signed **Payment Receipt** comes back after acceptance or rejection.

The useful design idea for kell is not a specific vendor: it is the **chain of evidence** (intent -> cart ->
payment -> receipt) and the rule that the agent cannot widen the mandate by itself.

### ACP and Shared Payment Tokens (OpenAI + Stripe)

The Agentic Commerce Protocol defines how an agent, a buyer and a business complete a purchase. Stripe's **Shared
Payment Token (SPT)** is a scoped grant to use a customer's payment method: it has usage and expiration limits, can
be revoked, and does not expose the underlying card credentials. Stripe has extended SPTs to network-led agentic
payments, including **Mastercard Agent Pay** and **Visa Intelligent Commerce**, plus BNPL methods. The network
issues an agentic token scoped to the customer's intent and handles credential translation and authorisation.

### PSD2 SCA and Rich Authorization Requests

In the EU/EEA, payment initiation is regulated. PSD2's SCA requires two independent factors for electronic
payments, with limited, risk-based exemptions (low value, recurring, trusted beneficiary, transaction risk
analysis). RFC 9396's `authorization_details` is the standard way to describe a payment initiation in an OAuth
request: type, actions, amount, currency, creditor, and a transaction id. The resulting token can carry
`acr: psd2_sca` and the authorised details, so the resource server can enforce exactly what was consented to.

For kell this suggests a practical pattern:

1. The agent prepares a **payment intent** (merchant, amount, currency, description, expiry).
2. The person sees it in chat with a clear **Pay** action; the amount and merchant come from the server, never from
   model text.
3. The server creates a **mandate or shared token** with a spend cap and expiry, using the payment provider's API.
4. The provider asks for **SCA/3-DS** if required; kell never sees the card number.
5. The result is stored as a **receipt** with an idempotency key, and the person can revoke the mandate.

## 5. Recommendations for kell

**Now (low risk, already close):**

- Keep OIDC for account sign-in; never use it as payment consent.
- Keep OAuth 2.1 + PKCE S256 + DCR for third-party tools; add CIMD discovery and prefer it when advertised.
- Keep the per-account sign-in-tools switch, the "just say it" phrase path, and the automatic retry after sign-in.
- Keep the failure cooldown in `discover_tools`.
- Show scopes in plain words before the person connects a tool; show a "Disconnect" that revokes the token.

**Next (medium risk):**

- Add a **consent screen for high-impact scopes** (write, send, delete) even when the server does not require one.
- Add **per-tool spend and action limits** for any tool that can move money or publish publicly.
- Add **audit export** for the person: which tool, which action, when, and the outcome (kell already logs status,
  latency and sizes; add a human-readable view).

**Payments (only with a payment partner):**

- Do not build a card form. Use a provider that issues **scoped tokens or mandates** (Stripe SPT, AP2-style
  mandates, or a network agentic token).
- Require an explicit **human confirmation** of merchant, amount and currency for every payment, and a **step-up**
  (SCA/3-DS) when the issuer asks.
- Store **no PAN**, no CVV, no raw payment credential; store the token reference, the mandate, the receipt and the
  idempotency key.
- Make the default **deny**: an agent may prepare a payment, but a person approves it unless a signed, bounded
  mandate says otherwise.

**Do not:**

- Treat a Google/OIDC login as proof of payment intent.
- Give an agent a long-lived, broad OAuth token "so it can shop".
- Let model output choose the amount, merchant or currency.
- Reuse a tool access token at a payment API, or a payment token at a tool server.

## 6. Open questions

- Which payment partner is acceptable to the owners (Anuj, Chaitanya)? Stripe SPT and AP2 both need a commercial
  relationship and a legal review; the choice is not only technical.
- Does kell need to support India-first rails (UPI mandates/e-mandates) before card networks? That changes the
  consent and recurring-payment design.
- Should the sign-in-tools switch be per tool as well as per account? The current switch is account-wide; a
  per-tool view would be more precise but more UI.
- How long should the failure cooldown be? Fifteen minutes is a guess; the right value depends on how often a
  transient outage is mistaken for a dead server.

## Sources

Standards and vendor documentation:

- Model Context Protocol, Authorization / Client Registration (2026-07-28):
  https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration
- RFC 9700, Best Current Practice for OAuth 2.0 Security (2025): https://www.rfc-editor.org/info/rfc9700/
- RFC 7636, Proof Key for Code Exchange (PKCE): https://www.rfc-editor.org/rfc/rfc7636
- RFC 8252, OAuth 2.0 for Native Apps: https://www.rfc-editor.org/rfc/rfc8252
- RFC 8707, Resource Indicators for OAuth 2.0: https://www.rfc-editor.org/rfc/rfc8707
- RFC 9396, OAuth 2.0 Rich Authorization Requests: https://www.rfc-editor.org/rfc/rfc9396.html
- OpenID Connect Core 1.0: https://openid.net/specs/openid-connect-core-1_0.html
- Google, Requesting additional permissions (incremental auth):
  https://developers.google.com/identity/sign-in/web/incremental-auth
- Google, OAuth 2.0 best practices: https://developers.google.com/identity/protocols/oauth2/resources/best-practices
- Google, Announcing Agent Payments Protocol (AP2), 16 September 2025:
  https://cloud.google.com/blog/products/ai-machine-learning/announcing-agents-to-payments-ap2-protocol
- AP2 specification: https://ap2-protocol.org/ap2/specification/
- Stripe, Agentic Commerce Protocol: https://docs.stripe.com/agentic-commerce/protocols/acp
- Stripe, Shared payment tokens: https://docs.stripe.com/agentic-commerce/concepts/shared-payment-tokens
- Stripe, additional payment methods for agentic commerce (Mastercard Agent Pay, Visa Intelligent Commerce),
  3 March 2026: https://stripe.com/blog/supporting-additional-payment-methods-for-agentic-commerce
- EBA, RTS on strong customer authentication and secure communication under PSD2:
  https://www.eba.europa.eu/legacy/regulation-and-policy/regulatory-activities/payment-services-and-electronic-money-0
- EUR-Lex, Commission Delegated Regulation (EU) 2018/389:
  https://eur-lex.europa.eu/eli/reg_del/2018/389/2023-09-12/eng

Academic and preprint literature (OpenAlex; early and not peer-reviewed unless stated):

- "Agentic AI in Payments and Financial Transactions: A Survey of Architectures, Safeguards and Open Problems"
  (2026), DOI 10.13140/rg.2.2.13674.89288.
- "Multi-Agent Orchestration for Agentic Commerce: Coordinating Shopping, Payment, Rewards, and Fraud Agents in
  Real-Time Financial Transactions" (2026), DOI 10.64820/aepjeer.32.21.26.122026.
- "Empirical Security Analysis of Production AI Agent Frameworks, Cloud Data Pipelines, and Multimodal Trajectory
  Poisoning: OAuth Token Exfiltration, SSRF, and Pre-Tool Cryptographic Sandboxing" (2026), DOI
  10.5281/zenodo.23832723.
- "Contract Preservation for Tool-Using Agents: Separating Authority from Evidence" (2026), DOI
  10.5281/zenodo.23837664.
- "Technical Report: WooCommerce Model Context Protocol Server - A Multi-Store, Previewed Safe-Write Architecture
  for Autonomous AI Agents in E-Commerce" (2026), DOI 10.5281/zenodo.23897614.
- "Dynamic Trust Drift in Agentic AI Supply Chains: A Framework for Detecting Context Poisoning,
  Post-Authorization Tool Behavior Changes, and Credential Exfiltration" (2026), DOI 10.51699/j7f24473.

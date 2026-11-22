# Key management: web assistants, coding agents, and the state of the art (research, 2026-11-02)

For the founder. Three questions: how do products like ChatGPT and Claude handle API keys; how do coding agents handle
them; and what does the strongest available encryption and control look like in 2026. Sources: primary product
documentation read directly (V), vendor docs and standards (S), the OpenAlex literature at the bottom. Not legal or
security advice.

## Section 1 - Web assistants (ChatGPT, Claude.ai, and the like)

**The assistant never holds the person's key.** The consumer chat products are built the other way round: the OPERATOR
holds provider keys; the person authenticates with an identity (OAuth sign-in), and any third-party connection is
authorized per user.

- **Claude connectors (V)**: a remote MCP server lets Claude in via (a) OAuth 2.0 with per-user sign-in - by default
  with Dynamic Client Registration (RFC 7591) or a Client ID Metadata Document; (b) a static credential (API key or
  bearer token) entered once by an organization Owner, sent as a header on every call - explicitly a beta for a
  limited set of organizations; or (c) nothing at all. Note the shape: per-user OAuth is the default; a shared static
  key is treated as the weaker, gated option.
- **ChatGPT's connected features (V)** work on OAuth grants per connector; the person's OpenAI API keys are never
  exposed to the chat, and the platform docs treat key safety as an operator concern.
- **Operator-side key governance is the real state of the art here (V)**: OpenAI's platform enforces project-scoped
  keys with expiration - administrators can set a maximum key lifetime at organization or project level, and the
  documented rotation procedure is create-new, verify, then revoke-old. Keys belong to projects, not to people, so
  blast radius is bounded by project. Anthropic separates key kinds by what they ACT AS: a personal key acts as you
  (with your roles) and dies when you lose org access; a service-account key acts as the service account; the legacy
  workspace key belongs to no one - and each is scoped to chosen workspaces.
- **Retention is not confidentiality (V)**: OpenAI's Zero Data Retention / Modified Abuse Monitoring are retention
  controls, subject to approval and extra obligations. They say nothing about who can USE your key. kel's existing
  copy rule ("ZDR is a retention promise, not a confidentiality boundary") matches what the vendors themselves say.

So the pattern for a web assistant: **keys live with the operator, scoped and expiring; people hold identities, not
keys; per-connection credentials are OAuth where possible and static headers only as a gated fallback.** (This is the
same shape as docs/hostingchats.md concluded for kel's hosted lane: one kell key per provider, the session token as
the person's virtual credential.)

## Section 2 - Coding agents (Claude Code, Cursor, Codex, MCP clients)

**Where the key lives is the whole game.** Claude Code's documented behaviour (V) is the reference:

- **OS-keychain storage with honest per-OS limits**: API keys and tokens go to the macOS Keychain when available; on
  Linux a `0600` file; on Windows a file inheriting the user-profile's ACL. Electron's safeStorage documents the
  differences underneath: macOS Keychain blocks other APPS; Windows DPAPI blocks other USERS but not other apps in
  the same session; Linux varies by keyring. No OS gives an app "other processes on this machine cannot read this"
  in every case - a client-held secret is only as safe as the machine.
- **Policy beats convenience**: an administrator can BLOCK `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and
  `apiKeyHelper` credentials at startup and force a managed-gateway sign-in, so a stale machine key cannot silently
  stand in for the org's identity. Enterprise key management is about the ability to forbid, not just to store.
- **Environment variables are the default hand-off** (OpenAI's production docs recommend env vars or a secret manager,
  never hard-coding) - but an agent READS its environment, which brings the agent-specific risk below.
- **Leak detection is layered (S)**: OWASP's DevSecOps model runs it at three layers - pre-commit (gitleaks, so
  nothing enters history), CI (TruffleHog on every PR plus periodic full-history scans, pinned to a release), and
  platform push protection (GitHub/GitLab reject recognized secrets before they land). Layering exists because each
  layer is bypassable alone (`--no-verify`).
- **The agent-specific threat is exfiltration by injection (OpenAlex, 2026)**: a tool-using agent can be steered by
  content it reads (a web page, a file, a room message) into sending its credentials somewhere. Current research:
  "The Framing Gap: Indirect Prompt-Injection Exfiltration Defeats Surface-Level Defenses in Tool-Using Agents";
  "Caught in the Act(ivation): Pre-Output and Multi-Turn Detection of Credential Exfiltration by LLM Agents"; PRISM
  (generation-time detection and mitigation in multi-agent pipelines); plus the broad prompt-injection review (2026,
  45 citations). The working conclusions: regex-style surface scrubbing is insufficient; detection has to sit at
  generation/output time; and the durable defense is architectural - agents should hold LEAST-PRIVILEGE, short-lived,
  revocable credentials, ideally audience-bound ones (Section 3), never a person's full-power key.

**kel's own agents fit here**: a room agent signs in with a CLI token (stored like Claude Code stores credentials),
and its power is bounded by the room tools and delegate_inbox as its own identity - the least-privilege shape the
research asks for. The gap the research names: those tokens should expire and be audience-bound (below), and anything
that lets an agent fetch a URL should be treated as an exfiltration channel.

## Section 3 - SOTA encryption and control (2026)

**Cloud, at rest: envelope encryption with a KMS-held root.** Data is encrypted with per-object data keys (DEKs);
DEKs are wrapped by key-encryption keys that never leave the provider's HSMs in plaintext (AWS KMS: AES-256-GCM,
non-exportable, hardware-backed). The strongest variant is **customer-managed keys**: the customer keeps the root and
can revoke it, making the data unreadable to the operator. This is "control" as an enforceable property, not a policy
promise.

**Confidential computing: attestation beats promises.** AWS Nitro Enclaves / NitroTPM, Azure Confidential Computing
and GCP's equivalents isolate a computation from even the cloud operator ("no mechanism for any AWS operator to
access..."), and cryptographic ATTESTATION proves which software is running before secrets are released. If the
question is "can the provider's staff or a compromise at the provider read my key", the 2026 answer beyond ZDR-style
policy is: run the key-using code inside an attested enclave, or accept the residual trust.

**In the browser: non-extractable keys and AEAD, now post-quantum-ready.** WebCrypto's `crypto.subtle` can generate
keys marked non-extractable, persist `CryptoKey` handles in IndexedDB, and do authenticated encryption (AES-GCM /
ChaCha20-Poly1305) entirely client-side. Chrome's 2026 guidance adds the frontier: envelope encryption client-side
with **ML-KEM768-X25519 hybrid key encapsulation** (post-quantum + classical) for establishing the shared secret.
This is exactly the shape kel's browser vault uses for BYOK keys - and its honest limit stands: a script on the same
origin can still use the vault, so keys must be spending-limited at the provider.

**Machines: zero standing credentials.** SPIFFE/SPIRE gives every workload a short-lived, automatically-rotated
identity (X.509-SVID or JWT-SVID) minted from a local Workload API - no long-lived secrets on disk, mutual TLS
between workloads, identity instead of keys. The same idea in human form is passkeys/WebAuthn (FIDO2): the
credential never leaves the device and is origin-bound; the literature consistently ranks it above SMS/TOTP and shows
good usability when deployment is done well.

**Delegation: bind tokens to what they are for.** MCP's 2025-06-18 authorization spec REQUIRES RFC 8707 resource
indicators - the access token names the exact MCP server it may be used against, in both authorization and token
requests - on top of OAuth 2.1 with PKCE. RFC 8693 token exchange defines the delegation/impersonation semantics for
"A acts for B on C" (delegation keeps A visible; impersonation makes A indistinguishable from B). Together these are
the state of the art for the agentic web: **audience-bound, least-privilege, short-lived** - the direct answer to the
exfiltration threat above.

**Group content: MLS (RFC 9420).** The Messaging Layer Security protocol provides end-to-end encryption for groups
with forward secrecy and post-compromise security, standardized and implemented (OpenMLS in Rust). For chats where
the server must NOT be able to read content, MLS is the 2026 state of the art - which is what docs/rooms_design.md
targets for sealed rooms, and why hosted connectors (which cannot hold MLS keys) are excluded from those rooms.

## What this means for kell (I)

| Lane | Today | The research-grade target |
|---|---|---|
| Hosted models (@kell, pilot) | one kell key per provider in server env; session token as the virtual key; caps and ledger | operator governance per OpenAI's model: scoped, expiring keys, rotation without redeploy, audit row naming the key generation |
| BYOK (in-tab agents) | browser vault, encrypted, non-extractable where WebCrypto allows, per account | keep; state the same-origin limit honestly (already done); consider provider-side spending limits as the real control |
| Coding agents / room agents | CLI token per machine, delegate_inbox as a named agent identity | expiry + audience-binding (RFC 8707) for the tokens; token exchange (RFC 8693) when relay agents act for a person; treat agent URL-fetch tools as exfiltration channels to design against |
| Sealed rooms (in design) | MLS via ts-mls | MLS RFC 9420 is the SOTA choice; hold connectors out of sealed rooms |
| Server at rest | Postgres + Railway | envelope-encrypt sensitive columns with a KMS root if we ever store anything but public material server-side; confidential computing only if a trust claim ever needs it |

And the copy rule the vendors themselves support: retention promises (ZDR) are about DATA, key governance is about
ACCESS; label them separately, with source and date.

## OpenAlex (S; the field is young, 2024-2026 papers dominate)

- How Bad Can It Git? Characterizing Secret Leakage in Public GitHub Repositories (2019, 115 citations) - the classic
  leakage baseline; A Comparative Study of Software Secrets Reporting by Secret Detection Tools (2023, 22) - scanners
  disagree; run two.
- What are the Practices for Secret Management in Software Artifacts? (2022, 12); Zero-Touch Secrets Management:
  Securing CI/CD Pipelines Through Ephemeral Credentials (2025); Taxonomy and Survey of Non-Human Identity and
  Secrets Management in Cloud Native and Agentic Computing Environments (2024) - the agentic identity survey.
- A Large-Scale Empirical Study of Secret Key Leakage in Hugging Face Spaces (2026) - new platforms, same failure.
- The agent-exfiltration cluster (all 2026): Caught in the Act(ivation); The Framing Gap; PRISM: Generation-Time
  Detection and Mitigation of Secret Leakage in Multi-Agent LLM Pipelines; Prompt Injection Attacks in LLMs and AI
  Agent Systems: A Comprehensive Review (45 citations).
- A survey on the (in)security of trusted execution environments (2023, 131) - enclaves are strong but not free of
  side channels; an Overview of Hardware Security and Trust (2020, 219) for the hardware grounding.
- OAuch: Exploring Security Compliance in the OAuth 2.0 Ecosystem (2022, 25) - real-world OAuth deployments get the
  details wrong; the MCP spec's MUSTs exist because of this.
- Passkeys and FIDO2: Security, Usability and Deployment of Passwordless Authentication (2026); Comparing MFA
  Methods from SMS and TOTP to FIDO2/WebAuthn and Passkeys (2026).

## Decisions this doc asks for

1. Confirm the operator pattern for the hosted lane stays as hostingchats.md decided, and add the OpenAI-style
   governance on top: key expiry dates and a documented create-verify-revoke rotation runbook.
2. Confirm agent tokens get expiry + audience-binding (RFC 8707) on the roadmap, before the relay grows.
3. Confirm the honest limits stay in the copy: client-held keys are safe from the SERVER, not from the machine;
   ZDR labels are retention, not confidentiality.

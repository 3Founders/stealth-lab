# Hosting chats that stream from providers: whose key, whose budget (research, 2026-11-02)

For the founder. It answers "is it one API key per user?" for the hosted lane (@kell, the pilot models) and for personal
agents. Sources: gateway vendors' own documentation and practitioner design guides (S), and the OpenAlex literature list at
the bottom (thin - this is mostly engineering practice, not research). V = primary text read (none: vendor docs are
secondary); S = secondary; I = inference. Not legal or security advice.

## The question, split in two

- **BYOK lane** (personal in-tab agents): the model runs on the person's own provider account.
- **Hosted lane** (@kell, and any "no setup" chat): kell pays a provider; the person must be metered, capped and billed by
  kell.

## What the pattern consensus says (S)

The gateway pattern (LiteLLM multi-tenant architecture, Envoy LLM gateway paper, PromptGuard) is consistent on five points:

1. **Provider keys live server-side, few of them, never shown to clients.** The gateway attaches the real credential when
   forwarding; apps and browsers never embed a vendor key. Rotation is a one-place operation. Storing user-owned real keys
   server-side is explicitly an anti-pattern (custody, blast radius, per-user rotation).
2. **Users get a scoped credential, not a provider key.** LiteLLM's "virtual key" ties each request to user/team/org so
   spend is attributed and budgets are enforced at every level; any level over budget blocks the request. kel's
   website-session token plays exactly this role already.
3. **Rate limits and budgets are per level** (server, key, user, team). For a start-up: per-user daily/total cap + a global
   spend ceiling; cap hits answer `403 policy_denied` with a human sentence, before or during streaming.
4. **Usage accounting must not trust the stream's final event.** A dropped or truncated stream never delivers the usage
   summary, so the gateway counts tokens as they pass and reconciles; ledger writes are asynchronous and idempotent, never
   on the hot path. Also worth metering: TTFT and tokens/second percentiles, 429s in both directions, per-model 429 rates.
5. **Managed vs self-hosted gateway** (OpenRouter vs LiteLLM): managed = no infra, ~5% platform fee; self-hosted = data and
   credentials stay on your network, you run Postgres/Redis/Docker. kel already runs Postgres, per-user in-memory rate
   windows, and thin governed proxies - the self-hosted shape, without a separate gateway product yet.

## Where real per-user keys DO belong (S, I)

Only in the BYOK lane, and only client-held: the key is created, stored (encrypted, non-extractable) and used in the
person's browser, sent straight to their chosen provider; the server never sees it and the provider bills the person. This
is the cheapest correct answer for personal agents: no custody, no central billing, and abuse is contained by the user's
own provider account. The cost is onboarding friction and per-browser storage, which the UI already says plainly.

## Recommendation for kell (I, mapped to what exists)

| Lane | Provider key | Where it lives | Person's credential | Budget / cap | Metering |
|---|---|---|---|---|---|
| BYOK (in-tab agents) | the person's | browser vault only, never sent to kell | their provider key, used directly | client-side daily cap (exists) | the provider's own dashboard |
| Hosted (@kell, pilot) | ONE key per provider, kell's | server env only, never to the browser | the website-session token (= the virtual key) | server caps + org wallet policy, `403 policy_denied` (exists) | `provider_call_ledger` (exists) |

So: **not one API key per user.** For the hosted lane it is one kell key per provider plus a per-person virtual credential
and budget; for personal agents it is the person's own key held client-side. Never store user-owned real provider keys
server-side, and never send a kell provider key to a browser.

Streaming specifics to keep (and two gaps to close):

- Keep the governed path as the only door (`/v1/chat/completions`): data-class header required, PUBLIC_SOURCE for the
  hosted lane, cap check before the call (exists).
- **Close: streaming usage accounting.** If a pilot SSE stream is cut, reconcile tokens from the gateway-side count rather
  than trusting the final chunk; write the ledger row asynchronously and idempotently.
- **Close: per-user in-flight cap** for hosted streams (one or two concurrent streams per person), so one tab cannot
  hog the provider; plus per-person rate windows (already exist for routes; apply to the pilot too).
- Rotation: the hosted provider keys should be rotate-able without a redeploy (dashboard/env), and the audit row should
  say which key generation served a call when more than one exists.

## OpenAlex (S; a thin field)

- Governing Enterprise AI Traffic at Scale: a Policy-Driven, Multi-Vendor LLM Gateway on Envoy (2026).
- The More It Says, the More You Pay: a Black-Box Audit of Provider-Side Token Inflation in LLM Services (2026) - meter
  provider-reported tokens against your own count; it also supports counting locally during streams.
- Secure Multi-Tenant API Architectures Using OAuth 2.0 and JWT for Cloud-Native Platforms (2025) - the session-token-as-
  virtual-key shape.
- Advanced API Security Techniques and Service Management (2022); Data Security in Multi-Tenant Clusters (2025).

## Decisions this doc asks for

1. Confirm the two-lane configuration above (no per-user provider keys server-side).
2. Choose the hosted per-user cap numbers (daily tokens/USD) and a global ceiling per provider key.
3. Decide later, not now: a separate gateway product (LiteLLM/managed) vs keeping the thin governed proxies. The switch
   becomes interesting only at scale or when many providers per model are needed.
4. ZDR/provider-policy labelling for the hosted lane stays open (separate note pending in rooms_design.md).

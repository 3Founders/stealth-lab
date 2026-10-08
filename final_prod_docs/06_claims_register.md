# Claims register: what we may and may not say

**DRAFT.** One row per claim a customer or a checklist asks for. "Say today" is the strongest sentence we can support now. Update a row only when its unlock item is done and recorded.

| Claim asked for | Say today | Do not say | Unlock |
|---|---|---|---|
| Zero data retention | "`call_model` prompts and outputs are not stored in our database. `find_ways` stores a query hash." | "Zero data retention", "we never store your code" | Written provider terms confirming zero-retention for each model host used, plus a log audit showing no content in application logs or Sentry |
| No training on your data | "We do not train models on customer content. We ask providers to use no-training settings where offered." | A flat guarantee that covers providers | Signed terms with each provider that state no training |
| TLS 1.3 in transit | Nothing yet | "TLS 1.3" | Run an SSL scan on the live endpoint; record the result and date |
| Encryption at rest | "Our database and object-storage providers encrypt at rest" only after confirming their documentation | A key-management claim | Link each provider's statement here |
| Hosted in [region] | The real regions, by component, from document 01 | "India only", "US only" | Move components, or describe the true split in the DPA |
| 99.9% uptime | A target, with measurement rules. As of 2026-10-07 **no availability has been measured** (no external probe yet), and the service runs as one instance. | A guarantee; any uptime percentage | Items in document 04 and the "what blocks 99.9%" list in `p1_results.md`; 30 days of probe data |
| Health checks and graceful shutdown | "Liveness and readiness endpoints; on shutdown, in-flight requests finish before the process stops" (live-tested 2026-10-07 on Windows; Linux SIGTERM run still to do) | "Zero-downtime deploys" (one instance restarts on every deploy) | Two instances and a rolling deploy |
| Backups and restore | "Point-in-time restore from our database provider" (Neon: restore in seconds; the history window depends on the plan) | An RPO or RTO figure | The Neon plan of every project confirmed; one production-sized branch restore timed (`p1_results.md`) |
| Under 30 ms added latency | "We are not in the model-call path" | Any millisecond figure | Not applicable unless we build a proxy |
| Automatic failover between Anthropic and OpenAI | "With several configured connections, a rule can fall back" once built | Automatic failover today | Build and test it |
| Independent penetration test, no high or critical findings | "Planned after deployment" | Any statement of results | A completed test and retest |
| SOC 2 or ISO 27001 | "In progress" only if we have started | "Compliant", "certified" | A report |
| Tenant isolation | "Enforced in the application, tested end to end"; add "and in row-level security" only after the services connect as `stealth_app` (3-hard.md Decision 1) | "Guaranteed", "cannot leak" | Switch the services to the `stealth_app` role (already created on every database), then run the live isolation suites against production |
| Savings (for example 50%) | The measured figure, with its method, baseline and sample, stated as a measurement | A promise of future savings | A measurement on a customer's real sessions in cache-aware dollars with a control |
| Right to delete / export | "Withdraw and export on request" only after it is built | "Fully deletable" | The hard-delete and export path (document 02) |
| Single sign-on / SCIM | "Not yet" | Anything else | Build, or use an identity provider that supplies it |
| Audit log access for customers | "Provider-call ledger per organisation" | A general audit-trail claim | A customer-facing view with export |

## Rule

Every number or guarantee in a customer document comes from this table or from a measurement with a date and method. If a row is not green, the sentence in "Say today" is the ceiling.

# Service Level Policy

**[COMPANY LEGAL NAME]** · Version **[1.0]** · Applies from the **Go-Live Date** in the Order Form. It does not apply during a Pilot Phase or shadow mode. This policy is Exhibit A of the Master Services Agreement; if they differ, the MSA controls.

> **Do not publish or sign until the conditions in the last section are met.** A single running instance cannot meet 99.9%.

## 1. Availability

- **Commitment:** the MCP endpoint is available **99.9%** of each calendar month.
- **Measurement:** external probes every **[60]** seconds from at least **[two]** locations: a request to the health endpoint and one authenticated tool call. A minute counts as down if both probes fail for that minute.
- **Exclusions:** scheduled maintenance announced **[48]** hours ahead and capped at **[4]** hours a month; the customer's network, client software or credentials; failure of a model provider the customer chose (see section 3); force majeure; suspension for misuse.
- **Credits:** below 99.9%: **5%** of that month's fees; below 99.0%: **15%**; below 95.0%: **30%**. Credits are the sole remedy for unavailability. Claim within **[30]** days.

## 2. Latency

- **Added latency on your agent's model calls: none.** The service is not in the path of those calls.
- **`find_ways`:** target p95 under **[●]** seconds. This tool makes embedding and model-judgment calls, so it takes seconds. It is a target we report monthly, not a credit-bearing commitment.
- **`call_model`:** adds one hop. Target added time over the provider's own response: under **[●]** ms at p95, measured by our logs. Target only.

## 3. Failure behaviour

| Event | What happens |
|---|---|
| Embedding or judge provider unavailable | `find_ways` still answers using lexical search and says so in its result |
| Your chosen model provider is down (`call_model`) | The call returns an error naming the provider. **[If you configured more than one connection and a fallback order, the next connection is tried, in that order only.]** We never switch to a provider you did not configure |
| Budget used up or kill switch on | The call is refused before any provider is contacted |
| A call's cost cannot be recorded | The result is withheld and the call fails, so no spend goes unrecorded |
| Our service is down | Your agent continues without our tools. Nothing in your repository depends on us being up |

We do not offer automatic failover between Anthropic, OpenAI or any other provider on your behalf.

## 4. Support and incidents

| Severity | Example | First response | Updates |
|---|---|---|---|
| 1 | Service down; suspected data exposure | **[1 hour]**, **[24x7]** | Every **[1]** hour |
| 2 | One tool failing; wrong results at scale | **[4 hours]**, business hours | Daily |
| 3 | Question; minor defect | **[1 business day]** | As needed |

Security incidents: notice within **48 hours** of confirmation, plus any report required by law (for example CERT-In within 6 hours of noticing a reportable incident). Contact: **[support@domain]**.

## 5. Recovery

Recovery point objective **[●]**; recovery time objective **[●]**. Backups are the database provider's point-in-time restore; a restore test was last run on **[date]**.

## Conditions before this policy is offered

1. At least two running instances, with in-process state moved out of the process.
2. External probes, a status page and alerting live for 30 days with the data to show.
3. A measured `find_ways` p95 and a load test at the contracted user count.
4. A tested database restore, and RPO and RTO filled in.
5. A named on-call and a support mailbox.

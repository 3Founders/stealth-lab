# Service levels and failure behaviour

**DRAFT. These are targets we will measure, not guarantees. Do not put a number in a signed contract until the "Evidence needed" column is filled.** The MSA's Exhibit A defines 99.9% availability from the Go-Live Date only.

## Targets

| Item | Target | Evidence needed before we commit |
|---|---|---|
| Availability | 99.9% monthly, measured by an external probe on `/` and an authenticated tool call | At least 30 days of probe data; a second instance; a status page. One process cannot meet 99.9%, since a restart is downtime |
| `find_ways` latency | p95 under [●] seconds (to be measured) | A stage-timing profile; the tool makes an embedding call and several judge calls, so it takes seconds, not milliseconds |
| Added latency on an agent's own model calls | **Zero: we are not in that path** | Not applicable. `find_ways` runs before the agent works. `call_model` is the only tool that adds a hop, and a customer can choose not to use it |
| Support response | [●] business hours, severity based | A named on-call and an email address |
| Incident notice | 48 hours after confirmation, plus statutory reports | `security_runbook.md` incident section, extended with a contact tree |
| Recovery | RPO [●], RTO [●] | A tested restore from Neon point-in-time recovery |

## What happens when something fails

| Failure | Behaviour today | Source |
|---|---|---|
| Embedding or judge provider down or refusing credentials | `find_ways` still answers using lexical search and marks `goal_judgment.mode = "lexical_fallback"` | `docs/deploy/hosted-mcp.md` **[code]** |
| `call_model`: a customer's chosen provider is down | The call returns an error with the status. **No automatic failover to another provider unless the customer configured several connections** and a rule for them | `backend/app/providers/` **[code]** |
| `call_model`: a cost cannot be recorded after the call | The result is withheld and the call is marked failed | `backend/app/providers/service.py` **[code]** |
| Budget exhausted or kill switch on | The call is refused before any provider is contacted | migration 136 **[code]** |
| Our server restarts | In-flight requests fail; stateless HTTP lets a client retry. Per-process state (rate governor, task store) resets | `--workers 1` note in `CLAUDE.md` |
| Database unavailable | Tool calls fail; no cached fallback | **[gap]** |
| Server down entirely | The agent works without the tools. Tool descriptions tell the agent to continue the task if a tool is unavailable | `server.py` instructions **[code, confirm wording]** |

## To close before we offer any SLA

1. Run two or more instances. This needs the in-memory task store and the governor state moved out of the process.
2. Add health probes, a status page and alerting.
3. Put the database on its pooled connection host.
4. Load test at the customer count we are selling (1,000 users) and publish the result.
5. Offer automatic fallback for `call_model` across a customer's connections, and document its order.

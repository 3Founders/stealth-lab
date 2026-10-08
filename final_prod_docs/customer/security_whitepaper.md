# Security and Data Flow: one page

**[COMPANY LEGAL NAME]** · Version **[1.0]** · **[DATE]** · Send under NDA. Items in [brackets] are completed from the live deployment before sending.

**What the service is.** A hosted MCP server. Your coding agent calls it to find proven procedures for a task and to choose a cost-effective model. It is not a proxy: your agent's own model traffic does not pass through us.

## 1. Are prompts or code stored on your servers?

| What the agent sends | What we keep |
|---|---|
| A task description to `find_ways` | A SHA-256 hash of the query, the caller id, and the ids of the procedures returned. The query text is not kept |
| Outcomes to `report_result` | Whether it worked, token counts, cost and latency. No prompt or output text |
| A prompt to `call_model` (optional, only if you configure a connection) | Token counts, cost, provider, model and status. The prompt and the output are not written to our database |
| Content you choose to share with `submit_way` | That content, with the visibility you chose (private, organisation or public) |

**Zero data retention.** We do not retain your prompts, outputs or source code. Your model provider may. Under **[Enterprise ZDR option]**, we send `call_model` traffic only to providers whose written terms with us provide zero retention and no training, listed in the Subprocessor Schedule. Without that option, each provider's standard terms apply. **[Remove this paragraph's ZDR option until the provider terms are signed.]**

We do not train models on your content.

## 2. How is data encrypted?

- **In transit:** HTTPS to our endpoint, **[TLS 1.2 or higher; TLS 1.3 supported. Insert the date and result of the latest scan]**. Database connections require TLS.
- **At rest:** the database and object storage encrypt data at rest using their providers' default encryption **[link to provider statement]**.
- **Your keys:** your model-provider keys stay in your own environment. We store a reference to them, never the key.

## 3. Where does it run?

| Component | Provider | Region |
|---|---|---|
| MCP server | **[host]** | **[region]** |
| Database | Neon (Postgres) | **[region]** |
| Embeddings and judge models | Google Vertex AI | **[region]** |
| Object storage | Cloudflare R2 | **[region]** |
| Sign-in | Supabase Auth | **[region]** |

The full list is the Subprocessor Schedule. Processing locations are set out in the Data Processing Agreement.

## How we protect it

Every request carries an OAuth token. Private and organisation content is filtered by one shared access function in the application (database row-level security policies are written beneath it but are not yet in force, because the services do not yet connect with the restricted role they apply to); an end-to-end test checks that one user cannot act on another's data. Retrieved text is screened, escaped and marked as untrusted. For `call_model`, an organisation admin sets allowed data classes, budgets and a kill switch, and every call is entered in an audit ledger.

## What we do not yet have

An independent penetration test **[status and date]**, SOC 2 or ISO 27001 certification, customer single sign-on and SCIM, and multi-instance failover. We describe these in our assessment summary rather than imply them.

**Security contact:** **[security@domain]** · Vulnerability policy: **[URL]**

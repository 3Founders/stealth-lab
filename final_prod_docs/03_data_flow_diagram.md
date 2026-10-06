# Data-flow diagram

**DRAFT.** Mermaid renders on GitHub and in most Markdown viewers. Dashed lines cross a trust boundary.

```mermaid
flowchart LR
  subgraph Customer["Customer environment (trust boundary 1)"]
    A["Coding agent<br/>Claude Code, Cursor, Codex, opencode"]
    K["Customer model keys<br/>env: references"]
    M["Customer-chosen model providers<br/>BYOK"]
  end

  subgraph Stealth["Our service (trust boundary 2)"]
    S["MCP server<br/>one container"]
    G["Policy, budget and kill switch<br/>call_model only"]
    DB[("Postgres, pgvector<br/>procedures, outcomes, ledger")]
    J["Embedding and judge calls"]
  end

  AUTH["Supabase Auth<br/>OAuth 2.1 and consent"]
  VX["Google Vertex AI<br/>embeddings and judges"]
  R2[("Cloudflare R2<br/>object storage")]

  A -. "1 sign-in, bearer token" .-> AUTH
  A -. "2 find_ways: task text" .-> S
  S -->|"3 query hash, ids"| DB
  S --> J
  J -. "4 task text, procedure text" .-> VX
  S -->|"5 procedures, untrusted-data notice"| A
  A -. "6 report_result: outcome, tokens, cost" .-> S
  S --> DB
  A -. "7 call_model: prompt" .-> S
  S --> G
  G --> K
  G -. "8 prompt, customer key" .-> M
  M -. "9 output" .-> S
  S -. "10 output; ledger row without content" .-> A
  S --> R2
```

## Reading it

| Step | Data crossing | Stored by us | Note |
|---|---|---|---|
| 1 | Credentials, consent | Supabase | Consent page: no framing, localhost and unnamed-app warnings |
| 2–5 | Task description out, procedures back | Hash only | The Vertex hop (4) sends task text and procedure text to a model provider; this is the flow a customer must approve in the subprocessor list |
| 6 | Outcome and counts | Yes, no content | Bound to the caller who got the plan |
| 7–10 | Prompt and output | Ledger row only | Customer key stays in their secret store. A platform-paid call (no customer key) uses our egress policy and is not the default |
| R2 | Object storage | Yes | Contents to confirm **[unverified]** |

## Not in this diagram, but customers will ask

- The hop from the container host to the database is across the public internet with TLS **[config]**, not a private link.
- Ingestion workers (bulk loading of public procedures) share the database and are a separate process with their own service credential (`docs/service_identity.md`).
- The Cursor and ChatGPT desktop clients keep their own logs of tool results on the user's machine. We cannot control that.

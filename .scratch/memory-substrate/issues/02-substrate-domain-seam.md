# Substrate / domain seam

Type: grilling
Status: claimed
Blocked by: 01

## Question

Where exactly is the line between the **general memory substrate** and the **IDE/coding domain** that sits on top of it?

spec.md wants a general-purpose experiential and procedural memory system with an agentic-coding environment as merely the first concrete domain. It also warns against hardcoding state to code repositories, because personal/mobile state must eventually fit. But it equally warns against premature abstraction. The seam has to be drawn somewhere specific.

Decide, for each of the core concepts — episode, event, observation, claim, state, procedure, procedure execution, evidence — which parts are substrate and which are domain:

- Does the concept have domain-specific **columns** (e.g. `repository`, `branch`, `commit_sha` on an episode), or does the domain ride entirely in a typed JSONB payload with a `domain` discriminator?
- If JSONB: what is the registration mechanism for a domain's payload schema, and how does anything validate it? The repo already has a cautionary tale here — `knowledge_nodes.node_type` is a bare TEXT column with no enum and no registry, and it has quietly accumulated `claim`, `failure_mode` and `code_location` as virtual types.
- If columns: what stops the coding domain from permanently shaping the substrate?

Grill these specifically:

- Is a second domain actually coming, or is "general-purpose" aspiration that will cost complexity now and never be exercised? The `tenant_id` precedent in this repo is exactly the failure mode: a column on every table, filtered by zero queries, and multi-tenancy that was decorative. What would make the general/domain split *not* be the next `tenant_id`?
- Which is cheaper to fix later — a substrate that turned out to be too coding-specific, or one that turned out to be needlessly abstract?
- Where do the existing domain-shaped services live relative to this seam: `call_graph.py`, `code_index.py`, `patch_format.py`, `graph_ingest.py`? These are unambiguously coding-specific and already sit inside `backend/app/services/`.

The answer must name the package/module boundary concretely, not just the conceptual one.

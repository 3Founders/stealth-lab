# Global Procedural Library: Parallel Worker Plan

## Decision

The global library does not wait for manual canary approval.

Every successfully parsed external package is automatically published to the
global Procedure library with:

- `verification_state = candidate` (or the repository equivalent)
- immutable source SHA and bundle hash
- source-reported material separated from Stealth execution evidence
- full resource, dependency, tool, and implementation metadata

Deduplication runs continuously after publication. High-confidence duplicates
are reconciled automatically; ambiguous Procedures remain independently
addressable and retain all provenance. Manual review is not a prerequisite for
the library or its retrieval index.

Malformed, unsafe, over-limit, or transport-failed packages are the only items
that remain blocked. They are retried or reported; they are never silently
dropped.

## Worker topology

Source snapshot worker
        |
        v
Package discovery workers
        |
        v
Parse / normalize workers
        |
        +--> implementation + dependency extraction
        +--> embedding workers
        |
        v
Idempotent persistence coordinator
        |
        +--> global candidate retrieval index
        +--> continuous dedup/reconciliation workers
        +--> runtime Procedure -> TaskGraph instantiation

One job represents one immutable package:

```json
{
  "job_type": "structured_skill_package",
  "source_id": "addy-agent-skills",
  "repo_url": "https://github.com/addyosmani/agent-skills",
  "resolved_commit": "<40-char SHA>",
  "skill_path": "skills/debugging-and-error-recovery/SKILL.md",
  "bundle_hash": "<sha256>",
  "extractor_version": "skill_md_v4"
}
```

The payload is the idempotency key. Credentials, API keys, and raw repository
archives never belong in a job payload.

## Agents to run in parallel


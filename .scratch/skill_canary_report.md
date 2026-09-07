# Structured skill ingestion canary

Run source: `addy-agent-skills` (`https://github.com/addyosmani/agent-skills`)

Resolved commit: `48cb1168aeaaa70dfc2bbf709eddfa2a8ed8129a`

The five manually inspected packages were:

| Skill path | Procedure | Abstract steps | State |
|---|---|---:|---|
| `skills/test-driven-development/SKILL.md` | test-driven-development | 3 | candidate |
| `skills/debugging-and-error-recovery/SKILL.md` | debugging-and-error-recovery | 6 | candidate |
| `skills/planning-and-task-breakdown/SKILL.md` | planning-and-task-breakdown | 5 | candidate |
| `skills/code-review-and-quality/SKILL.md` | code-review-and-quality | 5 | candidate |
| `skills/code-simplification/SKILL.md` | code-simplification | 4 | candidate |

All five retain the source URI, repository, commit, content hash, bundle hash,
source ID, and license metadata in Procedure provenance/domain payload and the
`ingested_artifacts` row. Generic source steps were not materialized as
TaskNodes. Bundled files remain resource manifests; scripts become separate
unverified Implementation candidates.

The first parser version exposed a defect: checklist/example bullets became
giant concrete step lists and four benign documents tripped the trust screen.
Parser v2 introduced workflow-heading precedence; parser v5 retained explicit
sub-skill references. The five were recompiled as immutable Procedure versions.

Retrieval QA used 15 natural-language queries (three per family) through normal
Stealth embeddings and retrieval. Result: 15/15 expected families appeared in
the top five; 14 ranked first. The one rank-two result was the ambiguous query
“how should I debug a failing test without randomly editing files?”, where TDD
ranked first and systematic debugging ranked second.

The canary remains candidate/unverified. No execution evidence was fabricated.

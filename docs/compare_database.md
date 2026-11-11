# Tool vs procedure: database

Generated 2026-11-11 13:44 UTC by `python -m scripts.compare.report --goal database` from task_runs. 10 corpus tasks (2 held out), 10 with runs, 90 attempts.
**Answer:** no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.


## Main tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 24 | 0% | 0.00 | 0.0000 | 112 | no verified success | 24 |
| tool | 24 | 0% | 0.00 | 0.0000 | 37 | no verified success | 24 |
| baseline | 24 | 100% | 1.00 | 0.0000 | 9765 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=24) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=24) |
| latency delta ms | -74.917 [-255.667, +16.375] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -9727.333 [-12394.542, -7607.875] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 24, exact p=1.19e-07 |

**baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -9652.417 [-12335.000, -7511.250] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 24, exact p=1.19e-07 |

**baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| db-001 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| db-002 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| db-003 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| db-004 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| db-005 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| db-006 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| db-007 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| db-008 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |

## Held-out tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 6 | 0% | 0.00 | 0.0000 | 16 | no verified success | 6 |
| tool | 6 | 0% | 0.00 | 0.0000 | 36 | no verified success | 6 |
| baseline | 6 | 100% | 1.00 | 0.0000 | 6860 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=6) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=6) |
| latency delta ms | +19.500 [+14.500, +25.000] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 6 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -6824.333 [-8946.333, -5220.333] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than tool (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -6843.833 [-8965.333, -5241.333] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than procedure (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| db-009 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| db-010 | 0/3 | 0/3 | 3/3 | registry:io.github.phatngoit/mcp-db-connect | - |

## Every failure

- **procedure**: 30 of 30
  - error: no_procedure (30): db-001#1, db-001#2, db-001#3, db-002#1, db-002#2, db-002#3, db-003#1, db-003#2, db-003#3, db-004#1, db-004#2, db-004#3, db-005#1, db-005#2, db-005#3, db-006#1, db-006#2, db-006#3, db-007#1, db-007#2 ...
- **tool**: 30 of 30
  - error: gate:denied (30): db-001#1, db-001#2, db-001#3, db-002#1, db-002#2, db-002#3, db-003#1, db-003#2, db-003#3, db-004#1, db-004#2, db-004#3, db-005#1, db-005#2, db-005#3, db-006#1, db-006#2, db-006#3, db-007#1, db-007#2 ...
- **baseline**: none

## Caveats

- Only 24 tool/procedure pairs (the plan's bar is 60 tasks x 3 repeats); any direction is preliminary.
- The procedure arm did not run on some attempts: no_procedure 30. These count as errors, so the comparison is partly 'tool vs no procedure', not 'tool vs a procedure'.
- The gate refused 30 tool attempts (no policy row, or a certificate/risk refusal): the tool arm measures what kell is allowed to run, not every card's capability.
- Scores come from each task's own verifier (regex, terms, or SQL rows on a fixture); no judge was used unless a task says rubric. Tool answers are raw tool output (search results), so a pass means the tool surfaced the answer, not that an agent used it correctly.


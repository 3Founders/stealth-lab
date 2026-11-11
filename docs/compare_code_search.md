# Tool vs procedure: code_search

Generated 2026-11-11 13:43 UTC by `python -m scripts.compare.report --goal code_search` from task_runs. 10 corpus tasks (2 held out), 10 with runs, 90 attempts.
**Answer:** no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.


## Main tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 24 | 0% | 0.00 | 0.0000 | 137 | no verified success | 24 |
| tool | 24 | 0% | 0.00 | 0.0000 | 40 | no verified success | 24 |
| baseline | 24 | 75% | 0.75 | 0.0000 | 12439 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=24) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=24) |
| latency delta ms | -97.667 [-330.500, +21.333] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.750 [-0.917, -0.583] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -12399.583 [-21211.500, -7119.625] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 18, exact p=7.63e-06 |

**baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.750 [-0.917, -0.583] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -12301.917 [-21141.375, -7005.208] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 18, exact p=7.63e-06 |

**baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| code-001 | 0/3 | 0/3 | 0/3 | registry:eu.pauhu/data | - |
| code-002 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |
| code-003 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |
| code-004 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |
| code-005 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |
| code-006 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |
| code-007 | 0/3 | 0/3 | 0/3 | registry:eu.pauhu/data | - |
| code-008 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |

## Held-out tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 6 | 0% | 0.00 | 0.0000 | 14 | no verified success | 6 |
| tool | 6 | 0% | 0.00 | 0.0000 | 33 | no verified success | 6 |
| baseline | 6 | 100% | 1.00 | 0.0000 | 8568 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=6) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=6) |
| latency delta ms | +19.000 [+18.000, +20.000] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 6 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -8534.667 [-15960.167, -4548.833] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than tool (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -8553.667 [-15979.167, -4567.667] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than procedure (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| code-009 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |
| code-010 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |

## Every failure

- **procedure**: 30 of 30
  - error: no_procedure (30): code-001#1, code-001#2, code-001#3, code-002#1, code-002#2, code-002#3, code-003#1, code-003#2, code-003#3, code-004#1, code-004#2, code-004#3, code-005#1, code-005#2, code-005#3, code-006#1, code-006#2, code-006#3, code-007#1, code-007#2 ...
- **tool**: 30 of 30
  - error: gate:denied (30): code-001#1, code-001#2, code-001#3, code-002#1, code-002#2, code-002#3, code-003#1, code-003#2, code-003#3, code-004#1, code-004#2, code-004#3, code-005#1, code-005#2, code-005#3, code-006#1, code-006#2, code-006#3, code-007#1, code-007#2 ...
- **baseline**: 6 of 30
  - fail: wrong answer (6): code-001#1, code-001#2, code-001#3, code-007#1, code-007#2, code-007#3

## Caveats

- Only 24 tool/procedure pairs (the plan's bar is 60 tasks x 3 repeats); any direction is preliminary.
- The procedure arm did not run on some attempts: no_procedure 30. These count as errors, so the comparison is partly 'tool vs no procedure', not 'tool vs a procedure'.
- The gate refused 30 tool attempts (no policy row, or a certificate/risk refusal): the tool arm measures what kell is allowed to run, not every card's capability.
- Scores come from each task's own verifier (regex, terms, or SQL rows on a fixture); no judge was used unless a task says rubric. Tool answers are raw tool output (search results), so a pass means the tool surfaced the answer, not that an agent used it correctly.


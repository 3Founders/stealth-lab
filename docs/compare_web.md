# Tool vs procedure: web

Generated 2026-11-11 13:43 UTC by `python -m scripts.compare.report --goal web` from task_runs. 10 corpus tasks (2 held out), 10 with runs, 90 attempts.
**Answer:** tool scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.


## Main tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 24 | 0% | 0.00 | 0.0000 | 99 | no verified success | 24 |
| tool | 24 | 50% | 0.50 | 0.0035 | 259 | $0.0070 | 12 |
| baseline | 24 | 96% | 0.96 | 0.0000 | 2185 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.500 [+0.292, +0.708] (n=24) |
| cost delta $ | +0.004 [+0.002, +0.005] (n=24) |
| latency delta ms | +160.208 [-43.042, +341.417] (n=24) |
| pass/fail (McNemar) | tool-only passes 12, procedure-only passes 0, exact p=0.000488 |

**tool scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.458 [-0.667, -0.250] (n=24) |
| cost delta $ | +0.003 [+0.002, +0.005] (n=24) |
| latency delta ms | -1925.917 [-2836.292, -1254.625] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 11, exact p=0.000977 |

**baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.958 [-1.000, -0.875] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -2086.125 [-3016.333, -1403.042] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 23, exact p=2.38e-07 |

**baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| web-001 | 0/3 | 3/3 | 3/3 | registry:ai.exa/exa | - |
| web-002 | 0/3 | 0/3 | 2/3 | registry:io.github.Developyn/laver-mcp | - |
| web-003 | 0/3 | 3/3 | 3/3 | registry:ai.exa/exa | - |
| web-004 | 0/3 | 3/3 | 3/3 | registry:ai.exa/exa | - |
| web-005 | 0/3 | 0/3 | 3/3 | registry:ai.gosummer/summer | - |
| web-006 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |
| web-007 | 0/3 | 0/3 | 3/3 | registry:ai.easy-peasy/mcp | - |
| web-008 | 0/3 | 3/3 | 3/3 | registry:ai.exa/exa | - |

## Held-out tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 6 | 0% | 0.00 | 0.0000 | 9 | no verified success | 6 |
| tool | 6 | 100% | 1.00 | 0.0070 | 813 | $0.0070 | 0 |
| baseline | 6 | 100% | 1.00 | 0.0000 | 1763 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +1.000 [+1.000, +1.000] (n=6) |
| cost delta $ | +0.007 [+0.007, +0.007] (n=6) |
| latency delta ms | +804.167 [+272.500, +1381.000] (n=6) |
| pass/fail (McNemar) | tool-only passes 6, procedure-only passes 0, exact p=0.0312 |

**tool scores higher than procedure (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=6) |
| cost delta $ | +0.007 [+0.007, +0.007] (n=6) |
| latency delta ms | -950.333 [-1653.333, -142.667] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and baseline (95% CI contains 0); only 6 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -1754.500 [-2147.500, -1353.333] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than procedure (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| web-009 | 0/3 | 3/3 | 3/3 | registry:ai.exa/exa | - |
| web-010 | 0/3 | 3/3 | 3/3 | registry:ai.exa/exa | - |

## Every failure

- **procedure**: 30 of 30
  - error: no_procedure (30): web-001#1, web-001#2, web-001#3, web-002#1, web-002#2, web-002#3, web-003#1, web-003#2, web-003#3, web-004#1, web-004#2, web-004#3, web-005#1, web-005#2, web-005#3, web-006#1, web-006#2, web-006#3, web-007#1, web-007#2 ...
- **tool**: 12 of 30
  - error: gate:denied (12): web-002#1, web-002#2, web-002#3, web-005#1, web-005#2, web-005#3, web-006#1, web-006#2, web-006#3, web-007#1, web-007#2, web-007#3
- **baseline**: 1 of 30
  - fail: wrong answer (1): web-002#3

## Caveats

- Only 24 tool/procedure pairs (the plan's bar is 60 tasks x 3 repeats); any direction is preliminary.
- The procedure arm did not run on some attempts: no_procedure 30. These count as errors, so the comparison is partly 'tool vs no procedure', not 'tool vs a procedure'.
- The gate refused 12 tool attempts (no policy row, or a certificate/risk refusal): the tool arm measures what kell is allowed to run, not every card's capability.
- Scores come from each task's own verifier (regex, terms, or SQL rows on a fixture); no judge was used unless a task says rubric. Tool answers are raw tool output (search results), so a pass means the tool surfaced the answer, not that an agent used it correctly.


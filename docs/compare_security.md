# Tool vs procedure: security

Generated 2026-11-11 13:44 UTC by `python -m scripts.compare.report --goal security` from task_runs. 10 corpus tasks (2 held out), 10 with runs, 90 attempts.
**Answer:** no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary; no difference shown between tool and baseline (95% CI contains 0); only 24 pairs (< 30), treat as preliminary; no difference shown between procedure and baseline (95% CI contains 0); only 24 pairs (< 30), treat as preliminary.


## Main tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 24 | 0% | 0.00 | 0.0000 | 92 | no verified success | 24 |
| tool | 24 | 0% | 0.00 | 0.0000 | 37 | no verified success | 24 |
| baseline | 24 | 12% | 0.12 | 0.0000 | 5826 | $0.0002 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=24) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=24) |
| latency delta ms | -55.667 [-196.792, +22.292] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.125 [-0.250, +0.000] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -5789.667 [-7485.625, -4281.792] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 3, exact p=0.25 |

**no difference shown between tool and baseline (95% CI contains 0); only 24 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.125 [-0.250, +0.000] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -5734.000 [-7384.583, -4193.750] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 3, exact p=0.25 |

**no difference shown between procedure and baseline (95% CI contains 0); only 24 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| sec-001 | 0/3 | 0/3 | 0/3 | registry:io.github.presendapp/presend-mcp | - |
| sec-002 | 0/3 | 0/3 | 0/3 | registry:io.github.presendapp/presend-mcp | - |
| sec-003 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-mcp | - |
| sec-004 | 0/3 | 0/3 | 0/3 | registry:io.github.presendapp/presend-mcp | - |
| sec-005 | 0/3 | 0/3 | 0/3 | registry:io.github.Velox-XR/velox-trifit-developer | - |
| sec-006 | 0/3 | 0/3 | 0/3 | registry:io.github.phatngoit/mcp-db-connect | - |
| sec-007 | 0/3 | 0/3 | 0/3 | registry:io.github.fredxhansson-cmyk/fuze | - |
| sec-008 | 0/3 | 0/3 | 0/3 | registry:io.github.presendapp/presend-mcp | - |

## Held-out tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 6 | 0% | 0.00 | 0.0000 | 9 | no verified success | 6 |
| tool | 6 | 0% | 0.00 | 0.0000 | 29 | no verified success | 6 |
| baseline | 6 | 100% | 1.00 | 0.0000 | 4327 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=6) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=6) |
| latency delta ms | +19.667 [+14.833, +23.000] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 6 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -4297.833 [-4983.000, -3619.500] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than tool (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -4317.500 [-5004.500, -3639.500] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than procedure (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| sec-009 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-mcp | - |
| sec-010 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |

## Every failure

- **procedure**: 30 of 30
  - error: no_procedure (30): sec-001#1, sec-001#2, sec-001#3, sec-002#1, sec-002#2, sec-002#3, sec-003#1, sec-003#2, sec-003#3, sec-004#1, sec-004#2, sec-004#3, sec-005#1, sec-005#2, sec-005#3, sec-006#1, sec-006#2, sec-006#3, sec-007#1, sec-007#2 ...
- **tool**: 30 of 30
  - error: gate:denied (30): sec-001#1, sec-001#2, sec-001#3, sec-002#1, sec-002#2, sec-002#3, sec-003#1, sec-003#2, sec-003#3, sec-004#1, sec-004#2, sec-004#3, sec-005#1, sec-005#2, sec-005#3, sec-006#1, sec-006#2, sec-006#3, sec-007#1, sec-007#2 ...
- **baseline**: 21 of 30
  - fail: wrong answer (21): sec-001#1, sec-001#2, sec-001#3, sec-002#1, sec-002#2, sec-002#3, sec-004#1, sec-004#2, sec-004#3, sec-005#1, sec-005#2, sec-005#3, sec-006#1, sec-006#2, sec-006#3, sec-007#1, sec-007#2, sec-007#3, sec-008#1, sec-008#2 ...

## Caveats

- Only 24 tool/procedure pairs (the plan's bar is 60 tasks x 3 repeats); any direction is preliminary.
- The procedure arm did not run on some attempts: no_procedure 30. These count as errors, so the comparison is partly 'tool vs no procedure', not 'tool vs a procedure'.
- The gate refused 30 tool attempts (no policy row, or a certificate/risk refusal): the tool arm measures what kell is allowed to run, not every card's capability.
- Scores come from each task's own verifier (regex, terms, or SQL rows on a fixture); no judge was used unless a task says rubric. Tool answers are raw tool output (search results), so a pass means the tool surfaced the answer, not that an agent used it correctly.


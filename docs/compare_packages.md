# Tool vs procedure: packages

Generated 2026-11-11 13:43 UTC by `python -m scripts.compare.report --goal packages` from task_runs. 10 corpus tasks (2 held out), 10 with runs, 90 attempts.
**Answer:** no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.


## Main tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 24 | 0% | 0.00 | 0.0000 | 146 | no verified success | 24 |
| tool | 24 | 0% | 0.00 | 0.0000 | 38 | no verified success | 24 |
| baseline | 24 | 88% | 0.88 | 0.0000 | 4941 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=24) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=24) |
| latency delta ms | -107.792 [-364.958, +21.750] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.875 [-1.000, -0.750] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -4902.958 [-5474.833, -4377.500] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 21, exact p=9.54e-07 |

**baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.875 [-1.000, -0.750] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -4795.167 [-5399.042, -4220.875] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 21, exact p=9.54e-07 |

**baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| pkg-001 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |
| pkg-002 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |
| pkg-003 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |
| pkg-004 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |
| pkg-005 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |
| pkg-006 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |
| pkg-007 | 0/3 | 0/3 | 0/3 | registry:io.github.presendapp/presend-deps | - |
| pkg-008 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |

## Held-out tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 6 | 0% | 0.00 | 0.0000 | 11 | no verified success | 6 |
| tool | 6 | 0% | 0.00 | 0.0000 | 30 | no verified success | 6 |
| baseline | 6 | 100% | 1.00 | 0.0000 | 4558 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=6) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=6) |
| latency delta ms | +18.833 [+17.167, +20.500] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 6 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -4527.833 [-5273.333, -3855.667] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than tool (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -4546.667 [-5292.500, -3872.333] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than procedure (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| pkg-009 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |
| pkg-010 | 0/3 | 0/3 | 3/3 | registry:io.github.presendapp/presend-deps | - |

## Every failure

- **procedure**: 30 of 30
  - error: no_procedure (30): pkg-001#1, pkg-001#2, pkg-001#3, pkg-002#1, pkg-002#2, pkg-002#3, pkg-003#1, pkg-003#2, pkg-003#3, pkg-004#1, pkg-004#2, pkg-004#3, pkg-005#1, pkg-005#2, pkg-005#3, pkg-006#1, pkg-006#2, pkg-006#3, pkg-007#1, pkg-007#2 ...
- **tool**: 30 of 30
  - error: gate:denied (30): pkg-001#1, pkg-001#2, pkg-001#3, pkg-002#1, pkg-002#2, pkg-002#3, pkg-003#1, pkg-003#2, pkg-003#3, pkg-004#1, pkg-004#2, pkg-004#3, pkg-005#1, pkg-005#2, pkg-005#3, pkg-006#1, pkg-006#2, pkg-006#3, pkg-007#1, pkg-007#2 ...
- **baseline**: 3 of 30
  - fail: wrong answer (3): pkg-007#1, pkg-007#2, pkg-007#3

## Caveats

- Only 24 tool/procedure pairs (the plan's bar is 60 tasks x 3 repeats); any direction is preliminary.
- The procedure arm did not run on some attempts: no_procedure 30. These count as errors, so the comparison is partly 'tool vs no procedure', not 'tool vs a procedure'.
- The gate refused 30 tool attempts (no policy row, or a certificate/risk refusal): the tool arm measures what kell is allowed to run, not every card's capability.
- Scores come from each task's own verifier (regex, terms, or SQL rows on a fixture); no judge was used unless a task says rubric. Tool answers are raw tool output (search results), so a pass means the tool surfaced the answer, not that an agent used it correctly.


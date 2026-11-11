# Tool vs procedure: docs

Generated 2026-11-11 13:43 UTC by `python -m scripts.compare.report --goal docs` from task_runs. 10 corpus tasks (2 held out), 10 with runs, 90 attempts.
**Answer:** no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary; baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.


## Main tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 24 | 0% | 0.00 | 0.0000 | 114 | no verified success | 24 |
| tool | 24 | 0% | 0.00 | 0.0000 | 50 | no verified success | 24 |
| baseline | 24 | 100% | 1.00 | 0.0000 | 1688 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.000 [+0.000, +0.000] (n=24) |
| cost delta $ | +0.000 [+0.000, +0.000] (n=24) |
| latency delta ms | -64.208 [-223.500, +23.583] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 0, exact p=1 |

**no difference shown between tool and procedure (95% CI contains 0); only 24 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -1637.625 [-2180.000, -1172.500] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 24, exact p=1.19e-07 |

**baseline scores higher than tool (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=24) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=24) |
| latency delta ms | -1573.417 [-2153.625, -1075.375] (n=24) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 24, exact p=1.19e-07 |

**baseline scores higher than procedure (95% CI excludes 0); only 24 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| docs-001 | 0/3 | 0/3 | 3/3 | registry:io.github.Velox-XR/velox-trifit-developer | - |
| docs-002 | 0/3 | 0/3 | 3/3 | registry:io.github.Velox-XR/velox-trifit-developer | - |
| docs-003 | 0/3 | 0/3 | 3/3 | registry:io.github.Velox-XR/velox-trifit-developer | - |
| docs-004 | 0/3 | 0/3 | 3/3 | registry:io.github.Velox-XR/velox-trifit-developer | - |
| docs-005 | 0/3 | 0/3 | 3/3 | registry:ai.filegraph/document-processing | - |
| docs-006 | 0/3 | 0/3 | 3/3 | registry:io.github.TANTIOPE/datadog-mcp | - |
| docs-007 | 0/3 | 0/3 | 3/3 | registry:ai.filegraph/document-processing | - |
| docs-008 | 0/3 | 0/3 | 3/3 | registry:io.github.TANTIOPE/datadog-mcp | - |

## Held-out tasks

### Arms

| arm | attempts | pass rate | mean score | mean cost $ | mean latency ms | cost per verified success | errors |
|---|---|---|---|---|---|---|---|
| procedure | 6 | 0% | 0.00 | 0.0000 | 11 | no verified success | 6 |
| tool | 6 | 50% | 0.50 | 0.0035 | 325 | $0.0070 | 3 |
| baseline | 6 | 100% | 1.00 | 0.0000 | 5622 | $0.0000 | 0 |

### tool minus procedure (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | +0.500 [+0.167, +0.833] (n=6) |
| cost delta $ | +0.004 [+0.001, +0.006] (n=6) |
| latency delta ms | +314.167 [+60.833, +732.333] (n=6) |
| pass/fail (McNemar) | tool-only passes 3, procedure-only passes 0, exact p=0.25 |

**tool scores higher than procedure (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### tool minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -0.500 [-0.833, -0.167] (n=6) |
| cost delta $ | +0.003 [+0.001, +0.006] (n=6) |
| latency delta ms | -5296.833 [-6812.500, -3724.833] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 3, exact p=0.25 |

**baseline scores higher than tool (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### procedure minus baseline (paired on task and attempt)

| measure | estimate [95% CI] |
|---|---|
| score delta | -1.000 [-1.000, -1.000] (n=6) |
| cost delta $ | -0.000 [-0.000, -0.000] (n=6) |
| latency delta ms | -5611.000 [-7513.500, -3786.333] (n=6) |
| pass/fail (McNemar) | tool-only passes 0, procedure-only passes 6, exact p=0.0312 |

**baseline scores higher than procedure (95% CI excludes 0); only 6 pairs (< 30), treat as preliminary.**

### Per task

| task | procedure pass/attempts | tool pass/attempts | baseline pass/attempts | tool card | procedure |
|---|---|---|---|---|---|
| docs-009 | 0/3 | 0/3 | 3/3 | registry:eu.pauhu/data | - |
| docs-010 | 0/3 | 3/3 | 3/3 | registry:ai.exa/exa | - |

## Every failure

- **procedure**: 30 of 30
  - error: no_procedure (30): docs-001#1, docs-001#2, docs-001#3, docs-002#1, docs-002#2, docs-002#3, docs-003#1, docs-003#2, docs-003#3, docs-004#1, docs-004#2, docs-004#3, docs-005#1, docs-005#2, docs-005#3, docs-006#1, docs-006#2, docs-006#3, docs-007#1, docs-007#2 ...
- **tool**: 27 of 30
  - error: gate:denied (27): docs-001#1, docs-001#2, docs-001#3, docs-002#1, docs-002#2, docs-002#3, docs-003#1, docs-003#2, docs-003#3, docs-004#1, docs-004#2, docs-004#3, docs-005#1, docs-005#2, docs-005#3, docs-006#1, docs-006#2, docs-006#3, docs-007#1, docs-007#2 ...
- **baseline**: none

## Caveats

- Only 24 tool/procedure pairs (the plan's bar is 60 tasks x 3 repeats); any direction is preliminary.
- The procedure arm did not run on some attempts: no_procedure 30. These count as errors, so the comparison is partly 'tool vs no procedure', not 'tool vs a procedure'.
- The gate refused 27 tool attempts (no policy row, or a certificate/risk refusal): the tool arm measures what kell is allowed to run, not every card's capability.
- Scores come from each task's own verifier (regex, terms, or SQL rows on a fixture); no judge was used unless a task says rubric. Tool answers are raw tool output (search results), so a pass means the tool surfaced the answer, not that an agent used it correctly.


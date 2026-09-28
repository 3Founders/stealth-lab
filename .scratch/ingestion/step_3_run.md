| measure | value |
|---|---|
| dataset | `FayeZC/SkillMD-138K` @ `0d73048a` |
| gate version | `skillmd-gate@v1` |
| rows seen | 27184 |
| rows fetched from raw | 2775 |
| rows admitted | 2000 |
| mirror rows | 6634 |
| mirror origin recovered | 3459 |
| exact duplicates | 0 |
| near duplicates | 20 |

### Disposition reasons
| reason | count |
|---|---|
| `mirror_origin_unrecoverable` | 3175 |
| `raw_404_deleted_or_moved` | 1793 |
| `screener_block_finding` | 437 |
| `name_charset_invalid` | 167 |
| `coercive_language` | 48 |
| `frontmatter_absent` | 47 |
| `body_oversized` | 25 |
| `frontmatter_missing_description` | 18 |
| `body_rows_disagree_with_word_count` | 7 |
| `filename_meta_skill_tooling` | 7 |
| `body_too_short` | 3 |
| `description_too_long` | 3 |
| `near_duplicate_of_ios-ux-audit` | 3 |
| `near_duplicate_of_frontend-design` | 2 |
| `near_duplicate_of_internal-comms` | 2 |
| `near_duplicate_of_planning-disaster-recovery` | 2 |
| `near_duplicate_of_using-git-worktrees` | 2 |
| `limit_reached` | 1 |
| `near_duplicate_of_agent-browser` | 1 |
| `near_duplicate_of_get-weather` | 1 |
| `near_duplicate_of_github` | 1 |
| `near_duplicate_of_migrating-apis` | 1 |
| `near_duplicate_of_mx-review` | 1 |
| `near_duplicate_of_react-best-practices` | 1 |
| `near_duplicate_of_skill-creator` | 1 |
| `near_duplicate_of_text-to-speech` | 1 |
| `near_duplicate_of_turborepo` | 1 |
| `scan_budget_exhausted` | 1 |

```json
{
  "basis_rows_seen": 27184,
  "basis_admitted": 2000,
  "assumed_total_rows": 138133,
  "scale_factor": 5.1,
  "projected_admitted": 10163,
  "projected_admitted_fraction": 0.0736,
  "projected_content_bytes": 68361144,
  "caveat": "PROJECTION, not a measurement. The sample is a prefix of the crawler's row order (90.0% registry rows first), not a uniform random sample, so the admitted fraction may not transfer."
}
```
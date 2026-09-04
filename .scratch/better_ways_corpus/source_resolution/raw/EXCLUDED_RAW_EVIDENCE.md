# Raw evidence intentionally excluded from this commit

Three of the 12 raw-evidence files this experiment produced are **not** committed
to this branch, because they contain substantial verbatim excerpts of source
text that should not be redistributed here. The local files still exist (in the
`evaluation-suite` worktree that ran the experiment) for anyone with direct
access to that machine; they are simply not pushed to `origin`.

For each, the essential facts (source URL, pinned commit, license, and this
experiment's own measured numbers) are already fully recorded in
`../candidates.jsonl` and `../final_report.md` — nothing measurable is lost by
excluding the raw file, only the verbatim excerpts are.

## 1. `tokenpilot_hookread_output.json`

Contains ~90 lines of near-verbatim `import`/`export` declarations extracted
directly from TokenPilot's own real source file (`src/index.ts`) — real
repository content, not ours to redistribute.

- Source: `https://github.com/Digital-Threads/token-pilot`
- Commit: `a1df2519b034d6f79fac9e22191ab28cb9311960`
- License: MIT (declared in `package.json`; **no standalone LICENSE file** in
  the repo — weaker provenance than a repo with an actual LICENSE file)
- Measured result (see `candidates.jsonl` candidate `C01` for full detail):
  raw file 17,988 tokens (tiktoken `cl100k_base`) vs. structural-summary
  output 1,289 tokens — 92.8% reduction, independently measured, corroborated
  by the tool's own self-reported estimate (~17,123 tokens).

## 2. `tokenpilot_summary_text.txt`

Same underlying content as (1) above, in plain-text form — same exclusion
reason, same source/commit/license, same candidate (`C01`).

## 3. `stealthlab_deferred_tools_schemas.json`

Contains verbatim tool-description text from this session's own Claude Code
harness (`WebFetch`, `WebSearch`, `CronCreate`, `PushNotification` full JSON
schema definitions) — internal system/harness content, not appropriate to
publish into this repository regardless of the measurement's validity.

Methodology note (not a correctness issue — flagged here for clarity): this
candidate (`C05`, "tool efficiency" family) measures the *same architectural
pattern* the family's seeds describe (advertise tool names only up front, load
a full schema only on demand) using this session's own Claude Code harness
deferred-tool mechanism (`ToolSearch`) as a real, directly-measurable instance
of that pattern — explicitly disclosed in `candidates.jsonl` as `"artifact":
"StealthLab's own deferred/searchable-tool mechanism (native -- this session's
own tool list is the evidence, not a third-party artifact)"`. It is not a
measurement of StealthLab's own MCP tool-schema surface specifically. The
numbers themselves (67.5x reduction, per-tool token counts) are preserved in
`candidates.jsonl` candidate `C05` and are unaffected by this file's exclusion.

- Source: n/a (native harness mechanism, not a third-party artifact)
- License: n/a
- Measured result: deferred name-only listing ≈5.6 tokens/tool vs. full schema
  ≈377.8 tokens/tool average (n=4: WebFetch 509, WebSearch 443, CronCreate 378,
  PushNotification 181) — 67.5x ratio, disclosed as a conservative lower bound
  (two of the four descriptions were lightly abbreviated to write the
  measurement file reliably; the other two were measured verbatim).

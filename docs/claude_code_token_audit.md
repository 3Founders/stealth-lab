# Claude Code token audit for this repo (2026-10-31)

Measured on this checkout with `git ls-files` and ripgrep. Nothing below has been applied; the files to create are at the end.

## 1. What actually costs tokens

Claude Code resends the whole conversation on every request, and about 95% of a session's input tokens are that cached prefix being re-read (Anthropic's costs doc, https://code.claude.com/docs/en/costs). So cost is driven by **how long the conversation is and how many steps it runs**, and the fresh input per step (files read, tool output) is what makes the conversation grow. A trimmed `CLAUDE.md` saves little (it is cached and cheap to re-read); what matters is keeping noisy files and tool output out of the conversation, and keeping sessions short.

## 2. Bloat vectors in this repo

4,093 tracked files, 149 MB. By cost to the conversation:

| Item | Size | Why it hurts |
|---|---|---|
| `backend/conflict_candidates.json` (tracked) | **62.8 MB**, one file | One accidental Read or a grep match on a long line floods the context. The deploy script already deletes it, so it is not source. |
| Data blobs: `backend/app/tool_routing/data/*.gz`, `backend/app/routing/data/*.npz` | about 31 MB | Binary; useless to read. |
| `routing_kell_frontend/public/films/kel-film.mp4` and other media | 42 MB across 51 binary files (gz, npz, mp4, pdf, png) | Binary. |
| Experiment results: `experiments/abtest/results*`, `.log`, `.jsonl` | 114 `.jsonl` files (4.3 MB), 43 `.log` files (2.5 MB), 5 MB under `/results` | Grep hits land in these. A grep for `stealthlab` returns 678 matching lines, with the results `.jsonl` files among the top files. |
| `.claude/` tracked content: 342 files (1.5 MB), including copies of whole frontends under `.claude/worktrees/` | | Duplicates every frontend file in search results. |
| `.scratch/` (448 files, 5.9 MB), `arXiv-1802.04538v2/` (31 files) | | Notes, LaTeX build files, board logs; read rarely. |
| Lockfiles: 16 files, 2.1 MB (`uv.lock` 0.6 MB, three `package-lock.json`) | | Large and low-value if read. |

Always loaded:
- `CLAUDE.md` is **12.8 KB, about 1,700 words (roughly 3,000 tokens)**, plus the global one (0.9 KB). It carries a status table, a doc map and historical notes besides the rules. It is cached after the first turn, so the saving from trimming is small.
- **37 project skills** under `.claude/skills` (plus the plugin skills) each put a description in the system prompt on every session. Many come from one imported pack and are unlikely to be used here.
- There is **no `permissions.deny` rule** in `.claude/settings.json` (3 allow rules only) and no ignore file of any kind.

## 3. `.claudeignore` does not work

`.claudeignore` is not a supported mechanism: one reported GitHub issue says a `.claudeignore` listing `.env` did not stop reads, and the docs name `permissions.deny` in `.claude/settings.json` instead (https://docs.claude.com/en/docs/claude-code/settings). Reports also say deny rules do not catch every route (`grep -r` and `@` imports can get around them), so they reduce accidents; they are not a security boundary. Whether Claude Code's search honors `.gitignore` is unconfirmed; ripgrep itself does honor `.ignore` files, so a root `.ignore` should keep the Grep tool out of the noisy paths (not confirmed for Claude Code's wrapper; test with one search after adding it).

## 4. Files to create (proposed, not applied)

### 4a. `permissions.deny` in `.claude/settings.json` (merge into the existing `permissions` block)

```json
"deny": [
  "Read(./backend/conflict_candidates.json)",
  "Read(./backend/app/tool_routing/data/**)",
  "Read(./backend/app/routing/data/**)",
  "Read(./experiments/abtest/results*/**)",
  "Read(./experiments/**/results/**)",
  "Read(./**/*.jsonl)",
  "Read(./**/*.log)",
  "Read(./**/uv.lock)",
  "Read(./**/package-lock.json)",
  "Read(./arXiv-1802.04538v2/**)",
  "Read(./routing_kell_frontend/public/**)",
  "Read(./.claude/traces/**)",
  "Read(./.claude/worktrees/**)",
  "Read(./**/*.mp4)",
  "Read(./**/*.mov)",
  "Read(./**/*.pdf)",
  "Read(./**/*.npz)",
  "Read(./**/*.gz)"
]
```

If one of these is genuinely needed (a results file you want analysed), read it with a targeted command (`head`, `jq`, `rg -m 5`) or remove that rule for the session.

### 4b. `.ignore` at the repo root (ripgrep syntax, gitignore-style)

```
backend/conflict_candidates.json
backend/app/tool_routing/data/
backend/app/routing/data/
experiments/abtest/results*/
experiments/**/results/
arXiv-1802.04538v2/
routing_kell_frontend/public/
.claude/traces/
.claude/worktrees/
*.jsonl
*.log
*.lock
package-lock.json
*.npz
*.gz
*.mp4
*.mov
*.pdf
```

### 4c. Housekeeping that removes bloat at the source

- `git rm --cached backend/conflict_candidates.json` and add it to `.gitignore` (the deploy script already deletes it).
- Stop tracking `.claude/worktrees/` (342 files incl. duplicate frontends) and `.claude/traces/`.
- Disable the project skills nobody uses (`.claude/skills`, 37 entries) and unused MCP servers.

### 4d. Streamlined `CLAUDE.md` template (about 300 words; move the status table, doc map and historical list to `docs/`)

```markdown
# CLAUDE.md — StealthLab / kell

Verified procedural memory for AI agents. Backend `backend/app` (only importable package), migrations `backend/db`, website `routing_kell_frontend/`, admin `admin/`, client package `packaging/npm`.
Read first: `proj_status.md`, `.scratch/build-board.md`. Spec v4 and `schema.md` are frozen.

## Working rules (token discipline)
- Search before reading: use Grep/Glob with a narrow pattern or path; never read a whole directory. Read only the lines you need (offset/limit).
- Never open data files, results, logs, lockfiles or binaries (see `.claude/settings.json` deny list). Use `head`, `jq` or `rg -m 5`.
- Tests: run the one failing test (`pytest path::test -q`), not the suite. The default `pytest` run is the fast suite (~3 min); slow tests are marked `slow`.
- Keep command output short: add `| tail -30`, `-q`, `--stat`, `--short`. Never cat a log.
- Edits: smallest diff; do not re-read a file after editing it.
- After 10 steps without a clear result, stop and report what you know and what you need.
- One task per session; say so when a task is finished so the session can be cleared.

## Hard rules
Fresh-start (no backfills); scope + provenance on all stored data; stay inside your lane's paths; exit criteria are numbers; proving tests ship with the code.

## Conventions
Tenant SQL only via `scope_predicates()`; write paths use `tenant_transaction`; evidence via `execution/evidence.py`; redaction before INSERT; `uuid7()` for ids; config via `settings.require`; migrations are immutable and idempotent; commits are prefixed with the lane tag and carry pytest counts.
```

## 5. Top three habits

1. **Name the file, function or line in the prompt** ("in `app/services/plans.py`, `activate()`") instead of "find where...". It removes the exploratory searches that fill the conversation.
2. **One task per session; `/clear` or start fresh between tasks.** Every later turn re-reads everything before it, so a long session costs more per step than a short one.
3. **Keep tool output small:** run one test, not the suite; pipe long output through `tail`; ask for a summary of a big file's relevant part, not the file.

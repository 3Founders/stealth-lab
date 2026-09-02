# AGENTS.md

> Entry point for OpenAI Codex / Cursor / Devin / Windsurf and any
> other AI agent that auto-reads `AGENTS.md` at repo root.
>
> This project follows the [agents-md](https://github.com/maweis1981/agents-md)
> AI-native development standard (`v0.1.x`).

## TL;DR — non-negotiable rules

1. **No direct pushes to `main`.** Use `ai/<feature>` branches.
2. **Don't commit on every change.** Group local edits into one
   stable commit per logical feature.
3. **One PR = one feature.** Draft until ready. Squash merge only.
4. **CI is PR-triggered, not push-triggered.** Production deploy is
   from `main` only.
5. **DB migrations** run on a **direct** connection; runtime uses
   **pooled**. Aggregate schema changes; don't migrate per prompt.
6. **Search before generating.** Reuse existing code; don't create
   parallel duplicates.
7. **Cap retries at 3.** Then surface the failure. No infinite loops.
8. **No polling.** Use events / webhooks / queues.
9. **Track material work in Issues.** Start only Ready work, keep
   Project status truthful, and link the implementation PR.

## Issue and Project tracking

- Find or create the canonical Issue in the repository that owns the
  primary deliverable before material implementation.
- Use one parent Issue and linked repository-specific sub-issues for
  cross-repository outcomes; do not create unlinked duplicates.
- Confirm Definition of Ready, assign ownership, and record dependencies
  and blockers before starting.
- Link the Pull Request and mark Done only after Definition of Done is met.
- Do not change Priority, Iteration, Target date, scope, or acceptance
  criteria when that creates a new commitment without human authority.

## Project specifics (edit me)

- **Stack**: <e.g. Next.js + Prisma + Neon, deployed on Vercel>.
- **Branches**: `ai/<feature-name>`, kebab-case, short-lived.
- **Commits**: Conventional Commits — `feat`, `fix`, `refactor`,
  `docs`, `chore`, `test`, `perf`, `build`, `ci`, `style`, `revert`.
- **Tests**: `<your test command>`.
- **Lint**: `<your lint command>`.
- **Migrations**: `<your migration command>`; uses `DIRECT_URL`.

## What you must not do

- `git checkout main` followed by edits.
- `git push origin main`.
- Force-push to `main` or to a review branch in progress.
- Commit with messages like `wip`, `tmp`, `fix`, `update`, `asdf`.
- Add a workflow that runs full deploy on every `push`.
- Run migrations against the pooled DB URL.
- Open a PR that bundles unrelated changes.
- Open a polling loop (`while (true) { await sleep(...) }`).

## Pre-push checklist

- [ ] Branch is `ai/<feature>`.
- [ ] Commit message follows the format.
- [ ] No secrets / `.env` / credentials in the diff.
- [ ] Local tests / type-check pass.
- [ ] No infinite loops or pollers left running.
- [ ] Migration (if any) verified, reversible / rollback documented.
- [ ] Not pushing just to "see if CI passes".

## Reference

Full spec: <https://github.com/maweis1981/agents-md/blob/main/STANDARDS.md>.
中文: <https://github.com/maweis1981/agents-md/blob/main/STANDARDS.zh-CN.md>.
Workflow: <https://github.com/maweis1981/agents-md/blob/main/docs/github-issues-projects.md>.

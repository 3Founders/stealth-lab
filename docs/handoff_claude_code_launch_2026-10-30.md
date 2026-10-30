# Handoff: make the Claude Code side of kell usable by a real user (2026-10-30)

For Chaitanya's Claude Code. Written by Anuj's session; Chaitanya can paste it as the prompt.

You are working in the stealth-lab repo with Chaitanya. Goal: a person who has never seen kell can install it,
sign in, and use it inside Claude Code: cheap models answer and fix what they can, the session's model does the
rest, and tools are suggested at the right moment. Claude web comes later; skip it here.

Work through the sections in order. Confirm with Chaitanya before anything that publishes, deploys, or writes a
secret. Never paste or print a key, and never commit one.

## 0. Where things stand

- `main` is at `9e1edfb` or later. Read `docs/tool_routing_plan.md` ("Done so far"),
  `docs/results_2026-10-30.md` and `docs/cheap_models_notes.md` for the context.
- Hosted MCP server: Railway project `kell-mcp`, service `kell-mcp`, https://mcp.kell.org.in.
  - It is **not deployed on push.**
  - Live deploy: `cbdf0d0f`, built from `9e1edfb`. Migration `160_tool_cards.sql` is applied: three new tables
    (`tool_cards`, `tool_card_vectors`, `tool_terms`), nothing existing changed.
  - All 48,124 tool cards are in the database. Their embeddings wait for `DEEPINFRA_KEY` (below).
- The npm package `kell-mcp` (`packaging/npm`, version 0.1.0) has **never been published**: `npm view kell-mcp`
  returns 404.

## 1. Server

1. **DEEPINFRA_KEY.** Anuj entered it in the Railway dashboard on 2026-10-30, but `railway variables --service
   kell-mcp` did not list it afterwards. It is probably a staged change waiting for **Deploy** on the dashboard's
   banner.
   - Check that it appears in `railway variables --service kell-mcp --json` (names only: never print values).
   - Without it, the tool router uses keywords only: no embeddings, no reranker.
   - Once it's live, the next start embeds the 48k cards: look for `tool catalog: embedded N/...` lines, about 35
     minutes, once.
2. **Deploying the current main** (only when the server changes). This is how the service is deployed (an upload over about 100 MB times out):
   ```bash
   H=$(git rev-parse --short HEAD); ST=.scratch/deploy_stage; rm -rf $ST; mkdir -p $ST
   git archive $H backend | tar -x -C $ST
   git show $H:backend/Dockerfile.mcp-server > $ST/Dockerfile
   rm -f $ST/backend/app/tool_routing/data/.gitignore $ST/backend/conflict_candidates.json
   railway up $ST --path-as-root --no-gitignore --service kell-mcp --detach -m "$H: <what>"
   ```
   The container runs `scripts/migrate.py` against production before it serves. List the new
   `backend/db/*.sql` files since the last deploy, and read them, before deploying.
3. **Verify after the deploy.**
   - `/readyz` returns 200.
   - The deploy log shows `applied 160_tool_cards.sql`.
   - A few minutes later the log shows `tool catalog: 48124 cards, ... written`. With the key set, it then shows
     `tool catalog: embedded N/48124` progress for about 35 minutes; that runs once, and later restarts skip it.
   - **Storage:** the tool tables take about 440 MB in Postgres. Check the Neon plan's storage limit first.

## 2. npm package

1. **Decide with Chaitanya:**
   - The version, for example `0.2.0`.
   - Whether `"license": "UNLICENSED"` stays. It publishes publicly but the code is proprietary.
2. **Local checks in `packaging/npm`:**
   - `npm test`: 805 tests, 0 failing as of `9e1edfb`.
   - `node scripts/check-publish.mjs`: the URL must be identical in `package.json`, `install/install.sh` and
     `install/install.ps1`; today it's `https://mcp.kell.org.in/mcp`.
   - `npm pack --dry-run`: read the file list. No `.env`, no keys, nothing from `.kell/` or `.scratch/`.
     `lib/tool_note.mjs` must be included.
3. **Publish.**
   - The GitHub secret `NPM_TOKEN` is needed: an npm automation token from the account that will own `kell-mcp`.
   - Bump `version`, commit, then push the tag `kell-mcp-v<version>`. `.github/workflows/publish-mcp-client.yml`
     publishes it.
4. **Installers.**
   - The site serves `/install.sh` and `/install.ps1` (`routing_kell_frontend/scripts/sync-installers.mjs` copies
     them from `packaging/npm/install/`). Check the live site serves the current ones.
   - Fill in the `<site>` placeholders in `packaging/npm/README.md`'s install table.

## 3. One decision to make: who pays for the cheap models

- Dispatch (cheap models trying first) needs a provider key.
  - Today, `kell setup` writes `~/.stealthlab/exec.json` and expects the user's own key in `GENERAL_COMPUTE_API_KEY`
    (`packaging/npm/lib/setup.mjs`).
  - Or the user joins an organisation whose gateway holds the key: `kell join <invite> --url ... --api ...`.
- The pricing deck says usage is included. That needs the gateway path or credits, and credits are "built, not
  switched on".
- Pick one for the first users and write it into the install instructions:
  - (a) Bring your own key: simplest, works today.
  - (b) A kell-run organisation that pilot users join.
- Without a key, kell still connects and gives lookups and tool notes, but `kell setup` exits 3 and dispatch stays off.

## 4. Acceptance test: a clean profile, as a new user would

Use a machine or a user profile with no kell on it. For Claude Code, a fresh `CLAUDE_CONFIG_DIR` pointing at an
empty folder works. Use a small Python or JS repo that has tests.

1. `npx -y kell-mcp@<version> setup`. It should exit 0, or 3 with a clear list of what's missing; fix each item.
2. `kell login`: the browser OAuth flow on a brand-new account, then `kell doctor`.
3. Start `claude` in the test repo and send these prompts. After each one, check `~/.stealthlab/hooks.log`;
   `STEALTHLAB_ROUTING_TIMING=1` adds timings.

| Prompt | Expected |
|---|---|
| "what does `<function>` in `<file>` do?" | triage `work=answer`. A cheap model answers after reading the repo, Sonnet checks it, and the reply arrives before the session's model would run. |
| a small real bug with a failing test | dispatch: a cheap model fixes it in a worktree, the tests pass, Sonnet reviews, the change is applied, and a report appears. `kell undo` puts it back. |
| "check that the login page still renders in a real browser" | triage `tool=browser`; the log shows `tools goal=browser N found`. The context has a "kell tool router" note: installed servers first (add Playwright with `claude mcp add` beforehand to see this), then one to add. |
| "rename foo to bar in utils.py" | no tool note, no knowledge dump, no long wait |
| "thanks, looks good" | nothing added |
| "!opus explain the architecture" | kell skips the prompt |

4. Watch for these, and fix them:
   - Does the console open a new browser tab every time a session starts? Anuj saw this; it should not happen.
   - Does any prompt hang the hook for more than a minute?
   - Does any error reach the session instead of `hooks.log`?
5. `kell uninstall`: Claude Code's `settings.json` keeps the user's own hooks, and kell's are gone.

## 5. Known issues, not blockers

- `backend/tests/test_auth_enforcement_offline.py::test_every_registered_mcp_tool_is_classified` fails because the
  tool `submit` is unclassified. This predates the tool router work; classify it.
- With dispatch, kell is about 2× slower than plain Opus. The cheap-model turns are the largest share of that.
- The reviewer is sometimes inconsistent on prompts with a false premise.
- Bare approvals ("yes do it" with nothing before it) are handled by a guard in the client; the JEV server prompt
  fix for them is still open.

Report back with: what you changed, the published version, the acceptance table filled in with what actually
happened, and anything still open.

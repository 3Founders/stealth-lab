# Orphaned lane/core-b fixes — preservation check

Before branching `evaluation-suite` off `origin/main`, checked two commits on
`lane/core-b` (`888bbbe`, `638a324`) that looked like they might be dangling
work never merged upstream. Verified against current `main` before writing
anything here, per house rules on not assuming.

## `888bbbe` — pytest-asyncio dep + python3→python doc fix

Found during a fresh-clone dry run (clone repo, follow `backend/README.md`
top to bottom as a new user, no shortcuts):

- `backend/requirements.txt` was missing `pytest-asyncio` despite tests using
  `@pytest.mark.asyncio` — caused 23 silent test failures on a bare install.
- `backend/README.md`'s migration step used `python3`, which resolves to the
  Windows Store stub instead of a real interpreter, contradicting this
  repo's own documented python-not-python3 rule.
- Also pointed root `README.md`'s Tests line at `backend/.env.example`,
  since the documented test command crashes at collection time without it.

## `638a324` — stray "7 tools"/"8 tools" doc mentions → "9 tools"

Bumped leftover tool-count mentions in `packaging/README.md`,
`server_entry.py`'s `--help` text, and one leftover line in
`README_MCP_SERVER.md` from 7/8 to 9, to match the table-level fix landed
elsewhere.

## Verification against current `main`

**Neither commit is actually orphaned.** Both are byte-identical in diff
content to commits that are already ancestors of `origin/main`:

- `888bbbe` diffs identical (zero delta) to `5c83430` — already on `main`.
  Confirmed directly: `origin/main`'s `backend/requirements.txt` already
  contains `pytest-asyncio`, and `backend/README.md` has zero `python3`
  mentions.
- `638a324` diffs identical (zero delta) to `41fd794` — already on `main`.

`5c83430` and `41fd794` carry the same commit messages as `888bbbe`/`638a324`
and were `lane/core-b`'s HEAD at the point `evaluation-suite` was branched
off. Best read: these are the same fixes, recommitted with new SHAs during a
rebase, and the rebased versions made it upstream — leaving `888bbbe` and
`638a324` as dangling pre-rebase duplicates, not lost work.

The "9 tools" bump from `638a324` is also independently stale regardless:
current tool count is 20 per `proj_status.md`, so even the already-landed
`41fd794` version of that fix needs a fresh count pass, not a re-application
of the "9" text.

## Recommendation

No cherry-pick or re-land needed — both fixes are already on `main` via
`5c83430` and `41fd794`. `888bbbe` and `638a324` can be treated as stale,
superseded duplicates and left dangling (or pruned) rather than merged
anywhere, including into this `evaluation-suite` branch.

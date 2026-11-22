# A brief per Goal (design draft, not built, not pushed)

Replaces section G of the first version of `docs/routing_issues.md`. The change: no new "task" primitive. A prompt
resolves to a **Goal**, and the brief hangs off the Goal.

## Why the Goal is the key

The pieces already line up on it:

- `find_ways` resolves a prompt to a Goal (`resolved`, `ambiguous` or `no_match`), and a Way (procedure) belongs to a Goal.
- The routing logs are sharded by Goal: an `instance_key` is `<goal_id>.<random>` (`backend/app/routing/plan.py`), and a
  `ROUTE` line in `.kell/routing.md` carries `goal=`.
- `.kell/library.md` already has Goal lines (`G|<G-id>|title|parent=…`), Way lines (`W|…|goal=<G-id>`) and Step lines
  (`S|<W-id>:<k>|kind|do|check=`), and a library entry links to them with `goal=` and `way=`.
- Claims already cite `source=file:line#sha`.

Many prompts map to one Goal (a follow-up, a retry, a second session), so one brief per Goal is updated over time.
A prompt that resolves to nothing gets a Goal created locally the way `submit_way` creates one for `no_match`; a prompt
that is only a question does not create or update anything.

## Where it lives

`.kell/goals/<G-id>.md`, one file per Goal, plus a line in `.kell/SUMMARY.md` for the Goal currently being worked on.
Generated and kept up to date by kell, like `routing.md`; not hand-edited.

## Format

```
GOAL      <G-id>  <title>
OBJECTIVE <one verifiable sentence>
DONE WHEN <the check command, or "no check: needs review">

CLAIMS    only those that bear on this Goal (budget about 2 KB)
  R-014   <fact>                                   src/a.py:120#sha=…   verified 2026-10-28
  R-031   <fact>                                   src/b.py:44#sha=…    STALE: file changed since

PROCEDURE <W-id>  <name>      (from the judged Way, or the last passing entry for this Goal)
  1  <what to do>             at src/a.py:130-160 (fn name)#sha=…       check: <cmd>
  2  <what to do>             at src/b.py:44-60#sha=…                    check: <cmd>

ROUTE     <R-id>  first <model>, then <model>; stop on check pass; then the session model
HISTORY   <date> <prompt id> <model that did it> <check result> <cost> <seconds>
```

## How it is made

When a prompt arrives and `find_ways` returns a Goal (or none), the hook assembles the brief from:

1. the Goal (id, title, objective) and its Way's steps, only if the match was **judged**, never "matched by words only";
2. claims whose cited file or symbol overlaps the located files (`locate.mjs`), plus a small always-on core (how to run
   the tests), under the budget;
3. locators for each step from the Way's `at=` fields, re-anchored by content hash.

Both the session model and the cheap executors get this same text, instead of the library excerpt and diff the hook
injects today.

## How it is updated

After the check runs, write back to the Goal's file and `.kell/library.md`:

- the outcome and cost in HISTORY, and the attempt counts in `routing.md` (already per Goal);
- claims the session found wrong, or whose cited line moved, marked STALE for `kell survey --validate` to recheck;
- the steps actually taken, with real file:line locators from the files the session edited (`sessionEdits` in
  `dispatch.mjs`), not the whole working tree;
- on a pass, promote to a Way under this Goal; on a question prompt, write nothing.

## What this fixes, from `docs/routing_issues.md`

| Item | How |
|---|---|
| 8, 9 | Only change prompts update a Goal, and only with the session's own edits |
| 13 | Nothing unjudged is injected; the size is capped |
| 10 | Checks are stored per step against the repo root, not as pasted heredocs |
| 24, 25 | The Goal and its located files give a per-task feature vector and a coverage hint to routing |
| 33 | HISTORY is the per-decision record |

## Open

- Wording: the shipped instructions (server `instructions`, the AGENTS.md block, the `/connect` prompt) still say "the
  unit is a TASK, a goal plus its context". If the Goal is the primitive, those need rewording to match.
- How many HISTORY rows to keep per Goal before pruning.
- Whether a Goal created locally is private to the repo until someone shares it (`shareDraft` in `library.mjs` is the
  existing path).

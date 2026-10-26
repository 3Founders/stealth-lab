# .kell/collab -- the shared plan for organization keys and developer join

Everything else in `.kell/` is a generated, machine-local projection and is ignored by git. This folder is the exception:
three hand-written files in the plan_and_run format that anyone (a person or a coding agent) can read and update.

- `claims.md`   facts about the work, each with a source file, line and blob sha. Stale a fact by status, never edit in place.
- `procedures.md`   the end-to-end procedure (operator, owner, developer).
- `run.md`   nodes: `done` (built, check passed offline), `ready` (take it), `blocked` (waiting on `deps`).

Take a node: set it to `running`, do it, run its `check=`, set it to `done`, add what you learned as a claim. Validate:

    mkdir -p /tmp/p/.kell && cp .kell/collab/*.md /tmp/p/.kell/ && npx stealthlab-mcp plan validate --root /tmp/p

Nothing here holds a secret, a customer name or a price. Keep it that way: this folder is pushed.

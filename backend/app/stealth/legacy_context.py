"""Names shared by the `.kell/` modules that remain (sync, edit ledger, journal, exploration).

The server-side projection that used to write `.kell/` (context.md, run.json, meta.json and the pages, from
`app.stealth.generator`) was removed on 2026-10-08: nothing in the live server or API called it, and the agent now
owns every `.kell/` file (the `plan_and_run` and `survey_repo` prompts, `stealthlab-mcp survey|library|plan`).
"""
from __future__ import annotations

import os

STEALTH_DIRNAME = ".kell"
# The folder's name before the rename to `.kell`.
LEGACY_DIRNAME = "." + "stealth"


def workspace_dir(root: str) -> str:
    """`root/.kell`. A workspace that still has only the pre-rename `root/.stealth` gets it renamed first; if the
    rename fails (a file in it is open, say), the old folder is used as it is and renamed on a later call."""
    cur = os.path.join(root, STEALTH_DIRNAME)
    old = os.path.join(root, LEGACY_DIRNAME)
    if not os.path.exists(cur) and os.path.isdir(old):
        try:
            os.rename(old, cur)
        except OSError:
            return old
    return cur

# The `.kell/*.md` pages a person or agent may hand-edit and the edit ledger records. `ledger.md` is excluded on
# purpose: it is generated from the ledger itself.
CONTENT_PAGE_FILES: tuple[str, ...] = ("claims.md", "procedures.md", "goals.md", "run.md")
OPTIONAL_CONTENT_PAGE_FILES: tuple[str, ...] = ("exploration.md",)

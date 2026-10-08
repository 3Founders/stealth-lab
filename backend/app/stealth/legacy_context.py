"""Names shared by the `.stealth/` modules that remain (sync, edit ledger, journal, exploration).

The server-side projection that used to write `.stealth/` (context.md, run.json, meta.json and the pages, from
`app.stealth.generator`) was removed on 2026-10-08: nothing in the live server or API called it, and the agent now
owns every `.stealth/` file (the `plan_and_run` and `survey_repo` prompts, `stealthlab-mcp survey|library|plan`).
"""
from __future__ import annotations

STEALTH_DIRNAME = ".stealth"

# The `.stealth/*.md` pages a person or agent may hand-edit and the edit ledger records. `ledger.md` is excluded on
# purpose: it is generated from the ledger itself.
CONTENT_PAGE_FILES: tuple[str, ...] = ("claims.md", "procedures.md", "goals.md", "run.md")
OPTIONAL_CONTENT_PAGE_FILES: tuple[str, ...] = ("exploration.md",)

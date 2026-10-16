import re

p = r"C:\Users\user\sl-merge\backend\app\mcp_server\prompts.py"
s = open(p, encoding="utf-8").read()
merged = """1. Facts. If `.stealth/claims.md` is missing, run the survey_repo prompt first. Read
   `.stealth/SUMMARY.md` whole if it exists, then look the task's files, symbols and error text up in
   this repo's own solved problems: `rg -i '^<term>\\\\|' .stealth/index/terms.idx` -> library ids.
2. Ask. Call `find_ways(query=<the task>, repo_claims=<text of .stealth/claims.md, then the
   .stealth/claims/<unit>.md pages of the units the task touches (index/units.idx maps a path
   to its page)>, ...)` once, adding this repo's library arguments when they exist:
   `stealthlab-mcp library payload` prints exactly `library_rows`, `route_obs` and `repo_identity`
   (pass them as they are).
   - `library_matches` (if any) come first: problems already solved IN THIS REPO. Read each one
     (`rg '^(GOAL|PROC|STEP)\\\\|<id>' .stealth/library.md`) and its diff (`.stealth/library/solutions/<id>.diff`)
     before anything global. A `stale` one touched files that changed since: re-check it before reuse.
"""
new, n = re.subn(r"<<<<<<< HEAD\n.*?=======\n.*?>>>>>>> feat/library\n", lambda m: merged, s, flags=re.S)
assert n == 1 and "<<<<<<<" not in new
open(p, "w", encoding="utf-8", newline="").write(new)
print("ok")

# library.md -- problems solved in THIS repository, with the diffs that solved them.
# Committed (team knowledge). One entry per id; lines may be in any order (merge=union safe).
# GOAL|<L-id>|<title>|unit=<path or .>|g=<global goal id or ->|outcome=<pass|historical|fail>|status=<current|stale>|verified_at=<date>|route=<R-id or ->|tags=<csv or ->
# PROC|<L-id>.p<n>|<name>|p=<global procedure id or ->|solution=<solutions/<L-id>.diff or ->|touches=<path#sha=<sha>,...>
# STEP|<L-id>.p<n>:<k>|<action|instruction|subgoal>|<do>|check=<command or ->
# Knowledge (reusable; content-hash ids): G|<G-id>|<title>|parent=|g=|unit=|tags=   W|<W-id>|<name>|goal=<G-id>|p=|v=   S|<W-id>:<k>|<kind>|<do>|check=
# An entry links to it with goal=<G-id> on GOAL and way=<W-id> on PROC.

GOAL|L-0a91f2|Fix KeyError %7C when the config has no "db" section (90% %257C of users)|unit=.|g=-|outcome=historical|status=current|verified_at=2026-08-02|route=-|tags=config|commit=0a91f2e7c1
PROC|L-0a91f2.p1|Default the section|p=-|solution=solutions/L-0a91f2.diff|touches=src/config%2Cloader.py#sha=77aa001|diff=truncated
STEP|L-0a91f2.p1:1|action|Use config.get("db", {})|check=pytest -q tests/test_config.py %7C tail -1

GOAL|L-77d3e0|Speed up the slow integration test by reusing the Postgres container|unit=services/worker|g=-|outcome=fail|status=current|verified_at=2026-10-01|route=-|tags=-

GOAL|L-b00c1e|Make the CSV export handle commas inside quoted fields|unit=packages/api|g=2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11|outcome=pass|status=stale|verified_at=2026-10-05|route=R-b00c1e|tags=csv,export
PROC|L-b00c1e.p1|Use the csv module's quoting instead of str.split|p=-|solution=solutions/L-b00c1e.diff|touches=packages/api/export.py#sha=4e1f2a9,packages/api/tests/test_export.py#sha=a0b1c2d
STEP|L-b00c1e.p1:1|action|Replace the manual split with csv.reader|check=pytest packages/api/tests/test_export.py -q
STEP|L-b00c1e.p1:2|instruction|Add a regression test with "a,b" in a quoted cell%0Aand one with an embedded newline|check=-

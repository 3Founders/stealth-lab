# library.md -- problems solved in THIS repository, with the diffs that solved them.
# This fixture is what `git merge` with `merge=union` leaves when two branches each appended
# an entry, one branch edited an existing entry, and both kept an identical line.

GOAL|L-b00c1e|Make the CSV export handle commas inside quoted fields|unit=packages/api|g=2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11|outcome=pass|status=current|verified_at=2026-09-30|route=R-b00c1e|tags=csv,export
PROC|L-b00c1e.p1|Use the csv module's quoting instead of str.split|p=-|solution=solutions/L-b00c1e.diff|touches=packages/api/export.py#sha=4e1f2a9,packages/api/tests/test_export.py#sha=a0b1c2d
STEP|L-b00c1e.p1:1|action|Replace the manual split with csv.reader|check=pytest packages/api/tests/test_export.py -q
GOAL|L-0a91f2|Fix KeyError %7C when the config has no "db" section (90% %257C of users)|unit=.|g=-|outcome=historical|status=current|verified_at=2026-08-02|route=-|tags=config|commit=0a91f2e7c1
STEP|L-b00c1e.p1:2|instruction|Add a regression test with "a,b" in a quoted cell%0Aand one with an embedded newline|check=-
PROC|L-0a91f2.p1|Default the section|p=-|solution=solutions/L-0a91f2.diff|touches=src/config%2Cloader.py#sha=77aa001|diff=truncated
STEP|L-0a91f2.p1:1|action|Use config.get("db", {})|check=pytest -q tests/test_config.py %7C tail -1
GOAL|L-b00c1e|Make the CSV export handle commas inside quoted fields|unit=packages/api|g=2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11|outcome=pass|status=stale|verified_at=2026-10-05|route=R-b00c1e|tags=csv,export
STEP|L-b00c1e.p1:1|action|Replace the manual split with csv.reader|check=pytest packages/api/tests/test_export.py -q
GOAL|L-77d3e0|Speed up the slow integration test by reusing the Postgres container|unit=services/worker|g=-|outcome=fail|status=current|verified_at=2026-10-01|route=-|tags=-
STEP|L-ffffff.p1:1|action|this step has no PROC or GOAL|check=-
this line is not part of the grammar

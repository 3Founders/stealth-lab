"""
Builds the REAL hint the way the actual architecture is supposed to
work -- a masked TaskNode trajectory (typed slots, an ordered step
chain, explicit postconditions), NOT Task A's literal pasted source
code (what the first version of run_task_b_2x2.py used).

Honest reasoning for this file existing: pasting real working code let
the model nearly copy-adapt it mechanically (real field names, real
logic, ready to reuse) -- a materially easier task than what the
platform's actual mechanism provides (retrieval of a masked schema +
step chain, requiring genuine slot-filling against an abstract
pattern). This is hand-authored FROM Task A's real, actually-solved
logic (not fabricated) -- the abstraction step a real TaskNode would
have encoded automatically, done by hand here since no automatic
abstraction pipeline exists yet.
"""

TASK_A_MASKED_TRAJECTORY = """\
Reusable task pattern (retrieved -- solved successfully on a prior, structurally similar task):

TaskNode: csv_groupby_aggregate_to_json
io_schema:
  input: a CSV file with columns including {GROUP_KEY}, {VALUE_FIELD}, {STATUS_FIELD}
  output: a JSON file, a single object whose keys are the distinct {GROUP_KEY} values
    (sorted alphabetically), each mapping to an object with:
      - "total_{VALUE_FIELD}": sum of {VALUE_FIELD} for that group, rounded to {ROUND_DIGITS} decimals
      - "count": number of included rows for that group

Ordered steps (this exact order succeeded before):
  1. Open the input CSV with csv.DictReader.
  2. For each row, check {STATUS_FIELD}; if it equals {EXCLUDED_STATUS_VALUE}, skip this row entirely
     (do not include it in any sum or count).
  3. For remaining rows, accumulate a running sum of {VALUE_FIELD} and a running count, keyed by
     {GROUP_KEY}.
  4. After processing all rows, round each group's summed value to {ROUND_DIGITS} decimal places.
  5. Build the final output dict with keys in alphabetically sorted order (Python's sorted() on
     the group keys).
  6. Write the result as JSON to the output file, with indent=2 for readability.

Postconditions (verified on the prior task, verify the same here):
  - output is valid JSON
  - every key present in the output is sorted alphabetically relative to the others
  - excluded-status rows must not appear in any sum or count
  - rounding must be exact to the specified decimal count

This pattern solved a prior task about grouping transactions by category, summing amounts,
and excluding voided transactions. Fill in {GROUP_KEY}, {VALUE_FIELD}, {STATUS_FIELD},
{EXCLUDED_STATUS_VALUE}, and {ROUND_DIGITS} for the NEW task below -- the field names and
specific values will be different; the pattern and step order are what transfers.
"""


TASK_DECOY_MASKED_TRAJECTORY = """\
Reusable task pattern (retrieved -- solved successfully on a prior, structurally different task):

TaskNode: csv_dedupe_by_key
io_schema:
  input: a CSV file with columns including {KEY_FIELD} (may repeat across rows)
  output: a JSON file, a single object with:
      - "unique_records": a list of the FIRST-seen row (as an object) for each distinct {KEY_FIELD}
      - "duplicates_removed": count of rows that were dropped because their {KEY_FIELD} had
        already been seen

Ordered steps (this exact order succeeded before):
  1. Open the input CSV with csv.DictReader.
  2. Track which {KEY_FIELD} values have already been seen, in a set.
  3. For each row, if its {KEY_FIELD} has already been seen, skip it and increment a
     duplicates-removed counter; otherwise keep the row and mark the key as seen.
  4. Write the kept rows as a JSON list under "unique_records", plus the removed count under
     "duplicates_removed".

Postconditions:
  - output is valid JSON
  - "unique_records" contains exactly one row per distinct {KEY_FIELD} value, the first one seen
  - "duplicates_removed" equals the total row count minus len(unique_records)

This pattern solved a prior task about deduplicating a customer records file by customer ID.
Deliberately a DIFFERENT problem shape than the groupby/aggregate pattern above -- no summing,
no status-based exclusion, output is a list plus a count, not a dict of per-group aggregates.
"""

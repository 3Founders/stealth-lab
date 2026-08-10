You are given a CSV file at `activity_logs.csv` with columns: date, event_type, duration, status.

Your task:
1. Group the log entries by `event_type`.
2. For each event_type, compute:
   - `total_duration`: the sum of `duration` for that event_type, rounded to 1 decimal place.
   - `count`: the number of entries for that event_type.
3. Exclude any row where `status` is `"cancelled"` from all calculations. Rows with other statuses (e.g. "completed", "pending") should be included.
4. Write the result to `output.json` as a single JSON object, with event_type names as keys (sorted alphabetically), each mapping to an object with `total_duration` and `count`.

Example shape (values illustrative only):
```json
{
  "event_a": {"total_duration": 123.4, "count": 3},
  "event_b": {"total_duration": 67.8, "count": 1}
}
```

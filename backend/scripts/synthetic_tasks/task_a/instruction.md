You are given a CSV file at `transactions.csv` with columns: date, category, amount, status.

Your task:
1. Group the transactions by `category`.
2. For each category, compute:
   - `total_amount`: the sum of `amount` for that category, rounded to 2 decimal places.
   - `count`: the number of transactions in that category.
3. Exclude any row where `status` is `"void"` from all calculations. Rows with other statuses (e.g. "completed", "pending") should be included.
4. Write the result to `output.json` as a single JSON object, with category names as keys (sorted alphabetically), each mapping to an object with `total_amount` and `count`.

Example shape (values illustrative only):
```json
{
  "category_a": {"total_amount": 123.45, "count": 3},
  "category_b": {"total_amount": 67.89, "count": 1}
}
```

You are given a CSV file at `shipments.csv` with columns: date, warehouse, quantity, status.

Your task:
1. Group the shipment records by `warehouse`.
2. For each warehouse, compute:
   - `total_quantity`: the sum of `quantity` for that warehouse, rounded to the nearest whole number (0 decimal places).
   - `count`: the number of shipment records for that warehouse.
3. Exclude any row where `status` is `"returned"` from all calculations. Rows with other statuses (e.g. "shipped", "pending") should be included.
4. Write the result to `output.json` as a single JSON object, with warehouse names as keys (sorted alphabetically), each mapping to an object with `total_quantity` and `count`.

Example shape (values illustrative only):
```json
{
  "warehouse_a": {"total_quantity": 1234.0, "count": 3},
  "warehouse_b": {"total_quantity": 567.0, "count": 1}
}
```

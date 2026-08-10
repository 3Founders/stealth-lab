import csv
import json
from collections import defaultdict
from pathlib import Path

def process_transactions():
    input_path = Path("transactions.csv")
    output_path = Path("output.json")

    category_data = defaultdict(lambda: {"total_amount": 0.0, "count": 0})

    try:
        with open(input_path, 'r', newline='', encoding='utf-8') as csvfile:
            try:
                reader = csv.DictReader(csvfile)
            except Exception:
                csvfile.seek(0)
                sample = csvfile.read(1024)
                csvfile.seek(0)
                if ';' in sample and ',' not in sample:
                    reader = csv.DictReader(csvfile, delimiter=';')
                else:
                    reader = csv.DictReader(csvfile)

            if not reader.fieldnames:
                raise ValueError("CSV file appears to be empty or malformed")

            required_columns = {'date', 'category', 'amount', 'status'}
            available_columns = set(reader.fieldnames)

            if not required_columns.issubset(available_columns):
                column_map = {}
                for req_col in required_columns:
                    for avail_col in reader.fieldnames:
                        if req_col.lower() == avail_col.lower():
                            column_map[req_col] = avail_col
                            break
                if len(column_map) != len(required_columns):
                    missing = required_columns - set(column_map.keys())
                    print(f"Warning: Missing columns: {missing}. Using available columns.")
                    if 'category' not in column_map:
                        with open(output_path, 'w') as f:
                            json.dump({}, f)
                        return
                for row in reader:
                    try:
                        category = row.get(column_map.get('category', 'category'))
                        status = row.get(column_map.get('status', 'status'))
                        amount_str = row.get(column_map.get('amount', 'amount'))
                        if not category or not status or not amount_str:
                            continue
                        if status.strip().lower() == "void":
                            continue
                        try:
                            amount = float(amount_str)
                        except (ValueError, TypeError):
                            continue
                        category_data[category]["total_amount"] += amount
                        category_data[category]["count"] += 1
                    except (KeyError, AttributeError):
                        continue
            else:
                for row in reader:
                    try:
                        category = row['category']
                        status = row['status']
                        amount_str = row['amount']
                        if not category or not status or not amount_str:
                            continue
                        if status.strip().lower() == "void":
                            continue
                        try:
                            amount = float(amount_str)
                        except (ValueError, TypeError):
                            continue
                        category_data[category]["total_amount"] += amount
                        category_data[category]["count"] += 1
                    except (KeyError, AttributeError):
                        continue

    except FileNotFoundError:
        print(f"Error: File '{input_path}' not found.")
        with open(output_path, 'w') as f:
            json.dump({}, f)
        return
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        with open(output_path, 'w') as f:
            json.dump({}, f)
        return

    result = {}
    for category, data in sorted(category_data.items()):
        if data["count"] > 0:
            result[category] = {
                "total_amount": round(data["total_amount"], 2),
                "count": data["count"]
            }

    try:
        with open(output_path, 'w') as jsonfile:
            json.dump(result, jsonfile, indent=2)
    except Exception as e:
        print(f"Error writing JSON file: {e}")

if __name__ == "__main__":
    process_transactions()

"""
Exact-match verification for the synthetic task pair -- deliberately
simple and robust, unlike edit-pdf's 9 fuzzy OCR-tolerant sub-checks.
Returns (passed: bool, message: str) so callers get a real, specific
diagnostic either way, not just True/False.
"""
import json
from pathlib import Path


def verify(output_path: Path, expected_path: Path) -> tuple[bool, str]:
    if not output_path.exists():
        return False, f"no output file produced at {output_path}"

    try:
        actual = json.loads(output_path.read_text())
    except json.JSONDecodeError as exc:
        return False, f"output.json is not valid JSON: {exc}"

    expected = json.loads(expected_path.read_text())

    if set(actual.keys()) != set(expected.keys()):
        missing = set(expected.keys()) - set(actual.keys())
        extra = set(actual.keys()) - set(expected.keys())
        return False, f"key mismatch -- missing: {sorted(missing)}, unexpected: {sorted(extra)}"

    # Keys must also be in the correct sorted order, per the instructions
    if list(actual.keys()) != sorted(actual.keys()):
        return False, f"keys are not sorted alphabetically: {list(actual.keys())}"

    errors = []
    for key in expected:
        exp_val = expected[key]
        act_val = actual.get(key, {})
        for field, exp_num in exp_val.items():
            act_num = act_val.get(field)
            if act_num is None:
                errors.append(f"{key}.{field}: missing")
                continue
            if isinstance(exp_num, int):
                if act_num != exp_num:
                    errors.append(f"{key}.{field}: expected {exp_num}, got {act_num}")
            else:
                if abs(float(act_num) - float(exp_num)) > 0.01:
                    errors.append(f"{key}.{field}: expected {exp_num}, got {act_num}")

    if errors:
        return False, "value mismatches:\n  " + "\n  ".join(errors)

    return True, "all values match expected output exactly"

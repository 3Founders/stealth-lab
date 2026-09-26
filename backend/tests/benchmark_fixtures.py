"""BigCodeBench-shaped rows for tests (same fields as the Hugging Face dataset)."""
from __future__ import annotations


def _tests(n: int) -> str:
    body = "\n".join(f"    def test_case_{i}(self):\n        self.assertTrue(True)\n" for i in range(1, n + 1))
    return f"import unittest\nclass TestCases(unittest.TestCase):\n{body}"


def row(i: int, statement: str, libs: str, n_tests: int = 5) -> dict:
    return {
        "task_id": f"BigCodeBench/{i}",
        "complete_prompt": f'def task_func():\n    """{statement}"""\n',
        "instruct_prompt": f"{statement}\nThe function should output with:\n    object: the result\n"
                           "You should write self-contained code starting with:\n```\ndef task_func():\n```",
        "canonical_solution": f"    return {i}\n",
        "code_prompt": "import pandas as pd\ndef task_func():\n",
        "test": _tests(n_tests),
        "entry_point": "task_func",
        "doc_struct": "{}",
        "libs": libs,
    }


ROWS = [
    row(0, "Calculate the average of the sums of absolute differences for permutations of a list.",
        "['random', 'itertools']"),
    row(1, "Load a CSV into a DataFrame and plot a histogram of each numeric column.",
        "['pandas', 'matplotlib', 'random']"),
    row(2, "Fetch a web page and extract every link from it.", "['requests', 'bs4', 're']"),
    row(3, "Compute the SHA256 hash of a file and return it as base64.", "['hashlib', 'base64', 'os']", n_tests=2),
    row(4, "Group sales records by month and return the monthly totals.", "['pandas', 'datetime']"),
    row(5, "Group sales records by month and return the monthly totals.", "['pandas', 'numpy']"),
]

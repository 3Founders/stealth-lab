"""BigCodeBench adapter (https://github.com/bigcode-project/bigcodebench, Apache-2.0).

Rows (Hugging Face `bigcode/bigcodebench`, one parquet file per version) carry:
task_id, complete_prompt, instruct_prompt, canonical_solution, code_prompt, test,
entry_point, doc_struct, libs. We use the INSTRUCT form (a natural-language task, as
an agent would receive it). Each task:

  * becomes a Goal named by the task statement's first sentence;
  * is placed under up to two DOMAIN Goals picked from its `libs`, most specific
    first (e.g. pandas before random) -- domains are things people want done
    ("Analyze tabular data with pandas and NumPy"), never benchmark families;
  * gets a frozen benchmark: its unittest suite, split into a visible ~1/3 (the
    runtime check) and the full suite (the gold grade). Tasks with fewer than 3 test
    methods cannot be split that way and are excluded, with the reason recorded.

The canonical solution is never stored -- only its sha256.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.benchmarks.tasks import BenchmarkTask, choose_visible

SOURCE = "bigcodebench"
LATEST_VERSION = "v0.1.4"
PARQUET_URL = "https://huggingface.co/datasets/bigcode/bigcodebench/resolve/main/data/{version}-00000-of-00001.parquet"
MIN_TESTS = 3

# (domain Goal name, libraries that place a task there) -- most specific first
DOMAINS: tuple[tuple[str, frozenset[str]], ...] = (
    ("Train and evaluate machine learning models in Python",
     frozenset({"sklearn", "tensorflow", "keras", "torch", "statsmodels", "gensim"})),
    ("Visualize data with Python plotting libraries", frozenset({"matplotlib", "seaborn", "plotly"})),
    ("Process images and audio in Python", frozenset({"PIL", "cv2", "skimage", "librosa", "soundfile", "wave"})),
    ("Make HTTP requests, scrape pages and serve web apps in Python",
     frozenset({"requests", "urllib", "bs4", "http", "mechanize", "socket", "ssl", "smtplib", "flask", "django",
                "flask_restful", "flask_mail", "flask_login", "flask_wtf", "ipaddress", "email", "cgi"})),
    ("Hash, encrypt and encode data in Python",
     frozenset({"hashlib", "cryptography", "base64", "hmac", "rsa", "secrets", "binascii", "Crypto", "zlib"})),
    ("Analyze tabular data with pandas and NumPy", frozenset({"pandas", "numpy"})),
    ("Compute statistics and scientific results in Python", frozenset({"scipy", "statistics", "sympy"})),
    ("Process and analyze text in Python",
     frozenset({"re", "nltk", "textblob", "wordcloud", "textwrap", "difflib", "unicodedata", "codecs", "regex",
                "wordninja", "Levenshtein", "string"})),
    ("Read and write data formats in Python",
     frozenset({"json", "csv", "xml", "yaml", "pickle", "openpyxl", "xlwt", "docx", "sqlite3", "configparser",
                "struct", "xmltodict", "pytesseract"})),
    ("Handle dates and times in Python", frozenset({"datetime", "time", "pytz", "dateutil", "calendar", "holidays"})),
    ("Work with files, processes and the operating system in Python",
     frozenset({"os", "shutil", "glob", "pathlib", "subprocess", "zipfile", "tarfile", "io", "tempfile", "psutil",
                "fnmatch", "signal", "platform", "sys", "threading", "multiprocessing", "queue", "logging",
                "getpass", "ctypes", "sqlite"})),
)
FALLBACK_DOMAIN = "Write core Python algorithms"
_TEST_RE = re.compile(r"^\s*def\s+(test_\w+)\s*\(\s*self", re.MULTILINE)
_SENTENCE_RE = re.compile(r"(.+?[.!?])(\s|$)", re.DOTALL)


def parse_libs(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        values = raw
    else:
        text = str(raw).strip()
        try:
            values = ast.literal_eval(text) if text.startswith("[") else [v for v in re.split(r"[,\s]+", text) if v]
        except (ValueError, SyntaxError):
            values = [v for v in re.split(r"[\[\]',\s]+", text) if v]
    return [str(v).strip() for v in values if str(v).strip()]


def domains_for(libs: Sequence[str], limit: int = 2) -> list[str]:
    roots = {lib.split(".")[0] for lib in libs}
    found = [name for name, members in DOMAINS if roots & members]
    return found[:limit] or [FALLBACK_DOMAIN]


def goal_name_from(statement: str, max_len: int = 140) -> str:
    """The task statement's first sentence, trimmed at a word boundary."""
    text = " ".join(statement.strip().split())
    match = _SENTENCE_RE.match(text)
    sentence = (match.group(1) if match else text).strip()
    if len(sentence) > max_len:
        cut = sentence[:max_len].rsplit(" ", 1)[0]
        sentence = cut.rstrip(",;:") + "…"
    return sentence


def test_names(test_code: str) -> list[str]:
    return list(dict.fromkeys(_TEST_RE.findall(test_code or "")))


def task_from_row(row: Mapping[str, Any], *, version: str = LATEST_VERSION) -> BenchmarkTask:
    external_id = str(row["task_id"])
    statement = str(row.get("instruct_prompt") or row.get("complete_prompt") or "")
    libs = parse_libs(row.get("libs"))
    tests = test_names(str(row.get("test") or ""))
    reference = row.get("canonical_solution")
    task = BenchmarkTask(
        source=SOURCE, source_version=version, external_id=external_id, goal_name=goal_name_from(statement),
        goal_description=statement, domains=domains_for(libs), test_code=str(row.get("test") or ""),
        test_names=tests, visible_tests=choose_visible(external_id, tests) if len(tests) >= MIN_TESTS else [],
        entry_point=str(row.get("entry_point") or "task_func"), libs=libs,
        prompt_prefix=str(row.get("code_prompt") or ""),
        reference_sha256=hashlib.sha256(str(reference).encode("utf-8")).hexdigest() if reference else None,
    )
    if len(tests) < MIN_TESTS:
        task.excluded_reason = (f"{len(tests)} test method(s): fewer than {MIN_TESTS}, so the tests cannot be split "
                                "into a runtime check and a gold grade")
    elif not statement:
        task.excluded_reason = "no task statement"
    return task


def tasks_from_rows(rows: Iterable[Mapping[str, Any]], *, version: str = LATEST_VERSION) -> list[BenchmarkTask]:
    """All rows -> tasks; Goal names that collide get the task id appended (Goal
    identity is by name, and two different tasks must not merge)."""
    tasks = [task_from_row(r, version=version) for r in rows]
    seen: dict[str, list[BenchmarkTask]] = {}
    for task in tasks:
        seen.setdefault(task.goal_name.casefold(), []).append(task)
    for group in seen.values():
        if len(group) > 1:
            for task in group:
                task.goal_name = f"{task.goal_name} [{task.external_id}]"
    return tasks


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        return pq.read_table(path).to_pylist()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def download(dest_dir: str | Path, *, version: str = LATEST_VERSION) -> Path:
    """Fetch the dataset's parquet file (about 2.4 MB) from Hugging Face."""
    import httpx

    dest = Path(dest_dir) / f"bigcodebench-{version}.parquet"
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", PARQUET_URL.format(version=version), follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    return dest


def domain_description(name: str) -> Optional[str]:
    return f"Practical Python tasks: {name[0].lower()}{name[1:]}."

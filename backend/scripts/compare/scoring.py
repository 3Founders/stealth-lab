"""Normalize an arm's output and score it with the task's own verifier (docs/goal_router.md P2-04).

    python -m scripts.compare.scoring --task web-001 --input out/procedure.json
    python -m scripts.compare.scoring --task db-002 --text "SELECT ..."

Every arm's output goes through the same normalize() and the same verifier, so a tool's JSON and a procedure's prose
are compared on the answer, not the wrapping. The raw output's hash is always kept.

Fail closed: a task without a verifier, an unknown kind, a rubric with no judge configured, or a verifier that crashes
is an "error" with score 0, never a pass. The rubric judge is blind: it sees the question, the rubric and the answer,
never the arm or the task id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from scripts.compare.contracts import Task

SQL_STEP_LIMIT = 2_000_000       # sqlite VM steps before an answer's query is stopped
TEST_TIMEOUT_S = 300
Judge = Callable[[str, str, str], Awaitable[float]]   # (question, rubric, answer) -> 0..1


@dataclass
class Score:
    status: str                  # "pass" | "fail" | "error"
    score: float                 # 0..1
    scorer: str                  # the verifier kind that decided
    raw_hash: str                # sha256 of the raw output, as received
    answer_hash: str             # sha256 of the normalized answer
    detail: str = ""


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _from_json(value: Any) -> Optional[str]:
    """The answer inside an arm's JSON: answer / text / result / output / content, one level of JSON-in-a-string."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in "{[":
            try:
                return _from_json(json.loads(s)) or s
            except ValueError:
                return s
        return s
    if isinstance(value, dict):
        for key in ("answer", "final", "text", "result", "output", "content"):
            if key in value and value[key] not in (None, ""):
                got = _from_json(value[key])
                if got:
                    return got
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        parts = [p for p in (_from_json(v) for v in value) if p]
        return "\n".join(parts)
    return None if value is None else str(value)


def normalize(raw: Any) -> str:
    """Any arm's output -> the answer text (whitespace collapsed per line, never empty-by-accident)."""
    text = _from_json(raw) if not isinstance(raw, str) else (_from_json(raw) or "")
    lines = [" ".join(line.split()) for line in str(text or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()


# -- verifiers: each -> (passed, score, detail) ------------------------------------------------------------------------

def _flags(v: dict) -> int:
    return re.IGNORECASE if "i" in str(v.get("flags", "i")) else 0


def v_exact(v: dict, answer: str) -> tuple[bool, float, str]:
    norm = lambda s: " ".join(str(s).lower().split()).strip(" .")
    ok = norm(answer) == norm(v["value"])
    return ok, float(ok), "" if ok else "differs"


def v_regex(v: dict, answer: str) -> tuple[bool, float, str]:
    m = re.search(v["pattern"], answer, _flags(v))
    return bool(m), float(bool(m)), f"matched {m.group(0)!r}" if m else "no match"


def v_contains_all(v: dict, answer: str) -> tuple[bool, float, str]:
    hay = answer.lower() if v.get("case_insensitive", True) else answer
    terms = [str(t) for t in v["terms"]]
    missing = [t for t in terms if (t.lower() if v.get("case_insensitive", True) else t) not in hay]
    got = (len(terms) - len(missing)) / max(1, len(terms))
    return not missing, got if not missing else 0.0, f"missing {missing}" if missing else ""


SQL_BLOCK = re.compile(r"```(?:sql|sqlite)?\s*(.+?)```", re.IGNORECASE | re.DOTALL)
SQL_START = re.compile(r"\b(WITH|SELECT)\b.*", re.IGNORECASE | re.DOTALL)


def extract_sql(answer: str) -> str:
    m = SQL_BLOCK.search(answer)
    sql = m.group(1) if m else (SQL_START.search(answer).group(0) if SQL_START.search(answer) else "")
    return sql.strip().rstrip(";").strip()


def _read_only(action: int, *_args: Any) -> int:
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    recursive = getattr(sqlite3, "SQLITE_RECURSIVE", None)
    if recursive is not None:
        allowed.add(recursive)
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def _cell(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, int) and not isinstance(x, bool):
        return float(x)
    return x


def v_sql_result(v: dict, answer: str) -> tuple[bool, float, str]:
    sql = extract_sql(answer)
    if not sql:
        return False, 0.0, "no SQL query in the answer"
    if ";" in sql:
        return False, 0.0, "more than one statement"
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(v["fixture_sql"])
        db.set_authorizer(_read_only)
        steps = {"n": 0}

        def guard() -> int:
            steps["n"] += 1
            return 1 if steps["n"] * 1000 > SQL_STEP_LIMIT else 0
        db.set_progress_handler(guard, 1000)
        got = [[_cell(c) for c in row] for row in db.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        return False, 0.0, f"query failed: {exc}"
    finally:
        db.close()
    expected = [[_cell(c) for c in row] for row in v["expected"]]
    if v.get("ordered"):
        ok = got == expected
    else:
        key = lambda rows: sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
        ok = key(got) == key(expected)
    return ok, float(ok), "" if ok else f"rows {got[:5]} != {expected[:5]}"


def v_json_schema(v: dict, answer: str) -> tuple[bool, float, str]:
    try:
        data = json.loads(answer)
    except ValueError:
        return False, 0.0, "answer is not JSON"
    try:
        import jsonschema
    except ImportError as exc:   # no validator, no verdict: fail closed
        raise RuntimeError("json_schema needs the jsonschema package") from exc
    try:
        jsonschema.validate(data, v["schema"])
    except jsonschema.ValidationError as exc:
        return False, 0.0, f"schema: {exc.message}"
    return True, 1.0, ""


def v_test_suite(v: dict, answer: str) -> tuple[bool, float, str]:
    """The task's own command (from the corpus, never from the answer) decides; exit 0 passes."""
    cmd = v["command"]
    argv = cmd if isinstance(cmd, list) else shlex.split(str(cmd))
    try:
        p = subprocess.run(argv, cwd=v.get("cwd") or None, capture_output=True, text=True, timeout=TEST_TIMEOUT_S,
                           input=answer)
    except subprocess.TimeoutExpired:
        return False, 0.0, f"tests did not finish in {TEST_TIMEOUT_S} s"
    tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
    return p.returncode == 0, float(p.returncode == 0), f"exit {p.returncode}: {tail[0][:200]}"


SYNC = {"exact": v_exact, "regex": v_regex, "contains_all": v_contains_all, "sql_result": v_sql_result,
        "json_schema": v_json_schema, "test_suite": v_test_suite}


async def score(task: Task, raw: Any, *, judge: Optional[Judge] = None) -> Score:
    raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    answer = normalize(raw)
    base = {"raw_hash": sha(raw_text), "answer_hash": sha(answer)}
    v = task.verifier if isinstance(task.verifier, dict) else {}
    kind = str(v.get("kind") or "")
    if not kind:
        return Score("error", 0.0, "none", detail="task has no verifier", **base)
    if not answer:
        return Score("fail", 0.0, kind, detail="empty answer", **base)
    try:
        if kind == "rubric":
            if judge is None:
                return Score("error", 0.0, kind, detail="rubric: no judge configured", **base)
            got = float(await judge(task.prompt, str(v["rubric"]), answer))   # blind: no arm, no task id
            got = max(0.0, min(1.0, got))
            threshold = float(v.get("pass_at", 0.5))
            return Score("pass" if got >= threshold else "fail", got, kind, **base)
        fn = SYNC.get(kind)
        if fn is None:
            return Score("error", 0.0, kind, detail=f"unknown verifier kind {kind!r}", **base)
        ok, value, detail = fn(v, answer)
        return Score("pass" if ok else "fail", value, kind, detail=detail, **base)
    except Exception as exc:  # noqa: BLE001 -- a broken verifier never passes
        return Score("error", 0.0, kind, detail=f"verifier crashed: {type(exc).__name__}: {exc}", **base)


def main() -> None:
    import asyncio

    from scripts.compare.corpus import load

    ap = argparse.ArgumentParser(description="Score one output against a task's verifier.")
    ap.add_argument("--task", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path, help="a file with the arm's output (JSON or text)")
    src.add_argument("--text")
    args = ap.parse_args()
    task = load(ids=[args.task])[0]
    if args.input:
        body = args.input.read_text(encoding="utf-8")
        try:
            raw: Any = json.loads(body)
        except ValueError:
            raw = body
    else:
        raw = args.text
    result = asyncio.run(score(task, raw))
    print(json.dumps({"task": task.id, **asdict(result)}, indent=2))
    sys.exit(0 if result.status != "error" else 1)


if __name__ == "__main__":
    main()

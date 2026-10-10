"""IFEval (instruction following) as A-vs-C tasks: write a response that follows every verifiable instruction.

The agent sees TASK.md (the prompt), response.md (empty) and test_check.py. Split (fixed before any run, the same
for both arms): the VISIBLE check is the first ceil(n/2) of the prompt's n instructions, in the dataset's order; the
GRADE is IFEval's strict prompt-level accuracy -- every instruction followed, on the response exactly as written.
The prompt itself states every instruction, as a real request would; the visible check only covers part of them.

Checker code: Google's instruction_following_eval (experiments/ifeval/data, Apache-2.0), loaded as the package it
imports itself as. nltk >= 3.9 no longer loads pickled tokenizers, so the sentence splitter is NLTK's punkt_tab.
"""
from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
import types
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "ifeval" / "data"
CHECK = "python -m pytest -q test_check.py"
HOW = ("Write your reply to the request in TASK.md into response.md (only the reply itself). Check it with "
       "`python -m pytest -q test_check.py`.")


@lru_cache(maxsize=1)
def _registry():
    pkg = types.ModuleType("instruction_following_eval")
    pkg.__path__ = [str(DATA)]
    sys.modules["instruction_following_eval"] = pkg
    for name in ("instructions_util", "instructions", "instructions_registry"):
        spec = importlib.util.spec_from_file_location(f"instruction_following_eval.{name}", DATA / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        setattr(pkg, name, mod)
        spec.loader.exec_module(mod)
        if name == "instructions_util":
            from nltk.tokenize import PunktTokenizer
            mod._get_sentence_tokenizer = lambda: PunktTokenizer()
    return sys.modules["instruction_following_eval.instructions_registry"].INSTRUCTION_DICT


@lru_cache(maxsize=1)
def rows() -> dict[str, dict]:
    out = {}
    for line in (DATA / "ifeval_input_data.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[str(r["key"])] = r
    return out


def visible_ids(r: dict) -> list[int]:
    n = len(r["instruction_id_list"])
    return list(range(math.ceil(n / 2)))


def follows(r: dict, response: str, which: list[int]) -> tuple[bool, list[str]]:
    """IFEval's strict check for the instructions at `which`; -> (all followed, the ones not followed)."""
    reg = _registry()
    missed = []
    for i in which:
        iid = r["instruction_id_list"][i]
        inst = reg[iid](iid)
        kwargs = {k: v for k, v in (r["kwargs"][i] or {}).items() if v is not None}
        inst.build_description(**kwargs)
        args = inst.get_instruction_args()
        if args and "prompt" in args:
            inst.build_description(prompt=r["prompt"])
        if not (response.strip() and inst.check_following(response)):
            missed.append(iid)
    return not missed, missed


def sample(n: int, seed: int) -> list[dict]:
    """n prompts, a seeded random sample of all of them (every prompt is checkable: no filtering on difficulty)."""
    keys = sorted(rows(), key=int)
    random.Random(seed).shuffle(keys)
    return [{"task_id": f"ifeval-{k}", "suite": "ifeval", "workspace": "ifeval", "ifeval_key": k} for k in keys[:n]]


def summary(task: dict, limit: int = 180) -> str:
    text = " ".join(rows()[task["ifeval_key"]]["prompt"].split())
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def prompt(task: dict) -> str:
    return f"{summary(task)} -- {HOW}"


def files(task: dict, checker: Path) -> dict[str, str]:
    r = rows()[task["ifeval_key"]]
    test = (f'import subprocess, sys\n\n\ndef test_check():\n'
            f'    r = subprocess.run([sys.executable, r"{checker}", "{task["ifeval_key"]}", "response.md"],\n'
            f'                       capture_output=True, text=True)\n'
            f'    print(r.stdout[-2000:])\n'
            f'    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]\n')
    return {"TASK.md": r["prompt"].strip() + "\n", "response.md": "", "test_check.py": test}


def solution_file(task: dict) -> str:
    return "response.md"


def grade(task: dict, solution: str) -> dict:
    r = rows()[task["ifeval_key"]]
    vis, _ = follows(r, solution, visible_ids(r))
    ok, missed = follows(r, solution, list(range(len(r["instruction_id_list"]))))
    return {"resolved": ok, "check_pass": vis, "error": None if ok else f"not followed: {', '.join(missed)}"}


if __name__ == "__main__":
    # the visible check: `python ifeval_task.py <key> <response file>` -> exit 0 when the visible instructions hold
    key, resp = sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8")
    r = rows()[key]
    ok, missed = follows(r, resp, visible_ids(r))
    print("CHECK PASS" if ok else f"CHECK FAIL: not followed: {', '.join(missed)}")
    sys.exit(0 if ok else 1)

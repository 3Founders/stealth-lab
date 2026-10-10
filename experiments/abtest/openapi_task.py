"""OpenAPI contract tasks (APIs-guru specs) as A-vs-C tasks: build a service that conforms to a real API contract.

The agent sees TASK.md, openapi.json (a real, small API's OpenAPI 3.0 contract from the APIs-guru directory) and
app.py (an empty FastAPI app), and implements every operation so responses conform: documented status codes,
content types and response schemas. Checks are property-based (Schemathesis, positive mode) against the app run with
uvicorn. Split (fixed before any run, the same for both arms): the VISIBLE check fuzzes every other operation (index
0, 2, ... in sorted (path, method) order) with 25 examples each and seed 1; the GRADE fuzzes ALL operations with 60
examples each and seed 2.

Specs: APIs-guru OpenAPI 3.0 APIs, in a seeded random order, kept when the contract has 2-5 JSON operations, only
path/query parameters and JSON bodies, local $refs only -- and when a generic reference server built from the spec
(responses generated from each schema) passes the grade while a stub fails it. That keeps solvable contracts; it
does not look at how any model does.
"""
from __future__ import annotations

import copy
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[0] / "openapi" / "data"
SPECS = DATA / "specs"
CHECK = "python -m pytest -q test_check.py"
METHODS = ("get", "post", "put", "patch", "delete")
CHECKS = "not_a_server_error,status_code_conformance,content_type_conformance,response_schema_conformance"
VISIBLE = {"n": 25, "seed": 1}
HIDDEN = {"n": 60, "seed": 2}
STUB = "from fastapi import FastAPI\n\napp = FastAPI()\n"


def operations(spec: dict) -> list[tuple[str, str]]:
    return sorted((p, m) for p, item in spec.get("paths", {}).items() for m in item if m in METHODS)


def subset(spec: dict, ops: list[tuple[str, str]]) -> dict:
    out = copy.deepcopy(spec)
    keep = set(ops)
    for p in list(out["paths"]):
        for m in list(out["paths"][p]):
            if m in METHODS and (p, m) not in keep:
                del out["paths"][p][m]
        if not any(m in METHODS for m in out["paths"][p]):
            del out["paths"][p]
    return out


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def fuzz(app_dir: Path, spec: dict, n: int, seed: int, timeout_s: int = 600) -> tuple[bool, str]:
    """Run the app in app_dir with uvicorn and fuzz it with Schemathesis against spec -> (passed, output tail)."""
    port = _port()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(port), "--log-level", "warning"],
                           cwd=app_dir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(600):           # up to 60 s for the app to listen
            if srv.poll() is not None:
                return False, f"the app did not start: {(srv.stderr.read() or '')[-800:]}"
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            return False, "the app did not listen within 60 s"
        with tempfile.TemporaryDirectory(prefix="oas_") as tmp:
            sp = Path(tmp, "spec.json")
            sp.write_text(json.dumps(spec), encoding="utf-8")
            try:
                r = subprocess.run([sys.executable, "-m", "schemathesis.cli", "run", str(sp), "--url",
                                    f"http://127.0.0.1:{port}", "-c", CHECKS, "-m", "positive", "-n", str(n),
                                    "--seed", str(seed), "--phases", "examples,coverage,fuzzing", "--no-color"],
                                   capture_output=True, text=True, timeout=timeout_s, encoding="utf-8", errors="replace",
                                   env={**env, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
            except subprocess.TimeoutExpired:
                return False, "the check timed out"
        return r.returncode == 0, (r.stdout + r.stderr)[-2500:]
    finally:
        srv.kill()
        try:
            srv.wait(timeout=30)       # Windows keeps the app's folder open until the process is gone
        except subprocess.TimeoutExpired:
            pass


@lru_cache(maxsize=1)
def index() -> list[str]:
    return sorted(p.stem for p in SPECS.glob("*.json"))


def spec_of(name: str) -> dict:
    return json.loads((SPECS / f"{name}.json").read_text(encoding="utf-8"))


def sample(n: int, seed: int) -> list[dict]:
    names = index()           # already the verified, seeded selection (openapi_select.py)
    rnd = random.Random(seed)
    names = sorted(names)
    rnd.shuffle(names)
    return [{"task_id": f"openapi-{x}", "suite": "openapi", "workspace": f"openapi-{x}", "spec": x} for x in names[:n]]


def prompt(task: dict) -> str:
    title = spec_of(task["spec"]).get("info", {}).get("title", task["spec"])
    return (f"Implement the {title} API in app.py (a FastAPI app named app) so it conforms to the OpenAPI contract in "
            "openapi.json -- every operation, with the documented status codes, content types and response schemas "
            "(details in TASK.md). Check it with `python -m pytest -q test_check.py`.")


def files(task: dict, checker: Path) -> dict[str, str]:
    spec = spec_of(task["spec"])
    ops = operations(spec)
    md = ("# Task\n\nImplement the API described by `openapi.json` in `app.py`: a FastAPI application named `app`, "
          "run as `uvicorn app:app`. Every operation must answer valid requests with a documented status code, the "
          "documented content type and a body that matches the documented response schema.\n\nOperations:\n\n" +
          "\n".join(f"- {m.upper()} {p}" for p, m in ops) +
          "\n\nThe check fuzzes the app with property-based requests generated from the contract (Schemathesis).\n")
    test = (f'import subprocess, sys\n\n\ndef test_check():\n'
            f'    r = subprocess.run([sys.executable, r"{checker}", "{task["spec"]}", "."],\n'
            f'                       capture_output=True, text=True, encoding="utf-8", errors="replace")\n'
            f'    print(r.stdout[-3000:])\n'
            f'    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]\n')
    return {"TASK.md": md, "openapi.json": json.dumps(spec, indent=2) + "\n", "app.py": STUB, "test_check.py": test}


def solution_file(task: dict) -> str:
    return "app.py"


def check(name: str, app_dir: Path, full: bool) -> tuple[bool, str]:
    spec = spec_of(name)
    ops = operations(spec)
    if full:
        return fuzz(app_dir, spec, **HIDDEN)
    return fuzz(app_dir, subset(spec, ops[0::2]), **VISIBLE)


def grade(task: dict, solution: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="oas_app_", ignore_cleanup_errors=True) as tmp:
        Path(tmp, "app.py").write_text(solution, encoding="utf-8")
        # the workspace's contract too: an app may load it at runtime, as it can in the workspace
        Path(tmp, "openapi.json").write_text(json.dumps(spec_of(task["spec"]), indent=2) + "\n", encoding="utf-8")
        vis, _ = check(task["spec"], Path(tmp), full=False)
        ok, out = check(task["spec"], Path(tmp), full=True)
    return {"resolved": ok, "check_pass": vis, "error": None if ok else out[-300:]}


if __name__ == "__main__":
    # the visible check: `python openapi_task.py <spec name> <app dir>` -> exit 0 when the visible operations conform
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")    # Schemathesis prints emoji; Windows' cp1252 can't
    ok, out = check(sys.argv[1], Path(sys.argv[2]).resolve(), full=False)
    print(out)
    print("CHECK PASS" if ok else "CHECK FAIL")
    sys.exit(0 if ok else 1)

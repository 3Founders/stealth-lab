"""Grade patches with the task's REAL tests: SWE-rebench-V2's prebuilt image, test command and log parser.

    .venv/Scripts/python grade_tests.py --gold --part test      # FIRST: gold patches must resolve (validates tasks)
    .venv/Scripts/python grade_tests.py --empty --part test     # and an empty patch must NOT resolve
    .venv/Scripts/python valid_tasks.py                         # -> runs/valid_tasks.json (scored set)
    .venv/Scripts/python grade_tests.py --tag test_L1           # grades runs/predictions_test_L1.jsonl

SWE-rebench-V2 publishes images and log parsers but no runner, so this is the runner, kept minimal and identical
to SWE-bench's resolution rule:
  1. start the task's image (repo at /<name>, at base_commit, dependencies installed);
  2. apply the candidate patch (git apply, then `patch --fuzz` as SWE-bench does); failure = "patch_failed";
  3. reset the files the test patch touches, apply the test patch;
  4. run install_config.test_cmd; parse the log with V2's own parser (install_config.log_parser);
  5. resolved = every FAIL_TO_PASS and every PASS_TO_PASS test PASSED.
Runners: "modal" (modal.Sandbox from the registry image) or "docker" (local `docker run`). An infrastructure
error is "error": retried, never scored as a failure (PREREGISTRATION.md section 6).

Output: runs/tests_<tag>.json {instance_id: {resolved, status, f2p: [passed, total], p2p: [passed, total], seconds}}
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import CONFIG, DATA, RUNS, load_jsonl, read_json, write_json

PARSER_DIR = DATA / "v2lib"
_lock = threading.Lock()


# ---------------------------------------------------------------- V2 log parsers (pinned)
def fetch_parsers() -> str:
    """Download V2's log_parsers.py + swe_constants.py at the pinned commit; return log_parsers.py's sha256."""
    cfg = CONFIG["grading"]["log_parsers"]
    pkg = PARSER_DIR / "lib" / "agent"
    pkg.mkdir(parents=True, exist_ok=True)
    for init in (PARSER_DIR / "lib" / "__init__.py", pkg / "__init__.py"):
        init.touch()
    for name, url in (("log_parsers.py", cfg["url"]),
                      ("swe_constants.py", cfg["url"].replace("log_parsers.py", "swe_constants.py"))):
        dest = pkg / name
        if not dest.exists():
            with urllib.request.urlopen(url, timeout=60) as r:
                dest.write_bytes(r.read())
    sha = hashlib.sha256((pkg / "log_parsers.py").read_bytes()).hexdigest()
    if cfg.get("sha256") and cfg["sha256"] != sha:
        raise SystemExit(f"log_parsers.py sha256 {sha} != pinned {cfg['sha256']}")
    return sha


def parser_for(install_config: dict, repo: str):
    if str(PARSER_DIR) not in sys.path:
        sys.path.insert(0, str(PARSER_DIR))
    mod = importlib.import_module("lib.agent.log_parsers")
    name = (install_config or {}).get("log_parser")
    if name and name in mod.NAME_TO_PARSER:
        return mod.NAME_TO_PARSER[name]
    if name and hasattr(mod, name):
        return getattr(mod, name)
    return mod.MAP_REPO_TO_PARSER[repo]


# ---------------------------------------------------------------- the in-container script
def _b64(s: str) -> str:
    return base64.b64encode((s or "").encode()).decode()


def test_files(test_patch: str) -> list[str]:
    out = []
    for line in (test_patch or "").splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4 and parts[3].startswith("b/"):
                out.append(parts[3][2:])
    return out


def script(src: dict, model_patch: str) -> str:
    """bash run inside the image. Markers delimit the phases so the log can be split reliably."""
    name = src["repo"].split("/")[1]
    cfg = src.get("install_config") or {}
    files = " ".join(f"'{f}'" for f in test_files(src["test_patch"]))
    return f"""set -u
cd /{name} || cd /testbed || exit 97
git config --global --add safe.directory '*' >/dev/null 2>&1
echo '{_b64(model_patch)}' | base64 -d > /tmp/model.patch
echo '{_b64(src["test_patch"])}' | base64 -d > /tmp/test.patch
if [ -s /tmp/model.patch ]; then
  git apply -v /tmp/model.patch >/tmp/apply.log 2>&1 || git apply -v --reject /tmp/model.patch >>/tmp/apply.log 2>&1 \\
    || patch --batch --fuzz=5 -p1 -i /tmp/model.patch >>/tmp/apply.log 2>&1 \\
    || {{ echo '>>>>> PATCH_FAILED'; cat /tmp/apply.log | tail -20; exit 98; }}
fi
echo '>>>>> PATCH_APPLIED'
{f"git checkout {src['base_commit']} -- {files} >/dev/null 2>&1 || true" if files else ""}
git apply -v /tmp/test.patch >/dev/null 2>&1 || {{ echo '>>>>> TEST_PATCH_FAILED'; exit 96; }}
echo '>>>>> START_TESTS'
{cfg.get("test_cmd") or "echo no test_cmd; exit 95"}
echo '>>>>> END_TESTS'
"""


def evaluate(log: str, src: dict) -> dict:
    """Pure: the log of one run -> the verdict (SWE-bench rule: all F2P and all P2P must pass)."""
    if ">>>>> PATCH_FAILED" in log:
        return {"resolved": False, "status": "patch_failed"}
    if ">>>>> TEST_PATCH_FAILED" in log or ">>>>> START_TESTS" not in log:
        return {"resolved": False, "status": "error", "detail": log[-400:]}
    body = log.split(">>>>> START_TESTS", 1)[1].split(">>>>> END_TESTS", 1)[0]
    status = parser_for(src.get("install_config") or {}, src["repo"])(body)
    passed = {k for k, v in status.items() if str(getattr(v, "value", v)) == "PASSED"}
    f2p, p2p = src["FAIL_TO_PASS"], src["PASS_TO_PASS"]
    f_ok, p_ok = sum(t in passed for t in f2p), sum(t in passed for t in p2p)
    return {"resolved": f_ok == len(f2p) and p_ok == len(p2p), "status": "graded",
            "f2p": [f_ok, len(f2p)], "p2p": [p_ok, len(p2p)], "parsed_tests": len(status)}


# ---------------------------------------------------------------- runners
def run_docker(image: str, sh: str, timeout: int) -> str:
    r = subprocess.run(["docker", "run", "--rm", "-i", "--platform", "linux/amd64", "--entrypoint", "bash", image,
                        "-s"], input=sh, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return r.stdout + "\n" + r.stderr


_modal_app = None


def run_modal(image: str, sh: str, timeout: int) -> str:
    import modal

    global _modal_app
    with _lock:
        if _modal_app is None:
            _modal_app = modal.App.lookup("kel-local-eval-grader", create_if_missing=True)
    img = modal.Image.from_registry(image if "/" in image.split(":")[0] else f"docker.io/{image}")
    sb = modal.Sandbox.create(image=img, app=_modal_app, timeout=timeout, cpu=2, memory=4096)
    try:
        p = sb.exec("bash", "-c", sh, timeout=timeout)
        p.wait()
        return p.stdout.read() + "\n" + p.stderr.read()
    finally:
        sb.terminate()


RUNNERS = {"docker": run_docker, "modal": run_modal}


def grade_one(src: dict, patch: str, runner: str, timeout: int, run_empty: bool = False) -> dict:
    if not (patch or "").strip() and not run_empty:
        return {"resolved": False, "status": "empty_patch"}
    started = time.time()
    try:
        log = RUNNERS[runner](src["image_name"], script(src, patch), timeout)
        out = evaluate(log, src)
    except Exception as exc:  # noqa: BLE001 -- infrastructure: retried, never scored
        out = {"resolved": False, "status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
    out["seconds"] = round(time.time() - started, 1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="grades runs/predictions_<tag>.jsonl")
    ap.add_argument("--gold", action="store_true", help="grade the gold patches (task validation)")
    ap.add_argument("--empty", action="store_true",
                    help="run the tests with NO patch (task validation: an empty patch must not resolve)")
    ap.add_argument("--part", default="test")
    ap.add_argument("--workers", type=int, default=CONFIG["grading"]["max_workers"])
    ap.add_argument("--runner", default=CONFIG["grading"]["runner"], choices=sorted(RUNNERS))
    ap.add_argument("--retry-errors", action="store_true")
    a = ap.parse_args()
    sha = fetch_parsers()
    src = read_json(RUNS / "grading_source.json")
    if a.gold:
        tag = f"gold_{a.part}"
        preds = {i: src[i]["patch"] for i in read_json(RUNS / "design.json")[a.part]}
    elif a.empty:
        tag = f"empty_{a.part}"
        preds = {i: "" for i in read_json(RUNS / "design.json")[a.part]}
    elif a.tag:
        tag = a.tag
        preds = {r["instance_id"]: r.get("model_patch") or "" for r in load_jsonl(RUNS / f"predictions_{tag}.jsonl")}
    else:
        raise SystemExit("--tag, --gold or --empty")
    out_path = RUNS / f"tests_{tag}.json"
    done = read_json(out_path) if out_path.exists() else {}
    todo = [i for i in preds if i not in done or (a.retry_errors and done[i]["status"] == "error")]
    print(f"{tag}: {len(todo)} to grade ({len(done)} done), runner={a.runner}, parsers sha256={sha[:12]}", flush=True)

    def work(i: str) -> None:
        res = grade_one(src[i], preds[i], a.runner, CONFIG["grading"]["timeout_s"], run_empty=a.empty)
        with _lock:
            done[i] = res
            write_json(out_path, done)
        print(f"{i:<50} {res['status']:<13} resolved={res['resolved']} {res.get('f2p', '')}", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, todo))
    graded = [v for v in done.values() if v["status"] != "error"]
    print(json.dumps({"graded": len(graded), "errors": len(done) - len(graded),
                      "resolved": sum(v["resolved"] for v in graded)}, indent=1))


if __name__ == "__main__":
    main()

"""Measure a model on kell's coding probe, so routing ranks it from a measurement instead of a guess (app/routing/probe.py).

    modal run scripts/routing_probe_modal.py --model glm-5.3-flash
    modal run scripts/routing_probe_modal.py --model new-model --exec ~/.stealthlab/exec.json --limit 20

The model answers the probe's coding problems (app/routing/data/probe_lcb.json: LiveCodeBench problems LLMRouterBench
graded for dozens of anchor models) with the exact prompts LLMRouterBench used. Each answer is graded on Modal with
LiveCodeBench's own grader (scripts/lcb_grader, Linux only), so model-written code never runs on this machine. The
result is written to app/routing/data/probe_results.json; routing reads it at the next start.

The model is described by an exec.json profile (model_id, openai_base_url, key_env, prices, sampling), the same one
kell dispatches with. Its key is read inside the container from the Modal secret `--secret` (default stealthlab-keys),
never passed on the command line. Cost: 100 problems, about a dollar or less on a cheap model.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "app" / "routing" / "data"
PROBE = DATA / "probe_lcb.json"
RESULTS = DATA / "probe_results.json"
DEFAULT_EXEC = HERE.parent.parent / "experiments" / "abtest" / "exec.json"

image = (modal.Image.debian_slim(python_version="3.12").pip_install("numpy==2.1.3", "httpx==0.27.2")
         .add_local_dir(str(HERE / "lcb_grader"), "/root/lcb_grader"))
app = modal.App("kell-routing-probe")
SECRET = os.environ.get("KELL_PROBE_SECRET", "stealthlab-keys")
MAX_TOKENS = int(os.environ.get("KELL_PROBE_MAX_TOKENS", "32000"))


def extract_code(text: str) -> str:
    """LLMRouterBench's extraction: the last [PYTHON] ... [/PYTHON] or ``` ... ``` block."""
    lines = (text or "").split("\n")
    marks = [i for i, line in enumerate(lines) if "PYTHON]" in line]
    if len(marks) < 2:
        marks = [i for i, line in enumerate(lines) if "```" in line]
    return "\n".join(lines[marks[-2] + 1: marks[-1]]).strip() if len(marks) >= 2 else ""


@app.function(image=image, secrets=[modal.Secret.from_name(SECRET)], timeout=1800, cpu=1, memory=2048,
              max_containers=8)
def solve_and_grade(item: dict, profile: dict) -> dict:
    import multiprocessing
    import sys
    import time

    import httpx

    sys.path.insert(0, "/root")
    key = os.environ.get(profile.get("key_env") or "", "")
    body = {"model": profile["model_id"], "messages": [{"role": "user", "content": item["prompt"]}],
            "max_tokens": MAX_TOKENS, **{k: profile[k] for k in ("temperature", "top_p") if k in profile},
            **(profile.get("request_extras") or {})}
    text, usage, error, t0 = "", {}, None, time.time()
    for attempt in range(4):
        try:
            r = httpx.post(profile["openai_base_url"].rstrip("/") + "/chat/completions", json=body, timeout=900,
                           headers={"Authorization": f"Bearer {key}"} if key else {})
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            text, usage = data["choices"][0]["message"].get("content") or "", data.get("usage") or {}
            error = None
            break
        except Exception as exc:  # noqa: BLE001 -- recorded as a failed item, the probe goes on
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            time.sleep(5)
    code = extract_code(text)
    passed = False
    if code:
        from lcb_grader.testing_util import run_test

        sample = {"input_output": json.dumps({**item["tests"], "fn_name": item.get("fn_name")})}
        manager = multiprocessing.Manager()
        out = manager.list()

        def run(o):
            o.append(run_test(sample, test=code, debug=False, timeout=6)[0])

        p = multiprocessing.Process(target=run, args=(out,))
        p.start()
        p.join(timeout=7 * len(item["tests"]["inputs"]) + 5)
        if p.is_alive():
            p.kill()
        res = list(out[0]) if out else []
        passed = bool(res) and all(x is True for x in res)
    return {"id": item["id"], "passed": passed, "seconds": round(time.time() - t0, 1), "error": error,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0)}


@app.local_entrypoint()
def main(model: str, exec: str = str(DEFAULT_EXEC), limit: int = 0):
    cfg = json.loads(Path(os.path.expanduser(exec)).read_text(encoding="utf-8"))
    profile = (cfg.get("profiles") or {}).get(model)
    if not profile:
        raise SystemExit(f"no profile {model!r} in {exec}")
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    items = probe["items"][:limit] if limit else probe["items"]
    rows = list(solve_and_grade.map(items, kwargs={"profile": profile}, order_outputs=False))
    k = sum(r["passed"] for r in rows)
    tin, tout = sum(r["prompt_tokens"] for r in rows), sum(r["completion_tokens"] for r in rows)
    cost = tin / 1e6 * float(profile.get("input_per_mtok") or 0) + tout / 1e6 * float(profile.get("output_per_mtok") or 0)
    errors = [r for r in rows if r["error"]]
    result = {"probe": probe["probe"], "n": len(rows), "k": k, "model_id": profile["model_id"],
              "base_url": profile.get("openai_base_url"), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "cost_usd": round(cost, 4), "errors": len(errors), "items": {r["id"]: int(r["passed"]) for r in rows}}
    print(json.dumps({kk: v for kk, v in result.items() if kk != "items"}, indent=1))
    if errors:
        print(f"{len(errors)} items had API errors, e.g. {errors[0]['error']}")
    if limit:
        print("--limit: a trial run, not written to probe_results.json")
        return
    if len(errors) > len(rows) // 10:
        print("more than 10% API errors: not written (rerun when the provider is up)")
        return
    have = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    have[model] = result
    RESULTS.write_text(json.dumps(have, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"written: {RESULTS}")

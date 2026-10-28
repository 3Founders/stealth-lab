"""Replay a cheap model on real issues it failed, with and without a change to the cheap path, graded on the hidden tests.

Each issue runs in its own container: the workspace at the issue's base commit with the project's own history (the series'
prepare_task), the issue's venv, then kell's step agent (packaging/npm/lib/agent/step_agent.mjs) on the SAME task text the
series sent it (taken from the run's saved run.json), once per variant, the workspace reset between variants. Graded with the
series' hidden-test grader (swe_series_task.grade_workspace). Every tool call is kept (the agent's transcript).

Variants (combinable, comma-separated in a name):
    base      the task as sent, the agent as it ships
    hist      + this repository's earlier changes to the located functions (lib/area_history.mjs) after the task, and the
              agent's `history` tool
    method    + the root-cause method and the no-change-no-finish guard (lib/agent/step_agent.mjs, method=root_cause)
    loc       the "Likely place" line made again by lib/locate.mjs as it is now, not the series' saved line
    cc        driven by lean Claude Code through kell's bridge (replay_cc.mjs) instead of the step agent, as the product
              runs the open models when Claude Code is installed (cc+hist, cc+method, cc+hist+method combine as above)

    modal run experiments/abtest/replay_cheap.py --tasks tasks.json --variants base,hist,method,hist+method --run replay-1

tasks.json: [{"instance_id": "...", "series": "jsonargparse", "task": "<the task text the series sent>"}, ...]
The model is glm-5.3-flash on z.ai (Z_AI_KEY from the stealthlab-keys secret), as in the series' exec.json.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

try:
    import modal
except ImportError:                          # the module also loads where modal is not installed (tests, a dry read)
    modal = None

HERE = Path(__file__).resolve().parent
APP_DIR = "/app"
VOL = "/mnt/series"
NPM = Path(APP_DIR) / "packaging" / "npm"
# exec.json and replay_cc.mjs: beside this file locally, under the app in the container (modal runs this file from /root)
ABTEST = HERE if (HERE / "exec.json").exists() else Path(APP_DIR) / "experiments" / "abtest"
# every model and driver gets the same 20 minutes, enough for every cheap solve seen so far (pipeline survey: 46-594 s, then
# 1053 and 1201): the replay measures what the models can do; the product's default (dispatch timeoutS) is set from it
AGENT_TIMEOUT_S = 1200
CC_TIMEOUT_S = AGENT_TIMEOUT_S
GLM = {"base_url": "https://api.z.ai/api/paas/v4", "model_id": "glm-5.3-flash", "input_per_mtok": 0.15, "output_per_mtok": 0.5, "cached_input_per_mtok": 0.03,
       "request_extras": {"thinking": {"type": "enabled", "clear_thinking": False}}}
# the agents' shells get no package index and no way out but loopback (the bridge, the provider calls of the step agent's own
# process are not proxied): GLM in Claude Code downloaded later jsonargparse releases and copied their fixes on 11 of 15 tries
# (replay-2g, 2026-10-28)
NO_NET = {"PIP_NO_INDEX": "1", "PIP_INDEX_URL": "http://127.0.0.1:9/simple", "UV_OFFLINE": "1", "UV_INDEX_URL": "http://127.0.0.1:9/simple",
          "PIP_DISABLE_PIP_VERSION_CHECK": "1", "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
          "https_proxy": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
WHERE = re.compile(r"([\w./-]+\.\w+):(\d+)-(\d+)")


def spots_of(task: str) -> list[dict]:
    """The located lines the task text names ("Likely place in this repository ...: a.py:10-20, ...")."""
    line = next((l for l in task.splitlines() if l.startswith("Likely place")), "")
    return [{"file": f, "start": int(a), "end": int(b)} for f, a, b in WHERE.findall(line)]


def area_history(root: Path, spots: list[dict]) -> str:
    js = ("import(process.argv[1]).then(m => process.stdout.write(m.areaHistory(process.argv[2], JSON.parse(process.argv[3]))))")
    r = subprocess.run(["node", "-e", js, (NPM / "lib" / "area_history.mjs").as_uri(), str(root), json.dumps(spots)],
                       capture_output=True, text=True, timeout=120)
    return r.stdout


# a fetch that got nothing: the replay's agents have no network, so pip says so and the command printed no download
BLOCKED = re.compile(r"No matching distribution|Could not find a version|Could not resolve|Failed to establish|Connection refused|"
                     r"ProxyError|Network is unreachable|Temporary failure in name resolution|Could not fetch")


def fetches(transcript: Path) -> list[str]:
    """The shell commands of a try that fetched from the network and got something (runner.FETCHES): such a try is not
    counted as solved. An attempt the network block stopped is no help to the agent and does not count (GLM tried
    `pip download jsonargparse` on most tries and solved 518 and 527 anyway, replay-5g)."""
    import runner                                 # noqa: E402
    pending, out = {}, []
    for line in (transcript.read_text(encoding="utf-8").splitlines() if transcript.exists() else []):
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if j.get("type") == "assistant":         # Claude Code: the call, then its result in a later user message
            for c in j.get("message", {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and (m := runner.FETCHES.search(str(c.get("input", {}).get("command", "")))):
                    pending[c.get("id")] = m.group(0).strip()
        elif j.get("type") == "user":
            for c in j.get("message", {}).get("content") or []:
                if isinstance(c, dict) and c.get("tool_use_id") in pending and not BLOCKED.search(json.dumps(c.get("content"))):
                    out.append(pending[c["tool_use_id"]])
        elif (m := runner.FETCHES.search(str((j.get("args") or {}).get("command", "")))) and not BLOCKED.search(str(j.get("result", ""))):
            out.append(m.group(0).strip())        # the step agent: call and result on one line
    return sorted(set(out))


def relocate(root: Path, task: str) -> str:
    """The task with its "Likely place" line made again by lib/locate.mjs as it is now (the saved task has the series' line)."""
    head, sep, _ = task.partition("Likely place")
    js = ("import(process.argv[1]).then(m => { const r = m.locate({ prompt: process.argv[3], root: process.argv[2] });"
          " process.stdout.write(m.renderWhere(r)) })")
    r = subprocess.run(["node", "-e", js, (NPM / "lib" / "locate.mjs").as_uri(), str(root), head], capture_output=True, text=True, timeout=300)
    if not sep or not r.stdout.strip():
        return task
    rest = task[len(head):].split("\n", 1)
    return head + r.stdout.strip() + ("\n" + rest[1] if len(rest) > 1 else "")


def profile(model: str) -> tuple[dict, str]:
    """(the agent's model settings, the key's env name) from the series' exec.json profile of `model`."""
    if model == "glm-5.3-flash":
        return GLM, "Z_AI_KEY"
    p = json.loads((ABTEST / "exec.json").read_text(encoding="utf-8"))["profiles"][model]
    cfg = {"base_url": p["openai_base_url"], "model_id": p["model_id"], "input_per_mtok": p.get("input_per_mtok"),
           "output_per_mtok": p.get("output_per_mtok"), **({"request_extras": p["request_extras"]} if p.get("request_extras") else {}),
           **{k: p[k] for k in ("temperature", "top_p") if k in p},
           **({"cached_input_per_mtok": p["cached_input_per_mtok"]} if "cached_input_per_mtok" in p else {}),
           **({"no_system_role": True} if p.get("no_system_role") else {})}
    return cfg, p["key_env"]


def run_cc(ws: Path, task: str, variant: str, py: Path, key: str, transcript: Path, model: str) -> dict:
    """The same try driven by lean Claude Code through kell's bridge (replay_cc.mjs), as the product runs it."""
    profiles = json.loads((ABTEST / "exec.json").read_text(encoding="utf-8"))["profiles"]
    if model == "glm-5.3-flash" and model not in profiles:
        profiles[model] = {"openai_base_url": GLM["base_url"], "model_id": GLM["model_id"], "key_env": "Z_AI_KEY",
                           "request_extras": GLM["request_extras"], "cached_input_per_mtok": 0.03}
    key_env = profiles[model]["key_env"]
    env = {**{k: v for k, v in os.environ.items() if not k.endswith("_KEY") and not k.startswith("STEALTHLAB_")},
           key_env: key, "PATH": f"{py.parent}{os.pathsep}{os.environ.get('PATH', '')}", "VIRTUAL_ENV": str(py.parent.parent), **NO_NET,
           **({"STEALTHLAB_AGENT_METHOD": "root_cause"} if "method" in variant else {})}
    t0 = time.time()
    r = subprocess.run(["node", str(ABTEST / "replay_cc.mjs")], input=json.dumps({"task": task, "profile_name": model, "profiles": profiles,
                       "timeout_s": CC_TIMEOUT_S, "transcript": str(transcript)}), cwd=ws, env=env, capture_output=True, text=True,
                       timeout=CC_TIMEOUT_S + 120)
    final = next((json.loads(l) for l in r.stdout.splitlines()[::-1] if l.startswith("{")), {})
    return {"seconds": round(time.time() - t0, 1), "stop": final.get("stop") or f"rc {r.returncode}: {r.stderr[-200:]}",
            "steps": final.get("steps"), "cost_usd": final.get("costUsd"), "message": str(final.get("message") or "")[:300]}


def run_agent(ws: Path, task: str, variant: str, py: Path, key: str, transcript: Path, model_cfg: dict = GLM) -> dict:
    # no early-stop rules: only the time limit ends a try, so every model gets its full chance; the rules are judged afterwards on
    # the transcripts (would one have stopped a try that went on to solve the issue?)
    cfg = {**model_cfg, "task": task, "transcript": str(transcript), "early_stop": False, "max_seconds": AGENT_TIMEOUT_S - 30,
           **({"history_tool": True} if "hist" in variant else {}),
           **({"method": "root_cause"} if "method" in variant else {})}
    env = {**{k: v for k, v in os.environ.items() if not k.endswith("_KEY") and not k.startswith("STEALTHLAB_")},
           "OPEN_MODEL_API_KEY": key, "PATH": f"{py.parent}{os.pathsep}{os.environ.get('PATH', '')}", "VIRTUAL_ENV": str(py.parent.parent), **NO_NET}
    t0 = time.time()
    try:
        r = subprocess.run(["node", str(NPM / "lib" / "agent" / "step_agent.mjs")], input=json.dumps(cfg), cwd=ws, env=env,
                           capture_output=True, text=True, timeout=AGENT_TIMEOUT_S + 120)
        out = r.stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        out += '\n{"type":"final","message":"timed out","stop":"timed_out"}'
    final = {}
    for line in out.splitlines():
        if line.startswith("{") and '"type":"final"' in line.replace(" ", ""):
            try:
                final = json.loads(line)
            except ValueError:
                pass
    return {"seconds": round(time.time() - t0, 1), "stop": final.get("stop"), "steps": final.get("steps"),
            "cost_usd": final.get("costUsd"), "message": str(final.get("message") or "")[:300]}


def replay_one(item: dict, variants: list[str], run: str) -> list[dict]:
    import runner                                 # noqa: E402  (inside the container: PYTHONPATH has experiments/abtest)
    import swe_series_task as swe                 # noqa: E402
    os.environ.setdefault("HF_HOME", f"{VOL}/cache/hf")
    if not (swe.DATA / f"{item['series']}.json").exists():
        swe.fetch(item["series"])
    # the PASS_TO_PASS tests this environment breaks, as the series' validate step found them (graded the same way as the series)
    found = Path(VOL) / item.get("validate_from", "cache-full-2") / item["series"] / "validate.json"
    if found.exists():
        swe.LOCAL.mkdir(parents=True, exist_ok=True)
        (swe.LOCAL / f"validate_{item['series']}.json").write_text(found.read_text(encoding="utf-8"), encoding="utf-8")
    task = next(t for t in swe.series(item["series"]) if t["instance_id"] == item["instance_id"])
    ws = runner.workspace("A", task["workspace"])
    swe.setup_project(task, ws)
    swe.prepare_task(task, ws)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", f"task {task['instance_id']}", "--allow-empty"], check=True)
    py = swe.arm_python(task, ws)
    out_dir = Path(VOL) / run / item["instance_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    relocated = relocate(ws, item["task"]) if any("loc" in v for v in variants) else item["task"]
    print(f"{item['instance_id']}: located now: {next((l for l in relocated.splitlines() if l.startswith('Likely place')), '-')[:300]}", flush=True)
    results = []
    for v in variants:
        subprocess.run(["git", "-C", str(ws), "reset", "-q", "--hard"], check=True)
        subprocess.run(["git", "-C", str(ws), "clean", "-qfd"], check=True)
        base_task = relocated if "loc" in v else item["task"]
        hist = area_history(ws, spots_of(base_task)) if "hist" in v else ""
        text = base_task + (f"\n\n{hist}" if hist else "")
        transcript = out_dir / f"{v}.transcript.jsonl"
        transcript.unlink(missing_ok=True)
        model_cfg, key_env = profile(item.get("model", "glm-5.3-flash"))
        key = os.environ.get(item.get("key_env") or key_env) or os.environ[key_env]
        rec = (run_cc(ws, text, v, py, key, transcript, item.get("model", "glm-5.3-flash")) if "cc" in v
               else run_agent(ws, text, v, py, key, transcript, model_cfg))
        rec["model"] = item.get("model", "glm-5.3-flash")
        diff = subprocess.run(["git", "-C", str(ws), "diff", "HEAD"], capture_output=True, text=True).stdout
        (out_dir / f"{v}.patch").write_text(diff, encoding="utf-8")
        g = swe.grade_workspace(task, ws)
        rec["fetched"] = fetches(transcript)
        rec.update({"instance_id": item["instance_id"], "variant": v, "resolved": bool(g.get("resolved")), "f2p": g.get("f2p"),
                    "p2p": g.get("p2p"), "history_chars": len(hist) if "hist" in v else 0,
                    "changed_files": re.findall(r"^\+\+\+ b/(\S+)", diff, re.M)})
        (out_dir / f"{v}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        results.append(rec)
        print(json.dumps(rec), flush=True)
    return results


if modal is not None:
    import modal_series as ms                     # noqa: E402  (the series' image: the same Python, Node, uv and code)

    app = modal.App("kell-replay")
    volume = modal.Volume.from_name(ms.VOLUME_NAME, create_if_missing=True)

    # at most 3 issues at once, and --key-env names several keys to share them out: one key's tokens-per-minute quota shared by
    # 6 agents made gpt-oss take 12-21 s a call and run out of time with no edit on 8 tries (replay-2, 2026-10-28); the product
    # runs 2 at once per provider
    @app.function(image=ms.build_image(), volumes={VOL: volume}, secrets=[modal.Secret.from_name(ms.KEYS_SECRET)],
                  timeout=6 * 3600, cpu=2.0, memory=8192, max_containers=3)
    def replay(item: dict, variants: list[str], run: str) -> list[dict]:
        try:
            return replay_one(item, variants, run)
        finally:
            volume.commit()

    @app.local_entrypoint()
    def main(tasks: str, variants: str = "base,hist,method,hist+method", run: str = "replay-1", model: str = "glm-5.3-flash",
             key_env: str = ""):
        keys = [k.strip() for k in key_env.split(",") if k.strip()]
        items = [{**it, "model": model, **({"key_env": keys[i % len(keys)]} if keys else {})}
                 for i, it in enumerate(json.loads(Path(tasks).read_text(encoding="utf-8")))]
        vs = [v.strip() for v in variants.split(",") if v.strip()]
        rows = [r for res in replay.starmap([(it, vs, run) for it in items]) for r in res]
        for r in rows:                              # a try that fetched the project from the network is not a solve
            r["solved"] = bool(r["resolved"]) and not r.get("fetched")
        print("\nissue                      " + "".join(f"{v:>14}" for v in vs))
        for iid in [it["instance_id"] for it in items]:
            got = {r["variant"]: r for r in rows if r["instance_id"] == iid}
            print(f"{iid:<27}" + "".join(f"{('Y' if got[v]['solved'] else 'F' if got[v]['resolved'] else 'n') + ' ' + str(got[v]['stop'])[:9]:>14}" if v in got else f"{'-':>14}" for v in vs))
        for v in vs:
            rs = [r for r in rows if r["variant"] == v]
            print(f"{v}: {sum(r['solved'] for r in rs)}/{len(rs)} solved ({sum(bool(r.get('fetched')) for r in rs)} fetched from the network), ${sum(r['cost_usd'] or 0 for r in rs):.2f}")

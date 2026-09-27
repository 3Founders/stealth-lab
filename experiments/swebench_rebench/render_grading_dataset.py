"""Build runs/grading_dataset.json: the SWE-rebench tasks in the format the pinned swebench 5.0.2 grades.

    python experiments/swebench_rebench/render_grading_dataset.py        # after design.py

swebench 5.x grades from per-task `image`, `eval_script`, `log_parser`, `eval_type`. SWE-rebench ships
`install_config` instead, and its own harness fork turns that into the eval script. So each script is rendered
by THAT fork (pinned commit, cloned into cache/, imported in a child process so it never mixes with the
installed swebench), exactly as SWE-rebench's own evaluation would run it:
    image       <- docker_image (the task's prebuilt image, which already has the repo at /testbed)
    eval_script <- fork make_test_spec(instance, namespace="swerebench").eval_script
    log_parser  <- install_config["log_parser"] (same parser names exist in swebench 5.x)
    eval_type   <- pass_and_fail
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "swebench"))
import swe_env  # noqa: E402

CHILD = r'''
import importlib.abc, importlib.machinery, json, sys, types

class _Any(types.ModuleType):          # the fork's package imports its TractoAI backend (tracto_eval, `yt`) at
    __path__ = []                      # load time; rendering never calls it, so those imports get a permissive dummy
    def __getattr__(self, name):
        return _Any(f"{self.__name__}.{name}")
    def __call__(self, *a, **k):
        return _Any(self.__name__)
    def __setitem__(self, key, value):
        pass
    def __getitem__(self, item):       # type hints such as yt.X[...] / X | None, and use as a base class
        return self
    def __or__(self, other):
        return self
    __ror__ = __or__
    def __mro_entries__(self, bases):
        return (object,)

class _YtFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        stub = name == "yt" or name.startswith(("yt.", "swebench.harness.tracto_eval"))
        return importlib.machinery.ModuleSpec(name, self) if stub else None
    def create_module(self, spec):
        return _Any(spec.name)
    def exec_module(self, module):
        pass

sys.meta_path.insert(0, _YtFinder())
from swebench.harness.test_spec.test_spec import make_test_spec
src = json.load(open(sys.argv[1], encoding="utf-8"))
out = []
for inst in src:
    ts = make_test_spec(inst, namespace="swerebench")
    out.append({"instance_id": inst["instance_id"], "eval_script": ts.eval_script})
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"))
'''


def fork_dir() -> Path:
    fork = swe_env.CONFIG["grading"]["fork"]
    path = swe_env.CACHE / "rebench-fork"
    if not path.exists():
        subprocess.run(["git", "clone", "-q", fork["url"], str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "checkout", "-q", fork["commit"]], check=True)
    return path


def main() -> None:
    src = json.loads((swe_env.RUNS / "grading_source.json").read_text(encoding="utf-8"))
    for inst in src:   # the fork expects install_config as a dict
        if isinstance(inst.get("install_config"), str):
            inst["install_config"] = json.loads(inst["install_config"])
    tmp_in, tmp_out = swe_env.RUNS / "_render_in.json", swe_env.RUNS / "_render_out.json"
    tmp_in.write_text(json.dumps(src), encoding="utf-8")
    env = {**os.environ, "PYTHONUTF8": "1",
           "PYTHONPATH": os.pathsep.join([str(fork_dir()), str(swe_env.HERE / "winshim")])}
    subprocess.run([sys.executable, "-c", CHILD, str(tmp_in), str(tmp_out)], check=True, env=env, cwd=swe_env.RUNS)
    scripts = {r["instance_id"]: r["eval_script"] for r in json.loads(tmp_out.read_text(encoding="utf-8"))}
    tmp_in.unlink()
    tmp_out.unlink()
    # SWE-rebench images install the repo editable from /<name> but ship it at /testbed, so the package does not
    # import under the standard harness (Docker or Modal: ModuleNotFoundError on every gold patch). Link each
    # missing editable root to /testbed at the start of the eval script -- the same fix for every backend, and
    # no per-image build layer.
    from modal_compat import REBENCH_LINK_SH
    link = "\n".join(l for l in REBENCH_LINK_SH.strip().splitlines())
    for iid, script in scripts.items():
        head, sep, rest = script.partition("set -uxo pipefail\n")
        scripts[iid] = (head + sep + link + "\n" + rest) if sep else (link + "\n" + script)
    out = []
    for inst in src:
        f2p, p2p = inst["FAIL_TO_PASS"], inst["PASS_TO_PASS"]
        out.append({"instance_id": inst["instance_id"], "repo": inst["repo"], "version": inst.get("version") or "",
                    "base_commit": inst["base_commit"], "patch": inst["patch"], "test_patch": inst["test_patch"],
                    "FAIL_TO_PASS": json.loads(f2p) if isinstance(f2p, str) else f2p,
                    "PASS_TO_PASS": json.loads(p2p) if isinstance(p2p, str) else p2p,
                    "image": inst["docker_image"], "eval_script": scripts[inst["instance_id"]],
                    "log_parser": inst["install_config"]["log_parser"], "eval_type": "pass_and_fail"})
    (swe_env.RUNS / "grading_dataset.json").write_text(json.dumps(out), encoding="utf-8")
    print(f"grading dataset: {len(out)} tasks -> {swe_env.RUNS / 'grading_dataset.json'}")


if __name__ == "__main__":
    main()

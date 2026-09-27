"""Pin the environment on first use; refuse to run when it drifts (so no arm is run on a different setup).

    python check_env.py --check    # prints the environment and checks readiness (docker, swebench, clean tree)

The first SCORED step (train-pool generation, after max_steps is frozen and committed) writes
runs/pinned.json; every later scored step, and grading of scored runs, must match it exactly.

Pinned: Kel commit (git HEAD of this repo, must be clean in backend/ and experiments/swebench/),
swebench package version, the dataset revision (HF commit sha), Python version, Docker server
version (or the modal grading backend), and the agent model id + base URL host. Every other script calls `require_pinned()`.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys

import swe_env

PINNED = swe_env.RUNS / "pinned.json"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=swe_env.ROOT, capture_output=True, text=True).stdout.strip()


def current() -> dict:
    import os
    from urllib.parse import urlparse

    try:
        import swebench  # noqa: F401
        from importlib.metadata import version
        swebench_version = version("swebench")
    except Exception:  # noqa: BLE001
        swebench_version = None
    backend = grading_backend()
    if backend == "modal":
        docker = "n/a (graded on modal)"      # generation needs no Docker; grading images run remotely
    else:
        try:
            docker = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True,
                                    text=True, timeout=30).stdout.strip() or None
        except Exception:  # noqa: BLE001
            docker = None
    dirty = _git("status", "--porcelain", "--untracked-files=no", "--", "backend", "experiments/swebench/*.py", "experiments/swebench/experiment.json")
    cfg = swe_env.CONFIG
    return {
        "kel_commit": _git("rev-parse", "HEAD"), "kel_tree_clean": not dirty,
        "swebench_version": swebench_version, "docker_server": docker, "grading_backend": backend,
        "modal_ready": modal_ready() if backend == "modal" else None,
        "modal_compat": _modal_compat() if backend == "modal" else None,
        "python": platform.python_version(),
        "dataset": cfg["dataset"]["name"], "dataset_revision": dataset_revision(),
        "model": cfg["model"]["id"],
        "model_host": urlparse(os.environ.get(cfg["model"]["base_url_env"], "")).hostname,
        "max_steps": cfg["agent"]["max_steps"],
    }


def grading_backend() -> str:
    return swe_env.CONFIG["grading"].get("backend", "docker")


def modal_ready() -> bool:
    """Modal credentials present (the token file `modal setup` writes, or MODAL_TOKEN_ID/SECRET)."""
    import os
    from pathlib import Path

    try:
        import modal  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return (Path.home() / ".modal.toml").exists() or bool(os.environ.get("MODAL_TOKEN_ID"))


def _modal_compat() -> str | None:
    """sha256 of the patched harness Modal runner (modal_compat.py), or None when the patch is not applied."""
    import hashlib

    import modal_compat
    try:
        return hashlib.sha256(modal_compat.target().read_bytes()).hexdigest() if modal_compat.applied() else None
    except SystemExit:
        return None


def dataset_revision() -> str | None:
    cfg = swe_env.CONFIG["dataset"]
    if cfg.get("revision"):
        return cfg["revision"]
    try:
        from huggingface_hub import HfApi
        return HfApi().dataset_info(cfg["name"]).sha
    except Exception:  # noqa: BLE001
        return None


def require_pinned(*, scored: bool = True) -> dict:
    """Refuse to continue unless the environment matches runs/pinned.json (written on first call)."""
    env = current()
    problems = []
    if not env["kel_tree_clean"]:
        problems.append("uncommitted changes in backend/ or experiments/swebench/ -- commit or stash first")
    if env["swebench_version"] is None:
        problems.append("swebench is not installed (pip install swebench)")
    if env["docker_server"] is None:
        problems.append("docker is not reachable (docker version failed) -- or set grading.backend to \"modal\"")
    if env["grading_backend"] == "modal" and not env["modal_ready"]:
        problems.append("grading.backend is modal but modal is not installed/authenticated (pip install "
                        "\"swebench[modal]\" && modal setup)")
    if env["grading_backend"] == "modal" and env["modal_compat"] is None:
        problems.append("the harness's Modal runner is not patched for the current Modal API (python modal_compat.py)")
    if scored and env["max_steps"] is None:
        problems.append("agent.max_steps is not frozen yet -- run the calibration stage first")
    if problems:
        raise SystemExit("ENVIRONMENT NOT READY:\n  - " + "\n  - ".join(problems))
    if not scored:
        return env          # calibration / gold check: readiness only; pinning starts with the first scored step
    if not PINNED.exists():
        PINNED.write_text(json.dumps(env, indent=1), encoding="utf-8")
        print(f"pinned environment written to {PINNED}")
        return env
    pinned = json.loads(PINNED.read_text(encoding="utf-8"))
    keys = ("kel_commit", "swebench_version", "docker_server", "grading_backend", "modal_compat", "python", "dataset_revision", "model", "model_host",
            "max_steps")
    drift = {k: (pinned.get(k), env.get(k)) for k in keys if pinned.get(k) != env.get(k)}
    if drift:
        raise SystemExit("ENVIRONMENT DRIFT vs runs/pinned.json (pinned, now):\n" +
                         "\n".join(f"  {k}: {v[0]!r} -> {v[1]!r}" for k, v in drift.items()) +
                         "\nArms must run on one identical setup. Restore it, or log a deviation and start a new run.")
    return env


if __name__ == "__main__":
    print(json.dumps(current(), indent=1))
    if "--check" in sys.argv:
        require_pinned(scored=False)
        print("environment ready (not pinned: pinning happens at the first scored step)")

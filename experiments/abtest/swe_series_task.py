"""A REAL long-horizon series: issues of one open-source Python repository, in the order they were filed, each graded by the
fixing change's own tests (SWE-rebench: github.com/nebius, dataset `nebius/SWE-rebench`, filtered split, CC-BY-4.0).

What a series is here (see runner.py --series, series_toy_task.py for the contract):
  * ONE workspace per arm for the whole series; for arm C its `.kell/` (library, survey facts, routing record) carries
    over from issue to issue, for C0 it is wiped before each, for A there is none -- so C minus C0 is what a persistent
    `.kell/` is worth, on work a developer would really do in one repository over weeks.
  * Each issue is graded at ITS OWN base commit: before every task `prepare_task` puts the workspace's files at that issue's
    base commit (the previous tasks' accepted fixes are NOT applied; only `.git`, `.kell/`, `.claude/` are kept). The
    knowledge carries over, the code does not -- so each issue's hidden tests are exact.
  * Grading (`grade_workspace`): a COPY of the workspace; the test files the fixing PR touched are put back to the base
    commit's versions (an agent that edits a test to make it pass gains nothing), the PR's `test_patch` is applied, and the
    FAIL_TO_PASS tests must now pass and the PASS_TO_PASS tests must still pass. solved = all of them. The tests and the gold
    patch live under `.local/swe/tasks/` (outside every workspace) and are never shown to an arm (runner.FORBIDDEN lists them).
  * Environment: one Python version per repository (SERIES[name]["python"]), dependencies installed from the FIRST issue's
    pinned requirements (the dataset records a working freeze per instance, minus the `-e git+...` line), one venv per arm
    for the agents and one clean venv for grading; the workspace itself is installed editable (`--no-deps`) at every task, as
    a developer would. Needs `uv` (it fetches the Python version) and, once, the network.

Selection criteria (`python swe_series_task.py candidates` prints what passes them; the chosen series are in SERIES below):
  1. >= 8 instances of the repository in the filtered split with ONE Python version (install_config.python) between them;
  2. pure Python: pytest as the test command (log parser parse_log_pytest), every test id a `file::name` id, no apt-get beyond
     the generic `gcc` line, no database or system library in the install; permissive license;
  3. small-to-medium: a repository a laptop can test in minutes (the instances' PASS_TO_PASS lists are short);
  4. a WINDOW of consecutive issues (by creation date), 8-12 of them, with the shortest span, so the repository changes
     little under the dependencies installed at the first issue;
  5. checked with the GOLD patch: `python swe_series_task.py validate <series>` -- the issue is kept only if, in this
     environment, its FAIL_TO_PASS tests fail at the base commit and pass with the gold patch, and its PASS_TO_PASS tests pass
     with it. Issues that fail the gold check are listed under SKIPPED with the reason, not silently dropped.

STATUS: SERIES holds the windows `candidates --ids` found (jsonargparse, sqlglot); they have passed criteria 1-4 only. The gold check
(criterion 5) needs Linux and the dataset's Python (3.9), so it is the first thing the Modal app (modal_series.py) does for the
issues it is about to run (the unusable ones are dropped from that run and reported); it has not been run anywhere yet.

The module contract (runner.py reads exactly this): series, sample, setup_project, prepare_task, python_dir, files, prompt,
issue_text, grade_workspace, CHECK.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE / ".local" / "swe"
DATA = LOCAL / "tasks"            # the selected instances: the issue, the gold patch, the hidden tests (never in a workspace)
CLONES = LOCAL / "repos"          # bare clones, one per repository
VENVS = LOCAL / "venvs"
DATASET, SPLIT_FILE = "nebius/SWE-rebench", "data/filtered-00000-of-00001.parquet"
TIMEOUT_S = 900                   # one grading run (the F2P and P2P test files)
KEEP = {".git", ".kell", ".claude", ".series_ready"}      # what a task switch leaves in the workspace
SKIP_COPY = KEEP | {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".venv", "venv"}
CHECK = ""                        # no project check configured: arm C relies on its test-first tests, as in the fresh-repo flow
WORKSPACE_PREFIX = "swe-"
PROMPT_CHARS = 3000               # the single-prompt form of an issue (a Windows .cmd shim cuts long command lines)

# name -> the chosen series. "issues" are SWE-rebench instance ids in creation order (filled in by `validate`, see SKIPPED).
SERIES: dict[str, dict] = {
    # the tightest windows of 10 consecutive issues among pure-pytest repositories with ONE Python version (`candidates --ids`,
    # 2026-10-14); the gold check (validate, run first by modal_series.py) decides which of them are usable
    "jsonargparse": {"repo": "omni-us/jsonargparse", "python": "3.9", "issues": [
        "omni-us__jsonargparse-502", "omni-us__jsonargparse-508", "omni-us__jsonargparse-518", "omni-us__jsonargparse-527",
        "omni-us__jsonargparse-531", "omni-us__jsonargparse-559", "omni-us__jsonargparse-560", "omni-us__jsonargparse-562",
        "omni-us__jsonargparse-564", "omni-us__jsonargparse-576"]},
    "sqlglot": {"repo": "tobymao/sqlglot", "python": "3.9", "issues": [
        "tobymao__sqlglot-4051", "tobymao__sqlglot-4067", "tobymao__sqlglot-4079", "tobymao__sqlglot-4087", "tobymao__sqlglot-4093",
        "tobymao__sqlglot-4096", "tobymao__sqlglot-4104", "tobymao__sqlglot-4106", "tobymao__sqlglot-4109", "tobymao__sqlglot-4110"]},
}
SKIPPED: dict[str, dict[str, str]] = {}      # series -> {instance id: why the gold check refused it}


def clone_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, timeout: int | None = None,
        check: bool = True, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, check=False, input=input_bytes, capture_output=True)
    if check and r.returncode != 0:                 # say WHY (a bare CalledProcessError hides the command's own message)
        tail = (r.stderr or b"").decode("utf-8", "replace")[-800:] or (r.stdout or b"").decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"`{' '.join(map(str, cmd))[:160]}` failed (exit {r.returncode}): {tail}")
    return r


def one_line(text: str) -> str:
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------- the series and its data

def series(name: str | None = None) -> list[dict]:
    """The ordered tasks of one series: the issues of one repository, by creation date, all in one workspace."""
    if not SERIES:
        raise SystemExit("swe_series_task.SERIES is empty: no series has been chosen yet")
    name = name or next(iter(SERIES))
    if name not in SERIES:
        raise SystemExit(f"unknown swe series {name!r} (known: {', '.join(SERIES)})")
    return [{"task_id": iid, "suite": "swe", "workspace": f"{WORKSPACE_PREFIX}{name}", "instance_id": iid, "series_name": name,
             "index": i} for i, iid in enumerate(SERIES[name]["issues"])]


def sample(n: int, seed: int) -> list[dict]:       # the module also works as a plain suite: the first n issues of the first series
    return series(None)[:n]


def records(name: str) -> dict[str, dict]:
    path = DATA / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing: run  python experiments/abtest/swe_series_task.py fetch {name}")
    return {r["instance_id"]: r for r in json.loads(path.read_text(encoding="utf-8"))}


def rec(task: dict) -> dict:
    return records(task["series_name"])[task["instance_id"]]


def issue_text(task: dict) -> str:
    """The issue as filed (the multi-turn driver rewrites it as a vague opener)."""
    return str(rec(task)["problem_statement"]).strip()


def prompt(task: dict) -> str:
    """One line a developer might paste: the issue text, flattened and cut (the single-prompt form)."""
    return one_line(issue_text(task))[:PROMPT_CHARS]


def files(task: dict, checker: Path) -> dict[str, str]:
    return {}                          # nothing is added to the repository: no TASK.md, no visible check, no hint


# ---------------------------------------------------------------- git: the repository at a commit

def clone_dir(repo: str) -> Path:
    return CLONES / (repo.replace("/", "__") + ".git")


def ensure_commit(repo: str, sha: str) -> Path:
    """The bare clone of `repo` with commit `sha` in it (cloned once, the commit fetched when a branch does not reach it)."""
    d = clone_dir(repo)
    if not d.exists():
        CLONES.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--bare", "-q", clone_url(repo), str(d)])
    if run(["git", f"--git-dir={d}", "cat-file", "-e", f"{sha}^{{commit}}"], check=False).returncode != 0:
        run(["git", f"--git-dir={d}", "fetch", "-q", "origin", sha])
    return d


def export_tree(repo: str, sha: str, dest: Path) -> None:
    """The files of `repo` at `sha`, without history, extracted into the empty directory `dest`."""
    d = ensure_commit(repo, sha)
    tar = run(["git", f"--git-dir={d}", "archive", "--format=tar", sha]).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:              # Python < 3.12
            tf.extractall(dest)


def sync_tree(src: Path, ws: Path) -> None:
    """Make `ws` hold exactly `src`'s files, keeping what belongs to the arm (.git, .kell, .claude, .series_ready)."""
    for child in ws.iterdir():
        if child.name in KEEP:
            continue
        shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
    for child in src.iterdir():
        if child.name in KEEP:
            continue
        shutil.copytree(child, ws / child.name) if child.is_dir() else shutil.copy2(child, ws / child.name)


def patch_files(patch: str) -> list[str]:
    """The paths a unified diff touches (b-side of `diff --git a/x b/x`)."""
    return re.findall(r"^diff --git a/(\S+) b/\1$", patch, flags=re.M)


def reuse_opportunity(patches: list[str]) -> dict:
    """How much an earlier issue's fix could help a later one: the share of the issues after the first whose gold patch touches a
    file an EARLIER issue of the window touched. Only the files are compared (not the lines), so it is a ceiling on what a library
    of earlier fixes could be matched to. Returns {share, issues, touching, files_per_issue}."""
    seen: set[str] = set()
    touching = 0
    per_issue = []
    for i, patch in enumerate(patches):
        files = set(patch_files(patch))
        per_issue.append(len(files))
        if i and files & seen:
            touching += 1
        seen |= files
    later = max(len(patches) - 1, 0)
    return {"share": round(touching / later, 3) if later else 0.0, "issues": len(patches), "touching": touching, "files_per_issue": per_issue}


def apply_patch(directory: Path, patch: str) -> tuple[bool, str]:
    """`git apply` in a plain directory (it works outside a repository too)."""
    r = run(["git", "apply", "--whitespace=nowarn", "-"], cwd=directory, input_bytes=patch.encode("utf-8"), check=False)
    return r.returncode == 0, r.stderr.decode("utf-8", "replace")[-400:]


# ---------------------------------------------------------------- the Python environment (uv)

def clean_requirements(text: str) -> list[str]:
    """The dataset's pip freeze for an instance, without the repository's own editable line and anything not installable from an index."""
    out = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-e ") or s.startswith("--") or "git+" in s or "file://" in s or " @ " in s:
            continue
        out.append(s)
    return out


def env_requirements(text: str, extra: list[str] | tuple = ()) -> list[str]:
    """What an instance's venv gets: its pip freeze, pytest, the dataset's own test packages (`extra`: install_config
    pip_packages, those the freeze does not name already), and setuptools<81 when the freeze does not pin setuptools --
    81 removed pkg_resources, a venv seeded today gets 82, and projects of these issues' age import it (in the survey,
    2 of 21 sets of tests failed on that alone, 2026-10-16)."""
    reqs = clean_requirements(text)
    name = lambda s: re.split(r"[<>=!~\[; ]", s.strip(), maxsplit=1)[0].lower().replace("_", "-")
    named = {name(s) for s in reqs}
    out = list(reqs)
    for p in ["pytest", *extra]:
        if name(p) not in named:
            out.append(p)
            named.add(name(p))
    if "setuptools" not in named:
        out.append("setuptools<81")
    return out


def editable_extras(install_cmd: str) -> str:
    """The extras of the dataset's install command ("pip install -e .[dev,tests]" -> "[dev,tests]"), else ""."""
    m = re.search(r"-e\s+\.(\[[^\]]+\])", str(install_cmd or ""))
    return m.group(1) if m else ""


class UvToolchain:
    """venvs and installs through `uv` (it fetches the Python version itself); tests replace this with a stand-in."""

    def venv(self, path: Path, python: str) -> Path:
        exe = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not exe.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            run(["uv", "venv", "--python", python, "--seed", "-q", str(path)])
        return exe

    def install(self, py: Path, requirements: list[str]) -> None:
        if not requirements:
            return
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("\n".join(requirements) + "\n", encoding="utf-8")
            r = run(["uv", "pip", "install", "--python", str(py), "-q", "-r", str(req)], check=False)
        if r.returncode != 0:
            raise RuntimeError("installing the pinned requirements failed: " + r.stderr.decode("utf-8", "replace")[-600:])

    def install_editable(self, py: Path, project: Path, extras: str = "") -> None:
        # extras ("[dev]"): the optional dependencies the dataset's install command names; --no-deps keeps the freeze's pins
        r = run(["uv", "pip", "install", "--python", str(py), "-q", "--no-deps", "-C", "editable_mode=compat", "-e", f"{project}{extras}"],
                check=False)
        if r.returncode != 0:
            raise RuntimeError("the editable install failed: " + r.stderr.decode("utf-8", "replace")[-600:])

    def pytest(self, py: Path, args: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTEST_ADDOPTS", "VIRTUAL_ENV")}
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            r = run([str(py), "-m", "pytest", *args], cwd=cwd, env=env, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        return r.returncode, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")


TOOLCHAIN = UvToolchain()


def arm_of(ws: Path) -> str:
    """The runner's workspaces are .local/ws/<arm>/<name>."""
    return ws.parent.name


def series_of(task: dict) -> dict:
    return SERIES[task["series_name"]]


def first_record(task: dict) -> dict:
    return records(task["series_name"])[SERIES[task["series_name"]]["issues"][0]]


def arm_python(task: dict, ws: Path) -> Path:
    return TOOLCHAIN.venv(VENVS / f"{arm_of(ws)}-{task['series_name']}", series_of(task)["python"])


def grade_python(task: dict) -> Path:
    py = TOOLCHAIN.venv(VENVS / f"grade-{task['series_name']}", series_of(task)["python"])
    marker = VENVS / f"grade-{task['series_name']}.ready"
    if not marker.exists():
        TOOLCHAIN.install(py, env_requirements(first_record(task)["requirements"]))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
    return py


def setup_project(task: dict, ws: Path) -> None:
    """Once per workspace and arm: the arm's venv with the first issue's pinned requirements (the agents' `python`)."""
    py = arm_python(task, ws)
    TOOLCHAIN.install(py, env_requirements(first_record(task)["requirements"]))


def record_history(r: dict, ws: Path) -> None:
    """The project's own commits up to this issue's base commit become part of the workspace's history -- a merge that
    keeps the workspace's files (`-s ours`) -- as in a developer's clone, so the survey can mine past fixes into the
    library. Only the base commit and its ancestors are fetched: nothing after it (the fix) can be seen.
    ABTEST_SERIES_HISTORY=off: no history, as before."""
    if os.environ.get("ABTEST_SERIES_HISTORY", "on").lower() == "off" or not (ws / ".git").exists():
        return                               # the gold check's plain directories have no history to carry
    sha = r["base_commit"]
    d = ensure_commit(r["repo"], sha)
    # the harness's own commits (this merge, the runner's "task"/"after" commits) carry the base commit's date: dated
    # today they made the history look two years old, and the survey's miner (two years before HEAD) found nothing
    when = run(["git", f"--git-dir={d}", "show", "-s", "--format=%cI", sha]).stdout.decode().strip()
    if when:
        os.environ["GIT_AUTHOR_DATE"] = os.environ["GIT_COMMITTER_DATE"] = when
    ref = f"refs/abtest/{sha}"
    run(["git", f"--git-dir={d}", "update-ref", ref, sha])
    run(["git", "-C", str(ws), "fetch", "-q", "--no-tags", str(d), f"{ref}:{ref}"])
    run(["git", "-C", str(ws), "merge", "-q", "-s", "ours", "--allow-unrelated-histories", "--no-edit",
         "-m", f"upstream history up to {sha[:10]}", ref])


def prepare_task(task: dict, ws: Path) -> None:
    """Before every task: the workspace's files become this issue's base commit; the project is installed editable."""
    r = rec(task)
    record_history(r, ws)                    # first, while the workspace is clean (the last task was committed)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        export_tree(r["repo"], r["base_commit"], base)
        sync_tree(base, ws)
    TOOLCHAIN.install_editable(arm_python(task, ws), ws)


def python_dir(task: dict, ws: Path) -> Path:
    """Put the arm's venv first on PATH, so the agent's, dispatch's and the test-first tests' `python` is the project's."""
    return arm_python(task, ws).parent


# ---------------------------------------------------------------- grading

def parse_passed(output: str) -> set[str]:
    """Test ids pytest reported as PASSED (`-rA` short summary lines)."""
    return {m.group(1).rstrip() for m in re.finditer(r"^PASSED (.+)$", output, flags=re.M)}


def test_args(r: dict) -> list[str]:
    """The dataset's pytest flags (minus the program name), with -rA so every outcome is listed."""
    cmd = str((r.get("install_config") or {}).get("test_cmd") or "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider")
    parts = cmd.split()
    while parts and parts[0] in ("pytest", "python", "-m", "python3"):
        parts.pop(0)
    if "-rA" not in parts:
        parts.append("-rA")
    return parts


def grade_copy(task: dict, tree: Path, *, gold: bool = False, exclude: tuple[str, ...] | list[str] = ()) -> dict:
    """Grade the files in `tree` (a throwaway copy): test files back to the base versions, the PR's test patch applied, then
    FAIL_TO_PASS must pass and PASS_TO_PASS must still pass. gold=True first applies the gold patch (the validation).
    `exclude`: PASS_TO_PASS tests that fail in THIS environment with and without the real fix (see `validate`); they are not
    held against anyone. The returned `_passed` (the set of tests that passed) is for `validate` and is not recorded."""
    r = rec(task)
    if gold:
        ok, err = apply_patch(tree, r["patch"])
        if not ok:
            return {"resolved": False, "check_pass": False, "infra": True, "error": f"the gold patch does not apply: {err}"}
    touched = patch_files(r["test_patch"])
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        export_tree(r["repo"], r["base_commit"], base)
        for rel in touched:                         # a test the agent changed or created goes back to the base state
            target = tree / rel
            if target.exists():
                target.unlink()
            if (base / rel).exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(base / rel, target)
    ok, err = apply_patch(tree, r["test_patch"])
    if not ok:
        return {"resolved": False, "check_pass": False, "infra": True, "error": f"the test patch does not apply: {err}"}
    f2p, p2p = list(r["FAIL_TO_PASS"]), list(r["PASS_TO_PASS"])
    targets = sorted({i.split("::")[0] for i in f2p + p2p if "::" in i and (tree / i.split("::")[0]).exists()})
    if not targets:
        return {"resolved": False, "check_pass": False, "infra": True, "error": "no test file of the instance exists in the tree"}
    py = grade_python(task)
    try:
        TOOLCHAIN.install_editable(py, tree)
    except RuntimeError as exc:
        return {"resolved": False, "check_pass": False, "infra": True, "error": str(exc)[:300]}
    code, out = TOOLCHAIN.pytest(py, [*test_args(r), *targets], tree, TIMEOUT_S)
    if code == 124:
        return {"resolved": False, "check_pass": False, "error": "the tests timed out"}
    passed = parse_passed(out)
    skip = set(exclude)
    miss_f, miss_p = [i for i in f2p if i not in passed], [i for i in p2p if i not in passed and i not in skip]
    return {"resolved": not miss_f and not miss_p, "check_pass": not miss_f,
            "f2p": f"{len(f2p) - len(miss_f)}/{len(f2p)}", "p2p": f"{len(p2p) - len(skip & set(p2p)) - len(miss_p)}/{len(p2p) - len(skip & set(p2p))}",
            **({"p2p_excluded": len(skip & set(p2p))} if skip & set(p2p) else {}),
            "error": None if not (miss_f or miss_p) else "; ".join(
                ([f"{len(miss_f)} FAIL_TO_PASS still failing, e.g. {miss_f[0]}"] if miss_f else [])
                + ([f"{len(miss_p)} PASS_TO_PASS broken, e.g. {miss_p[0]}"] if miss_p else []))[:300],
            "_passed": passed}


def grade_workspace(task: dict, ws: Path) -> dict:
    """The hidden grade of the workspace as the arm left it (see the module docstring)."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        shutil.copytree(ws, tree, ignore=lambda d, names: [n for n in names if n in SKIP_COPY or n.endswith(".egg-info")])
        g = grade_copy(task, tree, exclude=excluded_p2p(task))
        g.pop("_passed", None)
        return g


def excluded_p2p(task: dict) -> list[str]:
    """The PASS_TO_PASS tests `validate` found broken in this environment for this issue (validate_<series>.json), or []."""
    path = LOCAL / f"validate_{task['series_name']}.json"
    if not path.exists():
        return []
    for row in json.loads(path.read_text(encoding="utf-8")):
        if row.get("instance_id") == task["instance_id"]:
            return list(row.get("p2p_exclude") or [])
    return []


# ---------------------------------------------------------------- choosing and checking a series (CLI)

def load_split():
    """The filtered split as a pandas frame (needs huggingface_hub and pyarrow; used by `candidates` and `fetch` only)."""
    from huggingface_hub import hf_hub_download
    import pyarrow.parquet as pq
    return pq.read_table(hf_hub_download(DATASET, SPLIT_FILE, repo_type="dataset")).to_pandas()


SPAN_SLACK_DAYS = 30        # a window competes on reuse opportunity when its span is within this many days of the repository's tightest


def pick_window(spans: list[int], overlaps: list[float], slack: int = SPAN_SLACK_DAYS) -> int:
    """The index of the window to use: among the windows whose span (days) is within `slack` of the tightest, the one with the highest
    reuse opportunity; ties go to the tighter span, then the earlier window. slack=0 is the old rule (the tightest only)."""
    tightest = min(spans)
    pool = [i for i, d in enumerate(spans) if d <= tightest + slack]
    return min(pool, key=lambda i: (-overlaps[i], spans[i], i))


def candidates(window: int = 10, top: int = 25, slack: int = SPAN_SLACK_DAYS) -> list[dict]:
    """Repositories passing criteria 1-2 with a window of `window` consecutive issues (criterion 4): the tightest, or, among the windows
    within `slack` days of the tightest, the one with the highest reuse opportunity (the share of issues whose gold patch touches a file
    an earlier issue in the window touched). Every candidate's overlap is printed by `candidates`."""
    df = load_split()
    df["py"] = df.install_config.map(lambda c: (c or {}).get("python"))
    df["tc"] = df.install_config.map(lambda c: str((c or {}).get("test_cmd") or ""))
    df["pre"] = df.install_config.map(lambda c: " ".join(map(str, [] if (c or {}).get("pre_install") is None else list(c["pre_install"]))))
    df["np2p"] = df.PASS_TO_PASS.map(len)
    out = []
    for repo, x in df.groupby("repo"):
        x = x[(x.tc.str.startswith("pytest")) & (x.FAIL_TO_PASS.map(len) > 0) & (x.np2p <= 300)
              & x.FAIL_TO_PASS.map(lambda ids: all("::" in i for i in ids)) & x.PASS_TO_PASS.map(lambda ids: all("::" in i for i in ids))
              & x.pre.map(lambda p: not p.strip() or set(re.findall(r"install -y (.+)", p)) <= {"gcc"})]
        for py, y in x.groupby("py"):
            y = y.sort_values("created_at")
            if len(y) < window:
                continue
            dates = list(y.created_at.astype("datetime64[ns]"))
            starts = range(len(y) - window + 1)
            spans = [(dates[i + window - 1] - dates[i]).days for i in starts]
            overlaps = [reuse_opportunity(list(y.patch.iloc[i:i + window]))["share"] for i in starts]
            best = pick_window(spans, overlaps, slack)
            w = y.iloc[best:best + window]
            out.append({"repo": repo, "reuse": overlaps[best], "tightest_reuse": overlaps[spans.index(min(spans))], "python": py, "n_with_py": len(y), "span_days": (dates[best + window - 1] - dates[best]).days,
                        "first": str(w.created_at.iloc[0])[:10], "last": str(w.created_at.iloc[-1])[:10],
                        "p2p_median": int(w.np2p.median()), "license": str(w.license_name.iloc[0]), "ids": list(w.instance_id)})
    out.sort(key=lambda c: (-c["reuse"], c["span_days"], c["p2p_median"]))
    return out[:top]


def fetch(name: str) -> Path:
    """Write the chosen series' instances (issue, gold patch, hidden tests, install config) to .local/swe/tasks/<name>.json."""
    wanted = SERIES[name]["issues"] + list(SKIPPED.get(name, {}))
    df = load_split()
    rows = df[df.instance_id.isin(wanted)]
    missing = set(wanted) - set(rows.instance_id)
    if missing:
        raise SystemExit(f"not in the filtered split: {sorted(missing)}")
    keep = ["instance_id", "repo", "base_commit", "created_at", "problem_statement", "patch", "test_patch", "FAIL_TO_PASS",
            "PASS_TO_PASS", "install_config", "requirements", "version", "license_name"]
    recs = []
    for _, row in rows.sort_values("created_at").iterrows():
        d = {k: row[k] for k in keep}
        d["created_at"] = str(d["created_at"])
        d["FAIL_TO_PASS"], d["PASS_TO_PASS"] = list(d["FAIL_TO_PASS"]), list(d["PASS_TO_PASS"])
        ic = dict(d["install_config"] or {})
        d["install_config"] = {k: (list(v) if hasattr(v, "tolist") else v) for k, v in ic.items()}
        recs.append(d)
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{name}.json"
    path.write_text(json.dumps(recs, indent=1, default=str), encoding="utf-8")
    return path


def validate(name: str, only: list[str] | None = None) -> list[dict]:
    """The gold check (criterion 5), with no model: for each issue, at its base commit, the tests fail without the gold patch
    (FAIL_TO_PASS not all passing) and pass with it. Uses a scratch workspace under .local/swe/validate."""
    tasks = series(name)
    ws = LOCAL / "validate" / "val" / f"{WORKSPACE_PREFIX}{name}"
    ws.mkdir(parents=True, exist_ok=True)
    results = []
    setup_project(tasks[0], ws)
    for t in tasks:
        if only and t["instance_id"] not in only:
            continue
        try:
            prepare_task(t, ws)
        except RuntimeError as exc:           # e.g. the project's metadata cannot be installed at this commit: this issue is unusable, not the run
            results.append({"instance_id": t["instance_id"], "base_fails_f2p": False, "base": {}, "gold_resolves": False,
                            "gold": {"error": f"environment: {exc}"[:400], "infra": True}, "usable": False})
            print(f"{t['instance_id']:<44} environment: {str(exc)[:160]}", flush=True)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain"
            gold = Path(tmp) / "gold"
            ignore = lambda d, names: [n for n in names if n in SKIP_COPY or n.endswith(".egg-info")]  # noqa: E731
            shutil.copytree(ws, plain, ignore=ignore)
            shutil.copytree(ws, gold, ignore=ignore)
            before = grade_copy(t, plain)
            after = grade_copy(t, gold, gold=True)
        # PASS_TO_PASS tests that fail at the base commit AND with the real fix are broken by this ENVIRONMENT (a dependency version
        # unlike the dataset's own), not by anyone's change: they are excluded for this issue and said so (p2p_exclude), instead of
        # making a correct fix look wrong. Anything else must still pass.
        p2p = list(rec(t)["PASS_TO_PASS"])
        broken = [i for i in p2p if i not in before.get("_passed", set()) and i not in after.get("_passed", set())] if "_passed" in after else []
        if broken and "_passed" in after:
            miss_f = [i for i in rec(t)["FAIL_TO_PASS"] if i not in after["_passed"]]
            miss_p = [i for i in p2p if i not in after["_passed"] and i not in broken]
            after = {**after, "resolved": not miss_f and not miss_p, "p2p_excluded": len(broken),
                     "error": None if not (miss_f or miss_p) else after.get("error")}
        before.pop("_passed", None)
        after.pop("_passed", None)
        ok = (not before["check_pass"]) and after["resolved"]
        results.append({"instance_id": t["instance_id"], "base_fails_f2p": not before["check_pass"], "base": before,
                        "gold_resolves": after["resolved"], "gold": after, "usable": ok, "p2p_exclude": broken})
        print(f"{t['instance_id']:<44} base F2P {'fails' if not before['check_pass'] else 'PASSES'}   gold "
              f"{'resolves' if after['resolved'] else 'FAILS: ' + str(after.get('error'))}", flush=True)
    return results


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "candidates":
        for c in candidates():
            print(f"{c['repo']:<40} py {c['python']:<5} n={c['n_with_py']:<4} window {c['first']}..{c['last']} ({c['span_days']} d) "
                  f"p2p~{c['p2p_median']:<4} reuse {c['reuse']:.2f} (tightest window: {c['tightest_reuse']:.2f}) {c['license'][:22]}")
            if "--ids" in argv:                   # the window's instance ids, in order: what goes into SERIES[...]["issues"]
                print("    " + json.dumps(c["ids"]))
        return 0
    if cmd == "overlap" and len(argv) == 2:          # the reuse opportunity of a chosen series (after `fetch`)
        recs = records(argv[1])
        r = reuse_opportunity([recs[i]["patch"] for i in SERIES[argv[1]]["issues"] if i in recs])
        print(json.dumps(r))
        return 0
    if cmd == "fetch" and len(argv) == 2:
        print(fetch(argv[1]))
        return 0
    if cmd == "validate" and len(argv) >= 2:
        res = validate(argv[1], argv[2:] or None)
        out = LOCAL / f"validate_{argv[1]}.json"
        out.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"{sum(r['usable'] for r in res)}/{len(res)} usable; details: {out}")
        return 0
    print("usage: swe_series_task.py candidates [--ids] | overlap <series> | fetch <series> | validate <series> [instance ids]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

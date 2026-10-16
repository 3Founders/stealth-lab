"""The pipeline survey: each stage of StealthLab's cheap-model pipeline measured on its own, against the real fix, on many
real issues from many repositories (SWE-rebench) -- so a change is judged by where the pipeline loses issues across
repos, not by the one run that happened to show a problem.

Stage 1, locate: does StealthLab's "likely place" contain the code the real fix changed?
  file@1 / file@3   the first / any of the first three located files is a source file the real fix changed
  spot              a located range overlaps a hunk of the real fix (old-file lines, +-10)
  empty             nothing located
No model, no key: lib/locate.mjs on the repository at the issue's base commit, with the issue text as the prompt.

Selection (deterministic, `select`): Python-only source changes; one issue per repository; half single-file fixes, half
2-5 files. The issue text is the dataset's problem statement, as a user would paste it.

  python pipeline_survey.py select --n 48 --seed 1          # the ids (needs the dataset)
  python pipeline_survey.py stage1 <instance_id> ...        # one JSON line per issue (needs git and node)
  python pipeline_survey.py report <results.jsonl>          # the table
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TEST_OR_DOC = re.compile(r"(^|/)(tests?|testing|docs?|examples?)/|(^|/)test_[^/]*$|_test\.py$|conftest\.py$|\.(md|rst|txt|cfg|toml|ya?ml|json|ini)$")


def source_files(patch: str) -> list[str]:
    return [f for f in re.findall(r"^diff --git a/(\S+)", patch, re.M) if not TEST_OR_DOC.search(f)]


def gold_hunks(patch: str) -> dict[str, list[tuple[int, int, str]]]:
    """source file -> [(old start, old end, hunk header's enclosing name)]"""
    out: dict[str, list] = {}
    cur = None
    for line in patch.splitlines():
        m = re.match(r"^diff --git a/(\S+)", line)
        if m:
            cur = m.group(1) if not TEST_OR_DOC.search(m.group(1)) else None
            continue
        h = re.match(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@\s*(.*)$", line)
        if h and cur:
            a, n = int(h.group(1)), int(h.group(2) or 1)
            name = (re.search(r"(?:def|class)\s+(\w+)", h.group(3)) or [None, ""])[1]
            out.setdefault(cur, []).append((a, a + max(n, 1) - 1, name))
    return out


def select(n: int = 48, seed: int = 1) -> list[str]:
    import swe_series_task as S
    df = S.load_split()
    rows = []
    for r in df.itertuples():
        src = source_files(r.patch)
        if not src or not all(f.endswith(".py") for f in src) or len(src) > 5:
            continue
        rows.append((r.repo, r.instance_id, len(src)))
    rng = random.Random(seed)
    rng.shuffle(rows)
    single, multi, seen = [], [], set()
    for repo, iid, k in rows:
        if repo in seen:
            continue
        bucket = single if k == 1 else multi
        if len(bucket) < n // 2:
            bucket.append(iid)
            seen.add(repo)
        if len(single) >= n // 2 and len(multi) >= n - n // 2:
            break
    return single + multi


def record(iid: str) -> dict:
    import swe_series_task as S
    df = S.load_split()
    r = df[df.instance_id == iid].iloc[0]
    return {k: r[k] for k in ("instance_id", "repo", "base_commit", "patch", "problem_statement")}


def checkout(r: dict, dest: Path) -> None:
    """The repository at the issue's base commit, as a fresh git repository (locate uses git grep / ls-files)."""
    import swe_series_task as S
    S.export_tree(r["repo"], r["base_commit"], dest)
    for args in (["init", "-q"], ["config", "user.email", "s@s"], ["config", "user.name", "s"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", "-C", str(dest), *args], check=True, capture_output=True)


def stage1(iid: str) -> dict:
    r = record(iid)
    gold = gold_hunks(r["patch"])
    files = list(gold) or source_files(r["patch"])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        try:
            checkout(r, root)
        except Exception as e:  # noqa: BLE001 -- a repository that cannot be fetched is reported, not fatal
            return {"instance_id": iid, "repo": r["repo"], "error": f"checkout: {str(e)[:200]}"}
        pf = Path(tmp) / "prompt.txt"
        pf.write_text(str(r["problem_statement"]), encoding="utf-8")
        p = subprocess.run(["node", str(HERE / "survey_locate.mjs"), str(root), str(pf)], capture_output=True, text=True, timeout=300)
        try:
            both = json.loads(p.stdout)
        except ValueError:
            return {"instance_id": iid, "repo": r["repo"], "error": f"locate: {p.stderr[-300:]}"}

    def score(loc: dict) -> dict:
        lf = loc.get("files") or []
        spot = any(s["file"] in gold and any(not (s["end"] < a - 10 or s["start"] > b + 10) for a, b, _ in gold[s["file"]])
                   for s in loc.get("spots") or [])
        sym = any(s.get("symbol") and s["file"] in gold and s["symbol"] in {nm for _, _, nm in gold[s["file"]]} for s in loc.get("spots") or [])
        return {"located": lf[:3], "spots": [f"{s['file']}:{s['start']}-{s['end']}" for s in loc.get("spots") or []][:4],
                "file_at_1": bool(lf) and lf[0] in files, "file_at_3": any(f in files for f in lf[:3]), "spot": spot, "symbol": sym,
                "empty": not lf, "ms": loc.get("ms")}
    out = {"instance_id": iid, "repo": r["repo"], "gold_files": files, "n_gold": len(files),
           "terms": (both["search"].get("terms") or [])[:12], **score(both["search"])}
    if both.get("narrowed"):
        out["narrowed"] = {**score(both["narrowed"]), "log": both["narrowed"].get("log")}
    return out


def environment(r: dict, root: Path, venvs: Path) -> Path:
    """The issue's own Python as the dataset sets it up -- its system packages (pre_install apt lines, where apt is here),
    pinned requirements plus its test packages (pip_packages), the project installed editable with its install command's
    extras -> the venv's python. Before, only the freeze was installed, and some tests failed on the environment alone."""
    import swe_series_task as S
    cfg = r.get("install_config") or {}
    apt = [str(x) for x in (cfg.get("pre_install") if cfg.get("pre_install") is not None else [])]
    pkgs = sorted({p for line in apt for m in re.findall(r"apt-get install -y ((?:[\w.+-]+\s*)+)", line) for p in m.split()})
    if pkgs and shutil.which("apt-get") and hasattr(os, "geteuid") and os.geteuid() == 0:
        subprocess.run(["apt-get", "update", "-qq"], capture_output=True, timeout=300)
        subprocess.run(["apt-get", "install", "-y", "-qq", *pkgs], capture_output=True, timeout=600)
    py = S.TOOLCHAIN.venv(venvs / r["instance_id"], str(cfg.get("python") or "3.11"))
    extra = [str(p) for p in (cfg.get("pip_packages") if cfg.get("pip_packages") is not None else [])]
    # "packages": a requirements file's name, or the project's own packages listed by name ("ply rdflib click")
    pk = str(cfg.get("packages") or "")
    if pk and not re.search(r"\.(txt|yml|yaml|toml|cfg)\b", pk):
        extra += pk.split()
    S.TOOLCHAIN.install(py, S.env_requirements(r.get("requirements"), extra))
    S.TOOLCHAIN.install_editable(py, root, S.editable_extras(cfg.get("install")))
    return py


def run_spec(py: Path, spec: Path, cwd: Path) -> tuple[int, str]:
    """As the product runs them (lib/dispatch.mjs RUN_INSIDE): copied into the project's test folder, run, removed."""
    import shutil
    import swe_series_task as S
    d = next((cwd / t for t in ("tests", "test", "testing") if (cwd / t).is_dir()), cwd)
    dest = d / "test_zz_stealthlab_spec.py"
    shutil.copyfile(spec, dest)
    try:
        # coverage off, as the product runs them: a project's fail-under on one file of tests fails every run
        has_cov = subprocess.run([str(py), "-c", "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest_cov') else 1)"],
                                 capture_output=True).returncode == 0
        return S.TOOLCHAIN.pytest(py, ["-q", "-x", "-p", "no:cacheprovider", *(["--no-cov"] if has_cov else []), str(dest)], cwd, 300)
    finally:
        dest.unlink(missing_ok=True)


def stage2(iid: str, work: Path) -> dict:
    """The tests StealthLab writes for the issue: written at all? failing on the original code? passing with the real fix?
    Valid (fail before, pass after) is what makes them trustworthy evidence; failing WITH the real fix means they would
    reject a right answer."""
    import swe_series_task as S
    r = record(iid)
    full = S.load_split()
    full = full[full.instance_id == iid].iloc[0]
    r["install_config"], r["requirements"] = full["install_config"], full["requirements"]
    out = {"instance_id": iid, "repo": r["repo"], "n_gold": len(source_files(r["patch"]))}
    root = work / "repo"
    try:
        checkout(r, root)
        py = environment(r, root, work / "venvs")
    except Exception as e:  # noqa: BLE001
        return {**out, "env_error": str(e)[:300]}
    pf = work / "prompt.txt"
    pf.write_text(str(r["problem_statement"]), encoding="utf-8")
    p = subprocess.run(["node", str(HERE / "survey_spec.mjs"), str(root), str(pf), str(py), str(work / "home" / "specs" / "s")],
                       capture_output=True, text=True, timeout=900,
                       env={**os.environ, "STEALTHLAB_HOME": str(work / "home")})
    try:
        w = json.loads(p.stdout)
    except ValueError:
        return {**out, "error": f"writer: {p.stderr[-300:]}"}
    out.update({"written": bool(w.get("file")), "writer": w.get("model"), "ambiguous": w.get("ambiguous"), "writer_ms": w.get("ms"),
                "writer_log": [l for l in w.get("log") or []][-4:]})
    if not w.get("file"):
        return out
    spec = Path(w["file"])
    out["spec_code"] = spec.read_text(encoding="utf-8")[:60000]     # kept: stage 3's saved answers are re-judged with it offline
    before, tail_b = run_spec(py, spec, root)
    # the real fix applied IN PLACE (the project is installed editable from root: a src/ layout imports root's code), then undone
    ok, err = S.apply_patch(root, r["patch"])
    if not ok:
        return {**out, "error": f"gold patch: {err[:200]}"}
    try:
        after, tail_a = run_spec(py, spec, root)
    finally:
        # undone exactly (new files too): stage 3's agents must not see any of the real fix
        if not _apply(root, r["patch"], reverse=True):
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "--", "."], capture_output=True)
            for f in re.findall(r"^diff --git a/\S+ b/(\S+)\nnew file mode", r["patch"], re.M):
                (root / f).unlink(missing_ok=True)
    out.update({"fails_before": before != 0, "passes_with_fix": after == 0, "valid": before != 0 and after == 0,
                "rejects_right_answer": after != 0, "tail_with_fix": _why(tail_a) if after != 0 else ""})
    return out


def _why(out: str) -> str:
    """pytest's own verdict on a failing run -- its FAILED / ERROR lines, the error lines and the summary -- not the warnings
    printed after it (a trailing deprecation warning hid the real reason in 3 of 11 runs)."""
    keep = [l for l in out.splitlines() if re.match(r"(FAILED|ERROR) |E  |\S*Error\b|=+ .*(failed|error|passed)|\d+ (failed|error)", l)]
    return "\n".join(keep[-12:])[-1200:] or out[-400:]


def report2(rows: list[dict]) -> str:
    env_bad = [r for r in rows if r.get("env_error")]
    ok = [r for r in rows if not r.get("env_error") and not r.get("error")]
    w = [r for r in ok if r.get("written")]
    def pct(xs, k):
        return f"{sum(1 for x in xs if x.get(k)) / max(1, len(xs)) * 100:.0f}%"
    lines = [f"{len(ok)} issues checked ({len(env_bad)} environment could not be built, {len(rows) - len(ok) - len(env_bad)} errors)", "",
             f"{'':12} {'n':>3} {'written':>8} {'valid':>6} {'fails before':>13} {'rejects a right answer':>23}"]
    for name, xs in (("all", ok), ("single-file", [x for x in ok if x["n_gold"] == 1]), ("multi-file", [x for x in ok if x["n_gold"] > 1])):
        ws = [x for x in xs if x.get("written")]
        lines.append(f"{name:12} {len(xs):>3} {pct(xs, 'written'):>8} {pct(ws, 'valid'):>6} {pct(ws, 'fails_before'):>13} {pct(ws, 'rejects_right_answer'):>23}")
    lines.append(f"\nwriters: " + ", ".join(f"{m}={sum(1 for x in w if x.get('writer') == m)}" for m in sorted({x.get('writer') for x in w}, key=str)))
    for x in [x for x in w if x.get("rejects_right_answer")][:15]:
        lines.append(f"  rejects the real fix: {x['instance_id']}: {x['tail_with_fix'][-160:].strip()}")
    return "\n".join(lines)


def report(rows: list[dict]) -> str:
    ok = [r for r in rows if "error" not in r]
    def pct(xs, k):
        return f"{sum(1 for x in xs if x.get(k)) / max(1, len(xs)) * 100:.0f}%"
    lines = [f"{len(ok)} issues ({len(rows) - len(ok)} could not be checked out)", "",
             f"{'':12} {'n':>3} {'file@1':>7} {'file@3':>7} {'spot':>6} {'symbol':>7} {'empty':>6}"]
    for name, xs in (("all", ok), ("single-file", [x for x in ok if x["n_gold"] == 1]), ("multi-file", [x for x in ok if x["n_gold"] > 1])):
        lines.append(f"{name:12} {len(xs):>3} {pct(xs, 'file_at_1'):>7} {pct(xs, 'file_at_3'):>7} {pct(xs, 'spot'):>6} {pct(xs, 'symbol'):>7} {pct(xs, 'empty'):>6}")
        nx = [x["narrowed"] for x in xs if x.get("narrowed")]
        if nx:
            lines.append(f"{'  +narrow':12} {len(nx):>3} {pct(nx, 'file_at_1'):>7} {pct(nx, 'file_at_3'):>7} {pct(nx, 'spot'):>6} {pct(nx, 'symbol'):>7} {pct(nx, 'empty'):>6}")
    misses = [x for x in ok if not x["file_at_3"]]
    lines += ["", f"misses (no real-fix file in the top 3): {len(misses)}"]
    for x in misses[:30]:
        lines.append(f"  {x['instance_id']}: gold {x['gold_files'][:2]} | located {x['located']} | terms {x['terms'][:6]}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "select":
        n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 48
        seed = int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 1
        print("\n".join(select(n, seed)))
    elif cmd == "stage1":
        for iid in argv[1:]:
            print(json.dumps(stage1(iid)), flush=True)
    elif cmd == "report":
        print(report([json.loads(l) for l in open(argv[1], encoding="utf-8") if l.strip()]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


# ---------------------------------------------------------------- stage 3: the cheap agent, judged by our gate and by the truth

def _apply(root: Path, patch: str, reverse: bool = False) -> bool:
    if not patch.strip():
        return True
    p = subprocess.run(["git", "-C", str(root), "apply", "--whitespace=nowarn", *(["-R"] if reverse else []), "-"],
                       input=patch.encode(), capture_output=True)
    return p.returncode == 0


def hidden_pass(py: Path, root: Path, test_patch: str, f2p: list[str]) -> tuple[bool, str]:
    """The real hidden tests (the merged PR's): FAIL_TO_PASS must pass. As the SWE-bench harness grades: the files the test
    patch touches are first put back as they were at the base commit (an answer may have edited the same test file, as a
    developer would), then the test patch is applied; afterwards the answer's own versions are restored exactly.
    Before this, an answer that had edited a test file the hidden tests also change was scored wrong without being
    tested: 42 of 107 graded answers (survey-s3-ch1/ch2 + s4, 2026-10-16)."""
    import swe_series_task as S
    touched = sorted(set(re.findall(r"^diff --git a/\S+ b/(\S+)", test_patch or "", re.M)))
    saved = {f: ((root / f).read_bytes() if (root / f).exists() else None) for f in touched}
    for f in touched:
        tracked = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"HEAD:{f}"], capture_output=True).returncode == 0
        if tracked:
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "HEAD", "--", f], capture_output=True)
        else:
            (root / f).unlink(missing_ok=True)
    try:
        if not _apply(root, test_patch):
            return False, "test patch does not apply (even on the base versions of its files)"
        code, out = S.TOOLCHAIN.pytest(py, ["-q", "-p", "no:cacheprovider", *f2p], root, 600)
        return code == 0, out[-600:]
    finally:
        for f, data in saved.items():
            if data is None:
                (root / f).unlink(missing_ok=True)
            else:
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).write_bytes(data)


LAST_EXISTING: dict = {}                   # the last existing_fails run: how many of the ids it saw, and the output's tail


def existing_fails(py: Path, root: Path, p2p: list[str]) -> set[str] | None:
    """The project's own existing tests (the dataset's PASS_TO_PASS: they pass before and after the real fix) -> the ids
    that fail here, or None when nothing could be judged (a timeout, or none of the ids ran). A real project has these
    tests, so whether an answer keeps them passing is a signal an acceptance rule may use; judged against the unchanged
    code, so a test this environment breaks anyway is not counted against an answer.
    Run by FILE and read back per id (-rA): pytest given test ids stops the whole run when one id is not found (exit 4),
    which on 9 of 25 issues left the signal empty (survey-s4-ch1, 2026-10-16)."""
    import swe_series_task as S
    LAST_EXISTING.clear()
    if not p2p:
        return set()
    files = sorted({t.split("::")[0] for t in p2p if (root / t.split("::")[0]).exists()})
    if not files:
        LAST_EXISTING.update({"seen": 0, "tail": "none of the test files exist"})
        return None
    code, out = S.TOOLCHAIN.pytest(py, ["-q", "-rA", "-p", "no:cacheprovider", *files], root, 900)
    seen = {m.group(2).strip(): m.group(1) for m in re.finditer(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (.+?)(?: - .*)?$", out, re.M)}
    want = set(p2p)
    got = {t: v for t, v in seen.items() if t in want}
    LAST_EXISTING.update({"seen": len(got), "exit": code, "tail": out[-600:]})
    if code == 124 or not got:
        return None
    return {t for t, v in got.items() if v in ("FAILED", "ERROR")}


def stage3(iid: str, work: Path, agents: tuple[str, ...] = ("claude", "stealth"), model: str = "glm-5.3-flash") -> dict:
    import swe_series_task as S
    base = stage2(iid, work)                      # environment, our tests (and their validity), the where line
    if base.get("env_error") or base.get("error"):
        return base
    full = S.load_split()
    full = full[full.instance_id == iid].iloc[0]
    root, py = work / "repo", next((work / "venvs").glob(f"{iid}*/bin/python"), None) or next((work / "venvs").glob(f"{iid}*/Scripts/python.exe"))
    spec = next(iter(sorted((work / "home" / "specs" / "s").glob("test_spec.py"))), None)
    where = work / "home" / "specs" / "s" / "where.txt"
    f2p = list(full["FAIL_TO_PASS"])
    out = {**base, "agents": {}, "spec_code": spec.read_text(encoding="utf-8")[:60000] if spec else None,
           "where": where.read_text(encoding="utf-8") if where.exists() else ""}
    env = {**os.environ, "STEALTHLAB_HOME": str(work / "home"),
           "PATH": f"{py.parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    p2p = list(full["PASS_TO_PASS"])
    base_fail = existing_fails(py, root, p2p)
    out["existing"] = {"n": len(p2p), "fail_on_unchanged": None if base_fail is None else len(base_fail),
                       "seen": LAST_EXISTING.get("seen"), "tail": None if base_fail is not None else LAST_EXISTING.get("tail")}
    for agent in agents:
        p = subprocess.run(["node", str(HERE / "survey_agent.mjs"), str(root), str(work / "prompt.txt"), str(where), agent, model],
                           capture_output=True, text=True, timeout=1500, env=env)
        try:
            a = json.loads(p.stdout)
        except ValueError:
            out["agents"][agent] = {"error": p.stderr[-300:]}
            continue
        res = {k: a.get(k) for k in ("state", "files", "secs", "cost_usd", "tokens", "error")}
        patch = a.get("patch") or ""
        res["patch"] = patch[:200000]       # kept: any later test writer or acceptance rule is judged on these same answers
        if patch.strip() and _apply(root, patch):
            try:
                res["passes_our_tests"] = (run_spec(py, spec, root)[0] == 0) if spec else None
                res["truly_solved"], res["hidden_tail"] = hidden_pass(py, root, str(full["test_patch"]), f2p)
                after = existing_fails(py, root, p2p) if base_fail is not None else None
                res["existing_new_failures"] = None if after is None else len(after - base_fail)
            finally:
                _apply(root, patch, reverse=True)
        else:
            res.update({"passes_our_tests": False if spec else None, "truly_solved": False, "no_change": not patch.strip()})
        out["agents"][agent] = res
    return out


def stage4(iid: str, work: Path, prior: dict) -> dict:
    """Re-judging a finished stage 3 issue's SAVED answers with no model call: the environment rebuilt, each answer's
    patch applied, the project's existing tests (existing_new_failures) and the hidden tests run -- for the runs that
    were made before stage 3 recorded the existing tests."""
    import swe_series_task as S
    r = record(iid)
    full = S.load_split()
    full = full[full.instance_id == iid].iloc[0]
    r["install_config"], r["requirements"] = full["install_config"], full["requirements"]
    root = work / "repo"
    try:
        checkout(r, root)
        py = environment(r, root, work / "venvs")
    except Exception as e:  # noqa: BLE001
        return {"instance_id": iid, "env_error": str(e)[:300]}
    p2p, f2p = list(full["PASS_TO_PASS"]), list(full["FAIL_TO_PASS"])
    base_fail = existing_fails(py, root, p2p)
    out = {"instance_id": iid, "existing": {"n": len(p2p), "fail_on_unchanged": None if base_fail is None else len(base_fail),
                                            "seen": LAST_EXISTING.get("seen"),
                                            "tail": None if base_fail is not None else LAST_EXISTING.get("tail")}, "agents": {}}
    # the real fix, graded the same way: where it does not pass the hidden tests here (a missing test dependency, a test
    # unrelated to the issue), the issue says nothing about any answer and is left out of the scoring
    out["gold_solved"], out["gold_tail"] = False, "the real fix does not apply"
    if _apply(root, str(full["patch"])):
        try:
            out["gold_solved"], out["gold_tail"] = hidden_pass(py, root, str(full["test_patch"]), f2p)
        finally:
            _apply(root, str(full["patch"]), reverse=True)
    for agent, a in (prior.get("agents") or {}).items():
        patch = a.get("patch") or ""
        res = {"truly_solved": False, "existing_new_failures": None}
        if patch.strip() and _apply(root, patch):
            try:
                res["truly_solved"], res["hidden_tail"] = hidden_pass(py, root, str(full["test_patch"]), f2p)
                after = existing_fails(py, root, p2p) if base_fail is not None else None
                res["existing_new_failures"] = None if after is None else len(after - base_fail)
            finally:
                _apply(root, patch, reverse=True)
        out["agents"][agent] = res
    return out


def report4(rows: list[dict]) -> str:
    lines = [f"{len(rows)} issues re-judged"]
    for agent in ("claude", "stealth"):
        xs = [r["agents"][agent] for r in rows if agent in (r.get("agents") or {})]
        keep = [x for x in xs if x.get("existing_new_failures") == 0]
        lines.append(f"agent {agent}: {len(xs)} answers; keep the existing tests passing {len(keep)} "
                     f"(of them truly solved {sum(1 for x in keep if x['truly_solved'])}); "
                     f"truly solved but break existing tests {sum(1 for x in xs if x['truly_solved'] and (x.get('existing_new_failures') or 0) > 0)}")
    return "\n".join(lines)


def report3(rows: list[dict]) -> str:
    ok = [r for r in rows if r.get("agents")]
    lines = [f"{len(ok)} issues with a working environment", ""]
    # a run that never reached the model (its endpoint refused every try: no tokens, no change) says nothing about the
    # agent; every other run counts, an empty diff or a stall included (the runtime sets "error" on those too)
    unreached = lambda x: x.get("state") == "error" or (not x.get("files") and (
        str(x.get("error") or "").startswith("the executor failed") or (x.get("tokens") or {}).get("in") in (0, None)))
    for agent in ("claude", "stealth"):
        runs = [r["agents"][agent] for r in ok if agent in r["agents"]]
        xs = [x for x in runs if not unreached(x)]
        if len(xs) < len(runs):
            lines.append(f"agent {agent}: {len(runs) - len(xs)} runs never reached the model (left out below)")
        if not xs:
            continue
        solved = [x for x in xs if x.get("truly_solved")]
        gated = [x for x in xs if x.get("passes_our_tests")]
        rejected_right = [x for x in solved if x.get("passes_our_tests") is False]
        accepted_wrong = [x for x in gated if not x.get("truly_solved")]
        secs = sorted(x.get("secs") or 0 for x in xs)
        cost = sum(float(x.get("cost_usd") or 0) for x in xs)
        lines += [f"agent {agent} (n={len(xs)}): truly solved {len(solved)} ({len(solved) / len(xs) * 100:.0f}%), passed our tests {len(gated)}, "
                  f"right answers our tests REJECTED {len(rejected_right)}, wrong answers our tests ACCEPTED {len(accepted_wrong)}; "
                  f"median {secs[len(secs) // 2]} s, cost ${cost:.2f} in all"]
    return "\n".join(lines)

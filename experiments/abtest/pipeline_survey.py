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
    """The issue's own Python with its pinned requirements, the project installed editable -> the venv's python."""
    import swe_series_task as S
    cfg = r.get("install_config") or {}
    py = S.TOOLCHAIN.venv(venvs / r["instance_id"], str(cfg.get("python") or "3.11"))
    S.TOOLCHAIN.install(py, S.clean_requirements(r.get("requirements")) + ["pytest"])
    S.TOOLCHAIN.install_editable(py, root)
    return py


def run_spec(py: Path, spec: Path, cwd: Path) -> tuple[int, str]:
    import swe_series_task as S
    return S.TOOLCHAIN.pytest(py, ["-q", "-x", "-p", "no:cacheprovider", str(spec)], cwd, 300)


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
    before, tail_b = run_spec(py, spec, root)
    gold_tree = work / "gold"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "--detach", str(gold_tree), "HEAD"], check=True, capture_output=True)
    ok, err = S.apply_patch(gold_tree, r["patch"])
    if not ok:
        return {**out, "error": f"gold patch: {err[:200]}"}
    after, tail_a = run_spec(py, spec, gold_tree)
    out.update({"fails_before": before != 0, "passes_with_fix": after == 0, "valid": before != 0 and after == 0,
                "rejects_right_answer": after != 0, "tail_with_fix": tail_a[-400:] if after != 0 else ""})
    return out


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

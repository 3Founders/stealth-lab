"""regrade_from_pr: grade a series run's cheap answers without Modal or the dataset, from the upstream PR itself.

Like SWE-bench: the base commit + the answer, then the PR's test files on top; an answer is correct when every test
that passes with the real fix (the PR) passes with it -- the PR's new tests (FAIL_TO_PASS) included. A failing subtest
fails its test (pytest still lists the test PASSED). A run in which no test passes with the real fix grades nothing.

    git clone --filter=blob:none https://github.com/<owner>/<repo> R && git -C R fetch origin pull/<n>/head:pr
    python regrade_from_pr.py R <base commit> pr <python with the project's deps> <results.jsonl> [...]

ct-series-v6 (2026-10-18): jsonargparse-502 base 6d4f87bb, sqlglot-4051 base d3ee5ea6.
"""
import json, re, subprocess, sys
repo, base, pr, py, files = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
sh = lambda *a, **k: subprocess.run(a, cwd=repo, capture_output=True, text=True, **k)
tests = [f for f in sh("git", "diff", "--name-only", base, pr).stdout.split() if re.search(r"(^|/)(tests?|testing|\w*_tests)/|test_\w*\.py$", f)]
def run():
    out = sh(py, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "--continue-on-collection-errors", *sorted({t.split("/")[0] for t in tests}), timeout=1800).stdout
    got = {m.group(2): m.group(1) for m in re.finditer(r"^(PASSED|FAILED|ERROR) (\S+)", out, re.M)}
    for m in re.finditer(r"^SUBFAIL(?:ED)?\[.*?\] (\S+)", out, re.M):    # a failing subtest fails its test
        got[m.group(1)] = "FAILED"
    return got
def reset():
    sh("git", "checkout", "-q", "-f", base); sh("git", "clean", "-qfd")
def with_tests():
    sh("git", "checkout", pr, "--", *tests)
reset(); sh("git", "checkout", pr, "--", "."); gold = run()
reset(); with_tests(); before = run()
need = sorted(t for t, v in gold.items() if v == "PASSED")
f2p = sorted(t for t in need if before.get(t) != "PASSED")
if not need:
    sys.exit("no test passes with the real fix here: the environment cannot run the tests, nothing is graded")
print(f"tests: {len(need)} pass with the real fix; FAIL_TO_PASS (fail before it): {f2p}")
for f in files:
    for line in open(f):
        r = json.loads(line)
        for n, a in enumerate(r.get("attempts") or [], 1):
            if not (a.get("patch") or "").strip():
                continue
            reset()
            ap = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=repo, input=a["patch"], capture_output=True, text=True)
            if ap.returncode:
                print(f"{r['task_id']} {r['arm']} {a['model']}: patch does not apply ({ap.stderr.strip()[:120]})"); continue
            with_tests()
            got = run()
            broken = [t for t in need if got.get(t) != "PASSED"]
            verdict = "CORRECT" if not broken else f"wrong ({len(broken)} failing, e.g. {broken[:3]})"
            print(f"{r['task_id']} {r['arm']} {a['model']} (state {a.get('state')}, ${a.get('cost_usd')}): {verdict}")
reset()

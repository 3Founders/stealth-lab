"""Offline tests for the local-vs-global evaluation harness (no network, no LLM, no database).

    cd experiments/local_eval && .venv/Scripts/python -m pytest tests -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze  # noqa: E402
import blind  # noqa: E402
import build_notes  # noqa: E402
import build_tasks  # noqa: E402
import grade_tests  # noqa: E402
import history  # noqa: E402
import stats  # noqa: E402

MEM = {"history_window_days": 3650, "history_max_commits": 5000, "library_entries": 3, "library_diffs": 2,
       "diff_max_chars": 2400}


# ---------------------------------------------------------------- git fixture
def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"]}
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env).stdout


def _commit(repo: Path, path: str, text: str, msg: str, date: str) -> str:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, date=date)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path: Path) -> dict:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    c = {}
    c["init"] = _commit(r, "package.json", json.dumps({"scripts": {"test": "jest", "start": "node x"}}),
                        "initial", "2024-01-01T00:00:00")
    c["old_fix"] = _commit(r, "src/parser.js", "parse v1", "fix: parser crashes on empty token list (#11)",
                           "2024-02-01T00:00:00")
    c["doc_fix"] = _commit(r, "docs/guide.md", "typo", "fix typo in docs", "2024-02-02T00:00:00")
    c["feature"] = _commit(r, "src/feature.js", "feat", "add colour output", "2024-02-03T00:00:00")
    c["base"] = _commit(r, "src/other.js", "x", "refactor other module", "2024-03-01T00:00:00")
    c["future_fix"] = _commit(r, "src/parser.js", "parse v2", "fix: parser token list bug (the task's own fix) #42",
                              "2024-04-01T00:00:00")
    return {"path": r, **c}


# ---------------------------------------------------------------- local tier
def test_local_tier_reads_only_ancestors_of_base(repo):
    ctx = history.local_context(repo["path"], repo["base"], "parser crashes when the token list is empty", MEM)
    shas = {e.sha for e in ctx.library}
    assert repo["old_fix"] in shas
    assert repo["future_fix"] not in shas, "a commit after base_commit leaked into the local tier"
    assert repo["doc_fix"] not in shas, "doc-only fixes are not library entries"
    assert repo["feature"] not in shas, "non-fix commits are not library entries"
    top = ctx.library[0]
    assert top.sha == repo["old_fix"] and "parse v1" in top.diff


def test_claims_stub_uses_only_the_tree(repo):
    facts = history.claims_stub(repo["path"], repo["base"])
    joined = "\n".join(facts)
    assert "test = jest" in joined and "start" not in joined.split("scripts:")[1]
    assert "manifests: package.json" in joined


def test_bm25_prefers_matching_document():
    s = history.bm25_rank("parser crashes on empty token list", ["fix parser empty token list", "update colour docs"])
    assert s[0] > s[1] == 0


@pytest.mark.skipif(not __import__("os").environ.get(history.SURVEY_ENV) or not __import__("shutil").which("node"),
                    reason="workstream C's scanner not configured (LOCAL_EVAL_SURVEY_MJS) or node missing")
def test_survey_provider_reads_only_ancestors_of_base(repo):
    ctx = history.local_context(repo["path"], repo["base"], "parser crashes when the token list is empty",
                                {**MEM, "history_max_commits": 100}, provider="survey")
    assert ctx.claims, "the scanner wrote no facts"
    subjects = " ".join(e.subject for e in ctx.library)
    assert "the task's own fix" not in subjects, "a commit after base_commit leaked into the local tier"
    assert ctx.library and "parser" in ctx.library[0].subject and "parse v1" in ctx.library[0].diff
    assert not (repo["path"] / ".stealth").exists(), "the survey must run in a throwaway worktree"


def test_parse_survey_files(tmp_path):
    s = tmp_path / ".stealth"
    (s / "library" / "solutions").mkdir(parents=True)
    (s / "claims.md").write_text("CLAIM|R-001|current|test|repository|run tests with jest|source=package.json:3#sha=1\n"
                                 "CLAIM|R-002|stale|build|repository|old|source=x:1\n")
    (s / "library.md").write_text("GOAL|L-1|fix parser crash|unit=.|g=-|outcome=historical|commit=abc\n"
                                  "PROC|L-1.p1|fix parser crash|p=-|solution=solutions/L-1.diff|touches=src/p.js#sha=1\n")
    assert history.parse_claims(s) == ["[test] run tests with jest (package.json:3)"]
    (e,) = history.parse_library(s)
    assert e.sha == "abc" and e.files == ["src/p.js"] and e.solution == "solutions/L-1.diff"


def test_unknown_or_unmerged_provider_refuses(monkeypatch):
    monkeypatch.delenv(history.SURVEY_ENV, raising=False)
    with pytest.raises(NotImplementedError):
        history.local_context(Path("."), "HEAD", "x", MEM, provider="survey")
    with pytest.raises(ValueError):
        history.local_context(Path("."), "HEAD", "x", MEM, provider="nope")


# ---------------------------------------------------------------- global tiers
def _corpus(rows, dates, X):
    c = build_notes.Corpus.__new__(build_notes.Corpus)
    c.rows, c.X = rows, np.asarray(X, np.float32)
    c.dates = np.array([dates.get(r.get("row_ref") or "", "") for r in rows])
    c.repo = [build_notes.repo_of(r.get("row_ref")) for r in rows]
    c.org = np.array([build_notes.org_of(x) or "" for x in c.repo])
    c.patches = {}
    return c


def test_tiers_are_time_ordered_and_split_by_org():
    rows = [{"gid": "g1", "name": "a", "row_ref": "acme__app-1"},     # same repo, earlier
            {"gid": "g2", "name": "b", "row_ref": "acme__lib-2"},     # same org, earlier
            {"gid": "g3", "name": "c", "row_ref": "acme__app-9"},     # the task itself
            {"gid": "g4", "name": "d", "row_ref": "acme__app-10"},    # same repo, LATER than the task
            {"gid": "g5", "name": "e", "row_ref": "other__x-3"},      # other org, earlier
            {"gid": "g6", "name": "f", "row_ref": None}]              # undated
    dates = {"acme__app-1": "2024-08-01 00:00:00", "acme__lib-2": "2024-08-02 00:00:00",
             "acme__app-9": "2024-09-01 00:00:00", "acme__app-10": "2024-10-01 00:00:00",
             "other__x-3": "2024-08-03 00:00:00"}
    c = _corpus(rows, dates, np.eye(6))
    task = {"instance_id": "acme__app-9", "repo": "Acme/app", "created_at": "2024-09-01 00:00:00"}
    ent = {rows[i]["gid"] for i in np.flatnonzero(c.candidates(task, "enterprise"))}
    glo = {rows[i]["gid"] for i in np.flatnonzero(c.candidates(task, "global"))}
    assert ent == {"g1", "g2"}
    assert glo == {"g5"}


def test_goal_already_in_local_library_is_not_repeated():
    rows = [{"gid": "g1", "name": "a", "row_ref": "acme__app-11"}, {"gid": "g2", "name": "b", "row_ref": "acme__app-12"}]
    dates = {"acme__app-11": "2024-01-01 00:00:00", "acme__app-12": "2024-01-02 00:00:00"}
    c = _corpus(rows, dates, [[1, 0], [0.9, 0.1]])
    task = {"instance_id": "acme__app-99", "repo": "acme/app", "created_at": "2024-09-01 00:00:00"}
    mem = {"global_entries": 3, "global_diffs": 2}
    picked = build_notes.pick_goals(c, np.array([1.0, 0.0], np.float32), task, "enterprise", {11}, mem)
    assert [g["gid"] for g in picked] == ["g2"]


def test_tier_text_is_capped():
    goals = [{"name": f"goal {k}", "proc": "p", "steps": ["s"] * 8, "diff": "+x\n" * 2000} for k in range(3)]
    assert len(build_notes.fmt_goals("## T", goals, 4000)) <= 4000


# ---------------------------------------------------------------- task selection
def test_selection_rules():
    sp = {"model_cutoff": "2024-07-01", "min_history": 2, "max_per_repo": 2, "n_test": 100, "n_calibration": 1,
          "seed": "s"}
    hist = {"a/x": ["2024-01-01", "2024-02-01", "2024-08-01"], "b/y": ["2024-01-01", "2024-02-01"],
            "c/z": ["2024-01-01"]}

    def row(i, repo, created):
        return {"instance_id": i, "repo": repo, "created_at": created, "problem_statement": "p",
                "FAIL_TO_PASS": ["t"], "image_name": "img"}
    rows = [row("x1", "a/x", "2024-07-15"), row("x2", "a/x", "2024-09-01"), row("x3", "a/x", "2024-10-01"),
            row("x0", "a/x", "2024-06-01"),                       # before cutoff
            row("y1", "b/y", "2024-08-01"), row("z1", "c/z", "2024-08-01"),   # z: too little history
            row("seen", "a/x", "2024-09-02")]
    test, calib, rep = build_tasks.select(rows, hist, {"seen"}, sp)
    chosen = set(test) | set(calib)
    assert not chosen & {"x0", "z1", "seen"}
    assert "x3" not in chosen, "max_per_repo keeps the earliest tasks"
    assert len(calib) == 1
    calib_repo = {"x1": "a/x", "x2": "a/x", "y1": "b/y"}[calib[0]]
    assert all({"x1": "a/x", "x2": "a/x", "y1": "b/y"}[t] != calib_repo for t in test), "calibration repos are never scored"


# ---------------------------------------------------------------- test grading
def test_resolution_rule(monkeypatch):
    src = {"repo": "o/r", "install_config": {}, "FAIL_TO_PASS": ["t1"], "PASS_TO_PASS": ["t2"]}
    monkeypatch.setattr(grade_tests, "parser_for", lambda cfg, repo: lambda log: dict(
        line.split() for line in log.strip().splitlines()))
    ok = ">>>>> PATCH_APPLIED\n>>>>> START_TESTS\nt1 PASSED\nt2 PASSED\n>>>>> END_TESTS"
    broke = ok.replace("t2 PASSED", "t2 FAILED")
    assert grade_tests.evaluate(ok, src)["resolved"] is True
    assert grade_tests.evaluate(broke, src)["resolved"] is False
    assert grade_tests.evaluate(">>>>> PATCH_FAILED", src)["status"] == "patch_failed"
    assert grade_tests.evaluate("container died", src)["status"] == "error"
    assert grade_tests.grade_one(src, "  ", "docker", 10)["status"] == "empty_patch"


def test_script_applies_test_patch_after_model_patch_and_hides_gold():
    src = {"repo": "o/name", "base_commit": "abc", "install_config": {"test_cmd": "pytest -q"},
           "patch": "GOLD-SECRET", "test_patch": "diff --git a/tests/t.py b/tests/t.py\n+x"}
    sh = grade_tests.script(src, "diff --git a/m.py b/m.py\n+y")
    assert "cd /name" in sh and "pytest -q" in sh and "tests/t.py" in sh
    assert "GOLD-SECRET" not in sh and grade_tests._b64("GOLD-SECRET") not in sh
    assert sh.index("model.patch") < sh.index("/tmp/test.patch >/dev/null")


# ---------------------------------------------------------------- blinding
def test_blind_order_is_a_stable_permutation():
    a, b = blind.order("o__r-1"), blind.order("o__r-1")
    assert a == b and sorted(a) == sorted(blind.ARMS)
    assert len({tuple(blind.order(f"o__r-{k}")) for k in range(40)}) > 1


def test_proposal_scrubs_arm_identifying_words():
    p = blind.proposal({"patch": "+x", "final_message": "Per Kel's library.md and .stealth notes, the cause is X."})
    assert "Kel" not in p and "library.md" not in p and ".stealth" not in p and "the cause is X" in p


def test_make_and_collect_roundtrip(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    ids = [f"o__r-{k}" for k in range(3)]
    (runs / "instances.json").write_text(json.dumps({i: {"repo": "o/r", "language": "python",
                                                         "problem_statement": "issue"} for i in ids}))
    (runs / "grading_source.json").write_text(json.dumps({i: {"patch": "+gold"} for i in ids}))
    (runs / "design.json").write_text(json.dumps({"test": ids}))
    for arm in blind.ARMS:
        (runs / f"attempts_test_{arm}.jsonl").write_text(
            "\n".join(json.dumps({"instance_id": i, "patch": f"+{arm}-patch", "final_message": ""}) for i in ids))
    monkeypatch.setattr(blind, "RUNS", runs)
    monkeypatch.setattr(blind, "BLIND", runs / "blind")
    blind.make("test", 8)
    text = (runs / "blind" / "batch01.md").read_text()
    for arm in blind.ARMS:
        assert f"Proposal {arm}" not in text
    key = json.loads((runs / "blind" / "key.json").read_text())
    grades = {i: {s: {"root_cause_right": key[i][s] == "L1", "score": 5, "accept": False} for s in key[i]} for i in ids}
    (runs / "blind" / "grades01.json").write_text(json.dumps(grades))
    blind.collect()
    rc = json.loads((runs / "right_cause.json").read_text())["primary"]
    assert all(rc[i]["L1"]["root_cause_right"] and not rc[i]["A0"]["root_cause_right"] for i in ids)


# ---------------------------------------------------------------- statistics
def test_mcnemar_exact_known_values():
    assert stats.mcnemar_exact(0, 6) == pytest.approx(2 * 0.5 ** 6)
    assert stats.mcnemar_exact(5, 5) == 1.0
    assert stats.mcnemar_exact(0, 0) == 1.0


def test_holm_and_bh():
    h = stats.holm({"a": 0.01, "b": 0.04})
    assert h == {"a": 0.02, "b": 0.04}
    q = stats.benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.5})
    assert q["a"] == pytest.approx(0.03) and q["b"] == pytest.approx(0.06) and q["c"] == pytest.approx(0.5)


def test_wilson_and_cluster_methods():
    lo, hi = stats.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    d = np.zeros(40)
    cl = [f"r{k % 8}" for k in range(40)]
    assert stats.cluster_signflip_p(d, cl) == 1.0
    m, lo, hi = stats.cluster_bootstrap_ci(np.ones(40), cl)
    assert m == lo == hi == 1.0


def test_sample_size_matches_formula():
    assert 300 <= stats.mcnemar_sample_size(0.25, 0.08) <= 310


def test_analysis_end_to_end(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    rng = np.random.default_rng(1)
    ids = [f"o{k % 10}__r-{k}" for k in range(60)]
    (runs / "instances.json").write_text(json.dumps({i: {"repo": i.split("__")[0] + "/r", "language": "python",
                                                         "history_goals": 20} for i in ids}))
    (runs / "design.json").write_text(json.dumps({"test": ids}))
    rc = {}
    for arm, p in zip(analyze.ARMS, (0.2, 0.45, 0.45, 0.5)):
        res = rng.random(60) < p
        (runs / f"attempts_test_{arm}.jsonl").write_text("\n".join(json.dumps(
            {"instance_id": i, "usage": {"prompt_tokens": 1000, "completion_tokens": 100}, "steps": 10})
            for i in ids))
        (runs / f"tests_test_{arm}.json").write_text(json.dumps(
            {i: {"resolved": bool(r), "status": "graded"} for i, r in zip(ids, res)}))
        for i, r in zip(ids, res):
            rc.setdefault(i, {})[arm] = {"root_cause_right": bool(r), "score": 5.0, "accept": bool(r)}
    (runs / "right_cause.json").write_text(json.dumps({"primary": rc, "second": {}}))
    monkeypatch.setattr(analyze, "RUNS", runs)
    out = analyze.run("test")
    assert set(out["primary"]) == {"resolved:L1_vs_A0", "root_cause_right:L1_vs_A0"}
    assert all("p_holm" in v for v in out["primary"].values())
    assert "resolved:L3_vs_L2" in out["secondary"]
    text = analyze.report(out)
    assert "## Primary" in text and "L1_vs_A0" in text


# ---------------------------------------------------------------- task validity
def test_validity_rule():
    import valid_tasks as vt
    ok, bad = {"status": "graded", "resolved": True}, {"status": "graded", "resolved": False}
    err = {"status": "error", "resolved": False}
    assert vt.classify(ok, bad) == "valid"
    assert vt.classify(bad, bad) == "gold_fails"
    assert vt.classify(ok, ok) == "empty_passes"
    assert vt.classify(err, bad) == vt.classify(ok, err) == "environment_error"
    assert vt.classify(None, bad) == "not_checked"


def test_analysis_uses_only_valid_tasks(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    ids = [f"o{k % 5}__r-{k}" for k in range(20)]
    (runs / "instances.json").write_text(json.dumps({i: {"repo": i.split("__")[0] + "/r", "language": "python",
                                                         "history_goals": 20} for i in ids}))
    (runs / "design.json").write_text(json.dumps({"test": ids}))
    (runs / "valid_tasks.json").write_text(json.dumps({"valid": ids[:12]}))
    for arm in analyze.ARMS:
        (runs / f"attempts_test_{arm}.jsonl").write_text("\n".join(json.dumps({"instance_id": i, "usage": {}})
                                                                    for i in ids))
        (runs / f"tests_test_{arm}.json").write_text(json.dumps({i: {"resolved": True, "status": "graded"}
                                                                 for i in ids}))
    monkeypatch.setattr(analyze, "RUNS", runs)
    assert analyze.run("test", "valid")["n_scored"] == 12
    assert analyze.run("test", "design")["n_scored"] == 20

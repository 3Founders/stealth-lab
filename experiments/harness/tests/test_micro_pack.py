"""End-to-end micro-pack run: 11 scenarios x 3 arms through the real runner,
graded per scenario, plus scoreboard integration and resume behavior.

The hand-derived expectation matrix below IS the fixture contract: arms are
scripted deterministically over fixtures/micro, so any drift here is a
fixture/mechanism regression, not noise. Outcomes remain pipeline exercises,
NOT findings.
"""
import json

import run_micro_pack
from conftest import MICRO_FIXTURES

# scenario_pass per [A, B, C], derived by hand from the scripted-arm policy
# in scripted_arms.py + the criteria in scenarios.json:
PASS_MATRIX = {
    "mic-refund-001": [True, False, True],    # B misled by desk lore
    "mic-refund-002": [False, False, True],   # C-only rescue via review-v2
    "mic-refund-003": [True, True, False],    # poisoned gate trips C (honest)
    "mic-dep-001": [False, True, True],       # RAG rescue; C reuse+trail
    "mic-dep-002": [True, False, True],       # pin-bump trap catches B
    "mic-dep-003": [False, False, False],     # unseen, nobody has anything
    "mic-pdf-001": [False, True, True],
    "mic-pdf-002": [True, True, True],
    "mic-pdf-003": [False, False, True],      # full gauntlet: only C survives
    "mic-env-001": [True, True, True],        # pure staleness showcase
    "mic-env-002": [False, True, True],
}
FALSE_REUSE = {"B": 3, "C": 1}   # B: refund lore, dep pin-bump, pdf v1 fragment


def _run(tmp_path, extra=None):
    out = tmp_path / "micro_results.jsonl"
    argv = ["--out", str(out)] + (extra or [])
    rc = run_micro_pack.main(argv)
    return rc, out


class TestMicroPackEndToEnd:
    def test_full_run_matches_hand_derived_matrix(self, tmp_path):
        rc, out = _run(tmp_path)
        assert rc == 0
        rows = {json.loads(l)["task_id"]: json.loads(l)
                for l in out.read_text(encoding="utf-8").splitlines()}
        assert set(rows) == set(PASS_MATRIX)

        detail = json.loads(
            out.with_name("micro_results_detail.json").read_text("utf-8"))
        got = {}
        for v in detail["verdicts"]:
            got[(v["scenario_id"], v["arm"])] = v["scenario_pass"]
        for sid, expected in PASS_MATRIX.items():
            for arm, exp in zip("ABC", expected):
                assert got[(sid, arm)] is exp, f"{sid}/{arm}"

        for arm, n in FALSE_REUSE.items():
            assert detail["pack"]["by_arm"][arm]["false_reuse"] == n

        # Stale-refusal opportunities must be visible wherever a stale offer
        # exists (7 of 11 scenarios): B never refuses (no gate), C refuses 6
        # and is tripped once by the poisoned gate. Guards the regression
        # where arms inherited arm A's empty stale_offered and the §40
        # headline metric read "no offers" forever.
        sb = detail["scoreboard"]["arms"]
        assert sb["B"]["stale_opportunities"] == 7
        assert sb["B"]["stale_refusals_correct"] == 0
        assert sb["C"]["stale_opportunities"] == 7
        assert (sb["C"]["stale_refusals_correct"],
                sb["C"]["stale_refusals_missed"]) == (6, 1)

        # C's evidence trail exists on every graded row it owns...
        c_rows = [r for r in rows.values() if isinstance(r.get("C"), dict)]
        assert all(r.get("C_journal") for r in c_rows)

    def test_scoreboard_footer_present_with_counts(self, tmp_path, capsys):
        rc, out = _run(tmp_path)
        text = capsys.readouterr().out
        assert rc == 0 and "POWER-ANALYSIS FOOTER" in text
        assert "MICRO PACK" in text
        for line in text.splitlines():
            if "exact-p=" in line:
                assert "discordant pairs" in line

    def test_resume_skips_completed_scenarios(self, tmp_path, capsys):
        rc1, out = _run(tmp_path)
        first = out.read_text(encoding="utf-8")
        capsys.readouterr()
        rc2, _ = _run(tmp_path)
        second = out.read_text(encoding="utf-8")
        assert rc1 == rc2 == 0
        assert first == second, "resume must not duplicate rows"
        assert "11 already done" in capsys.readouterr().out

    def test_dry_run_corpus_tasks_flow_through_all_arms(self, tmp_path):
        manifest = tmp_path / "cc_manifest.jsonl"
        manifest.write_text("\n".join(json.dumps(r) for r in [
            {"task_id": "cc-ab12cd34-L5", "kind": "main",
             "session_id": "ab12cd34", "source_path": "X:\\s.jsonl",
             "line_no": 5, "prompt_chars": 42, "ts": None,
             "unseen": True, "dry_run": True,
             "archetype": "real_session_prompt"},
            {"task_id": "cc-ef567890-L2", "kind": "subagent",
             "session_id": "ef567890", "source_path": "X:\\a.jsonl",
             "line_no": 2, "prompt_chars": 7, "ts": None,
             "unseen": True, "dry_run": True,
             "archetype": "real_session_prompt"},
        ]), encoding="utf-8")
        rc, out = _run(
            tmp_path, ["--corpus-manifest", str(manifest)])
        assert rc == 0
        rows = {json.loads(l)["task_id"]
                for l in out.read_text(encoding="utf-8").splitlines()}
        assert {"cc-ab12cd34-L5", "cc-ef567890-L2"} <= rows
        detail = json.loads(
            out.with_name("micro_results_detail.json").read_text("utf-8"))
        assert len(detail["dry_runs"]) == 2
        assert all(d["all_valid"] and set(d["arms_exercised"]) == {"A", "B", "C"}
                   for d in detail["dry_runs"])

    def test_broken_fixtures_refuse_to_run(self, tmp_path):
        broken = tmp_path / "broken"
        broken.mkdir()
        src = MICRO_FIXTURES
        for name in ("tasks.json", "procedures.json", "rag_corpus.json"):
            (broken / name).write_text(
                (src / name).read_text(encoding="utf-8"), encoding="utf-8")
        scen = {"scenarios": [{"scenario_id": "nope", "archetype": "weird",
                               "success_criteria": {},
                               "evidence_requirements": []}]}
        (broken / "scenarios.json").write_text(json.dumps(scen),
                                               encoding="utf-8")
        rc = run_micro_pack.main(["--fixtures-dir", str(broken),
                                  "--out", str(tmp_path / "o.jsonl")])
        assert rc == 2

import json
from pathlib import Path

import pytest

import scoreboard

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def episode(arm, *, resolved=False, unseen=False, reused=None, memory=None,
            refused=None, caused_failure=False, stale_offered=None):
    return {
        "task_id": "", "arm": arm, "valid": True, "invalid_reason": None,
        "resolved": resolved,
        "reused_procedure_ids": list(reused or []),
        "followed_memory_ids": list(memory or []),
        "refused_procedure_ids": list(refused or []),
        "reuse_caused_failure": caused_failure,
        "stale_offered": list(stale_offered or []),
        "tokens_in": 48000 if arm == "A" else 18000,
        "tokens_out": 6000 if arm == "A" else 3000,
        "tool_calls": 20, "latency_seconds": 30.0,
        "human_interventions": 0, "unseen_task": unseen,
    }


def build_rows():
    """Hand-computed paired scenario (see test docstrings below).

    pass pattern per task [A,B,C]:
      t1 P P P   t2 P f P   t3 f P P   t4 f f F(false-reuse)   t5 P f f(unseen)
    plus t6 whose C episode is INVALID -> excluded from the paired subset.
    """
    def row(tid, a, b, c):
        r = {"task_id": tid}
        for arm, ep in (("A", a), ("B", b), ("C", c)):
            ep["task_id"] = tid
            r[arm] = ep
        return r

    return [
        row("t1",
            episode("A", resolved=True),
            episode("B", resolved=True, refused=["pay-ach-v2"],
                    stale_offered=["pay-ach-v2"]),
            episode("C", resolved=True)),
        row("t2",
            episode("A", resolved=True),
            episode("B", resolved=False, memory=["rag-payments-1"],
                    caused_failure=True),
            episode("C", resolved=True)),
        row("t3",
            episode("A"),
            episode("B", resolved=True),
            episode("C", resolved=True)),
        row("t4",
            episode("A"),
            episode("B"),
            episode("C", resolved=False, reused=["migrate-postgres-v3"],
                    caused_failure=True,
                    stale_offered=["migrate-postgres-v3"])),
        row("t5",
            episode("A", resolved=True, unseen=True),
            episode("B", unseen=True),
            episode("C", unseen=True)),
        row("t6",
            episode("A", resolved=True),
            episode("B", resolved=True),
            dict(episode("C"), valid=False, invalid_reason="provider_error")),
    ]


@pytest.fixture()
def jsonl(tmp_path):
    path = tmp_path / "results.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in build_rows():
            f.write(json.dumps(r) + "\n")
    return path


class TestAggregation:
    def test_usable_excludes_partial_or_invalid_tasks(self, tmp_path, jsonl):
        classified = scoreboard.classify_rows(
            scoreboard.load_rows(jsonl), _procs())
        usable = scoreboard.usable_tasks(classified)
        assert [c["task_id"] for c in usable] == ["t1", "t2", "t3", "t4", "t5"]

    def test_pass_rates_match_hand_computation(self, jsonl):
        _, detail = scoreboard.build_summary([jsonl], FIXTURES)
        arms = detail["arms"]
        # A: t1,t2,t5 ; B: t1,t3 ; C: t1,t2,t3
        assert (arms["A"]["passes"], arms["A"]["pass_rate"]) == (3, 0.6)
        assert (arms["B"]["passes"], arms["B"]["pass_rate"]) == (2, 0.4)
        assert (arms["C"]["passes"], arms["C"]["pass_rate"]) == (3, 0.6)
        assert detail["n_usable"] == 5 and detail["n_tasks_total"] == 6

    def test_discordant_counts_match_hand_computation(self, jsonl):
        _, detail = scoreboard.build_summary([jsonl], FIXTURES)
        comps = {(c["first"], c["second"]): c for c in detail["comparisons"]}
        assert (comps[("A", "B")]["discordant_first_only"],
                comps[("A", "B")]["discordant_second_only"]) == (2, 1)
        # A-vs-C: t5 A-only win; t3 C-only win; t2 both pass, t4/t5... both
        # fail at t4 -> concordant.
        assert (comps[("A", "C")]["discordant_first_only"],
                comps[("A", "C")]["discordant_second_only"]) == (1, 1)
        assert (comps[("B", "C")]["discordant_first_only"],
                comps[("B", "C")]["discordant_second_only"]) == (0, 1)

    def test_false_reuse_and_stale_refusal_counts(self, jsonl):
        _, detail = scoreboard.build_summary([jsonl], FIXTURES)
        arms = detail["arms"]
        assert arms["B"]["false_reuse_count"] == 1   # t2: misleading memory
        assert arms["C"]["false_reuse_count"] == 1   # t4: poisoned gate
        assert arms["B"]["reuse_count"] >= 1
        b, c = arms["B"], arms["C"]
        assert (b["stale_opportunities"], b["stale_refusals_correct"],
                b["stale_refusals_missed"]) == (1, 1, 0)
        assert (c["stale_opportunities"], c["stale_refusals_correct"],
                c["stale_refusals_missed"]) == (1, 0, 1)

    def test_unseen_slice_and_costs(self, jsonl):
        _, detail = scoreboard.build_summary([jsonl], FIXTURES)
        arms = detail["arms"]
        assert arms["A"]["unseen_n"] == 1 and arms["A"]["unseen_passes"] == 1
        assert arms["C"]["unseen_passes"] == 0
        # A episodes are 48k/6k at $2.50/$10 per Mtok -> $0.18 each; all five
        # usable tasks carry a valid A episode.
        assert arms["A"]["total_cost_usd"] == pytest.approx(0.90)


class TestRenderedText:
    def test_no_bare_p_values(self, jsonl):
        text, _ = scoreboard.build_summary([jsonl], FIXTURES)
        assert "POWER-ANALYSIS FOOTER" in text
        for line in text.splitlines():
            if "exact-p=" in line or line.strip().endswith("p=N/A"):
                assert "discordant pairs" in line, f"bare p-value: {line!r}"

    def test_footer_carries_counts_for_every_pair(self, jsonl):
        text, _ = scoreboard.build_summary([jsonl], FIXTURES)
        footer = text.split("POWER-ANALYSIS FOOTER")[1]
        for pair in ("A vs B", "A vs C", "B vs C"):
            assert pair in footer

    def test_banner_and_arm_labels(self, jsonl):
        text, _ = scoreboard.build_summary(
            [jsonl], FIXTURES, banner="TEST BANNER")
        assert "TEST BANNER" in text
        assert "+ verified procedures" in text


class TestCli:
    def test_main_writes_detail_json(self, tmp_path, jsonl, capsys):
        rc = scoreboard.main([str(jsonl), "--fixtures-dir", str(FIXTURES)])
        out = capsys.readouterr().out
        assert rc == 0 and "SPEC 40 SCOREBOARD" in out
        detail_path = jsonl.with_name("results_scoreboard.json")
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
        assert detail["n_usable"] == 5


def _procs():
    import scoring
    return scoring.load_procedures(FIXTURES)

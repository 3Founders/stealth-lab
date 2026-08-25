import pytest
import scoring
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def proc_truth():
    return scoring.load_procedures(FIXTURES)


def ep(**over):
    base = {
        "task_id": "t1", "arm": "A", "valid": True, "resolved": False,
        "reused_procedure_ids": [], "followed_memory_ids": [],
        "refused_procedure_ids": [], "reuse_caused_failure": False,
        "stale_offered": ["pay-ach-v2"],
        "tokens_in": 1000, "tokens_out": 500,
        "tool_calls": 3, "latency_seconds": 1.5,
        "human_interventions": 0, "unseen_task": False,
    }
    base.update(over)
    return base


class TestFalseReuse:
    def test_failed_reuse_caused_failure(self):
        row = scoring.classify(ep(resolved=False, reuse_caused_failure=True,
                                  reused_procedure_ids=["pay-ach-v3"]),
                               proc_truth())
        assert row["false_reuse"] is True

    def test_success_is_never_false_reuse(self):
        # spec v4 L1259: false_reuse marks a reuse attempt itself CAUSING the
        # failure — a success despite a rocky reuse does not qualify.
        row = scoring.classify(ep(resolved=True, reuse_caused_failure=True,
                                  reused_procedure_ids=["pay-ach-v3"]),
                               proc_truth())
        assert row["false_reuse"] is False

    def test_failure_without_reuse_attempt(self):
        row = scoring.classify(ep(resolved=False, reuse_caused_failure=True),
                               proc_truth())
        assert row["false_reuse"] is False

    def test_followed_memory_counts_as_attempt(self):
        row = scoring.classify(ep(arm="B", resolved=False,
                                  reuse_caused_failure=True,
                                  followed_memory_ids=["rag-payments-1"]),
                               proc_truth())
        assert row["false_reuse"] is True and row["reused"] is True


class TestStaleRefusal:
    def test_refusing_actually_stale_is_correct(self):
        row = scoring.classify(ep(refused_procedure_ids=["pay-ach-v2"]),
                               proc_truth())
        assert row["stale_refusal_correct"] is True
        assert row["stale_offer_opportunity"] is True

    def test_using_stale_is_missed_refusal(self):
        row = scoring.classify(ep(reused_procedure_ids=["pay-ach-v2"]),
                               proc_truth())
        assert row["stale_refusal_missed"] is True
        assert row["stale_refusal_correct"] is False

    def test_refusing_fresh_procedure_is_not_stale_refusal(self):
        row = scoring.classify(ep(refused_procedure_ids=["pay-ach-v3"]),
                               proc_truth())
        assert row["stale_refusal_correct"] is False

    def test_unknown_procedure_id_tolerated(self):
        row = scoring.classify(ep(refused_procedure_ids=["ghost-proc"]),
                               proc_truth())
        assert row["stale_refusal_correct"] is False


class TestTransferAndCost:
    def test_transfer_requires_unseen_pass_and_reuse(self):
        yes = scoring.classify(ep(resolved=True, unseen_task=True,
                                  reused_procedure_ids=["pay-ach-v3"]),
                               proc_truth())
        no_reuse = scoring.classify(ep(resolved=True, unseen_task=True),
                                    proc_truth())
        seen = scoring.classify(ep(resolved=True,
                                   reused_procedure_ids=["pay-ach-v3"]),
                                proc_truth())
        assert yes["transfer_success"] and not no_reuse["transfer_success"]
        assert not seen["transfer_success"]

    def test_cost_uses_default_price_table(self):
        row = scoring.classify(
            ep(tokens_in=1_000_000, tokens_out=1_000_000), proc_truth())
        assert row["cost_usd"] == pytest.approx(12.50)

    def test_missing_fields_do_not_raise(self):
        row = scoring.classify({"task_id": "legacy", "arm": "A"},
                               proc_truth())
        assert row["pass"] is False and row["cost_usd"] == 0.0

import json

import run_harness
from conftest import FIXTURES


class TestLoadDone:
    def test_missing_file_is_empty(self, tmp_path):
        assert run_harness.load_done(tmp_path / "nope.jsonl") == set()

    def test_error_rows_retry_partial_rows_do_not_count(self, tmp_path):
        path = tmp_path / "r.jsonl"
        rows = [
            {"task_id": "ok", "A": {"valid": True}, "B": {"valid": True},
             "C": {"valid": True}},
            {"task_id": "boom", "error": "ValueError", "A": {"valid": True},
             "B": {"valid": True}, "C": {"valid": True}},
            {"task_id": "half", "A": {"valid": True}},
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        assert run_harness.load_done(path) == {"ok"}


class TestSyntheticSweep:
    def test_end_to_end_smoke(self, tmp_path):
        out = tmp_path / "out.jsonl"
        rc = run_harness.main(["--task-ids", "pay-001,deploy-001,db-mig-001",
                               "--out", str(out)])
        assert rc == 0
        rows = {r["task_id"]: r for r in
                (json.loads(l) for l in out.read_text(encoding="utf-8").splitlines())}
        assert set(rows) == {"pay-001", "deploy-001", "db-mig-001"}

        # pay-001: arm C refuses the stale offer the surface surfaced to it...
        c = rows["pay-001"]["C"]
        assert "pay-ach-v2" in c["refused_procedure_ids"]
        assert c["resolved"] is True

        # ...arm B has no gate and no refusal machinery at all.
        assert rows["pay-001"]["B"]["refused_procedure_ids"] == []

        # deploy-001: unseen task, no applicable procedure -> honest solo
        # fallback, stale canary offer still refused.
        u = rows["deploy-001"]["C"]
        assert u["unseen_task"] is True
        assert u["reused_procedure_ids"] == []
        assert "deploy-canary-v1-stale" in u["refused_procedure_ids"]

        # db-mig-001: solo-failing task rescued by an applicable procedure.
        assert rows["db-mig-001"]["A"]["resolved"] is False
        assert rows["db-mig-001"]["C"]["reused_procedure_ids"] == [
            "migrate-postgres-v4"]
        assert rows["db-mig-001"]["C"]["resolved"] is True

    def test_poisoned_gate_produces_false_reuse_row(self, tmp_path):
        out = tmp_path / "out.jsonl"
        run_harness.main(["--task-ids", "db-mig-002", "--out", str(out)])
        rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        c = rec["C"]
        # The bypassed applicability gate let a ground-truth-stale procedure
        # through; scoring must classify it as false reuse (spec v4 L1259).
        assert c["reuse_caused_failure"] is True
        assert c["resolved"] is False

"""Offline: GCE queue sharding and submission planning (no gcloud calls)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gce_queue  # noqa: E402


def test_shard_is_stable_and_spread():
    ids = [f"repo__repo-{i}" for i in range(400)]
    first = [gce_queue.shard(i, 3) for i in ids]
    assert first == [gce_queue.shard(i, 3) for i in ids]          # deterministic across calls/processes
    counts = [first.count(k) for k in range(3)]
    assert min(counts) > 100                                        # roughly even


def test_same_instance_always_same_worker_across_arms():
    workers = ["w1", "w2", "w3"]
    rows = lambda: [{"instance_id": "dask__dask-1", "model_patch": "x"}]  # noqa: E731
    got = {w for tag in ("test_A0", "test_K", "test_KP") for w in gce_queue.plan_batches(tag, rows(), {}, workers)}
    assert len(got) == 1


def test_skips_empty_and_already_submitted():
    rows = [{"instance_id": "a", "model_patch": "p"}, {"instance_id": "b", "model_patch": "  "},
            {"instance_id": "c", "model_patch": "q"}]
    plan = gce_queue.plan_batches("test_A0", rows, {"test_A0": {"c": "h"}}, ["w1"])
    assert [r["instance_id"] for r in plan["w1"]] == ["a"]
    assert gce_queue.plan_batches("test_A0", rows, {"test_A0": {"a": "h", "c": "h"}}, ["w1"]) == {}


def test_rows_from_keeps_last_row_per_instance(tmp_path):
    p = tmp_path / "p.jsonl"
    p.write_text('{"instance_id": "a", "model_patch": "old"}\n{"instance_id": "a", "model_patch": "new"}\n',
                 encoding="utf-8")
    assert gce_queue.rows_from(p) == [{"instance_id": "a", "model_patch": "new"}]


def test_tagged_matches_docker_images_output():
    assert gce_queue.tagged("swerebench/sweb.eval.x86_64.a_1776_b-1") == "swerebench/sweb.eval.x86_64.a_1776_b-1:latest"
    assert gce_queue.tagged("swebench/sweb.eval.x86_64.x:latest") == "swebench/sweb.eval.x86_64.x:latest"
    assert gce_queue.tagged("registry:5000/img") == "registry:5000/img:latest"

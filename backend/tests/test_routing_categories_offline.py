"""One ranking per kind of work: benchmarks hang under their category, the triage names one, the plan uses it
(app/routing/task_categories.py)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app.routing import evidence, service, task_categories as tc
from app.services.semantic import prompts


def test_every_benchmark_we_import_has_a_category():
    for bench, cat in [("swe-bench", "repo_change"), ("swe-rebench", "repo_change"),
                       ("llmrouterbench:swe-bench-verified", "repo_change"), ("llmrouterbench:livecodebench-test", "script"),
                       ("routerbench:mbpp", "script"), ("llmrouterbench:aime-hybrid", "math"),
                       ("routerbench:mmlu-college-mathematics", "math"), ("routerbench:mmlu-anatomy", "knowledge"),
                       ("llmrouterbench:tau2-test", "agent_tools"), ("llmrouterbench:bbh-test", "reasoning"),
                       ("routerbench:mtbench", "writing"), ("llmrouterbench:arenahard-coding", "script")]:
        assert tc.category_of(bench) == cat, bench
    assert tc.category_of("something-new") is None


def test_benchmark_nodes_sit_under_their_category_node():
    items = [{"goal_id": "g1", "benchmark": "swe-bench", "repo": "astropy/astropy"}]
    aggs = [{"benchmark": "llmrouterbench:aime-hybrid", "model_key": "m", "n": 10, "k": 5}]
    meta, parents, _ = evidence.assemble(items, aggs)
    swe, aime = evidence.benchmark_node("swe-bench"), evidence.benchmark_node("llmrouterbench:aime-hybrid")
    assert parents[swe] == [tc.category_node("repo_change")] and parents[aime] == [tc.category_node("math")]
    assert tc.category_node("repo_change") in meta and tc.category_node("math") in meta


def test_the_triage_names_a_category_and_an_unknown_one_is_dropped():
    out = prompts.parse_triage({"kind": "reusable_task", "work": "change", "category": "sql", "confidence": 0.8})
    assert out["category"] == "sql"
    assert "category" not in prompts.parse_triage({"kind": "reusable_task", "category": "poetry", "confidence": 0.8})
    assert "repo_change" in prompts.TRIAGE_SYSTEM_PROMPT and '"category"' in prompts.TRIAGE_SYSTEM_PROMPT


def test_the_plan_draws_its_prior_under_the_named_category():
    x = np.arange(12.0).reshape(2, 2, 3)
    g = SimpleNamespace(meta={"category_nodes": ["repo_change", "math"]}, arrays={"category_x": x})
    assert np.array_equal(service._category_parent(g, "math"), x[:, 1, :])
    assert service._category_parent(g, "sql") is None, "a category the fit has no node for: no parent"
    assert service._category_parent(SimpleNamespace(meta={}, arrays={}), "math") is None, "a fit before categories"

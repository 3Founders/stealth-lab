"""OPTIONAL live-provider integration (costs real API calls; never part of CI).

    RUN_LIVE_MODELS=1 python -m pytest tests/test_retrieval_live_models.py -q

Uses whatever the environment configures (JEV_BASE_URL / GEMINI_API_KEY / LOCAL_MODEL_NAME) through the
production chain (app.services.semantic). Asserts protocol properties only -- valid vocabulary, sensible
direction on unambiguous pairs, JEV-vs-fallback bookkeeping -- not exact model outputs.
"""
import os

import pytest

from app.services.semantic.chain import SemanticJudge
from app.services.semantic.prompts import IDENTITY_RELATIONS

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_MODELS") != "1", reason="set RUN_LIVE_MODELS=1 (real provider calls)")


@pytest.mark.asyncio
async def test_live_chain_judges_goal_identity_and_task_relevance():
    judge = SemanticJudge.from_settings()
    if not judge.providers:
        pytest.skip("no semantic provider configured")
    same = await judge.judge_identity("goal", "find all callers of a function", "locate every call site of a function")
    assert same.ok, same.reason
    assert same.value["relation"] in IDENTITY_RELATIONS["goal"]
    assert same.value["relation"] in ("same", "specializes", "generalizes", "related")     # not "distinct" for a paraphrase
    diff = await judge.judge_identity("goal", "find callers of a function", "rotate the production database password")
    assert diff.ok and diff.value["relation"] in ("distinct", "related")
    task = await judge.judge_identity(
        "task_goal", "migrate the database schema\nKnown in this environment: the project database is postgres",
        "migrate the database schema on mysql")
    assert task.ok and task.value["relation"] in IDENTITY_RELATIONS["task_goal"]
    assert same.provider in [p.name for p in judge.providers]


@pytest.mark.asyncio
async def test_live_embedding_provider_returns_1024_dim_vectors():
    from app.services.embeddings import Embedder

    v = await Embedder().embed_one("find callers of a function", input_type="query")
    assert len(v) == 1024

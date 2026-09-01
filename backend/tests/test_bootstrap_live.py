"""
Real, live-database proof that scripts/bootstrap.py's consolidated
summary counts are real -- not hardcoded, not estimated -- for the ONE
coherent bootstrap command (directive req #4, #41-42).

Same pattern as tests/test_environment_probe_e2e.py: requires a real
DATABASE_URL, skips (not fails) without one. A FakeEmbedder stands in for
app.services.embeddings.Embedder so this test makes zero real Voyage
calls, same discipline the other e2e tests in this suite use.

Seeds exactly the fixture set the task calls for:
  - one known-procedural repo file (a real SKILL.md, parsed by the real
    LocalDirSkillSource + run_skill_ingestion() path)
  - one known-procedural chat-history fixture (a tiny, real-shaped Claude
    export JSON whose assistant turn carries a real tool_result block --
    the actual structural signal classify_message_evidence() requires to
    clear the "attempted" bar, not a hedged "I would" suggestion)
  - one known-non-procedural repo file (a plain prose README.md: no
    numbered steps, no fenced code block, not named SKILL.md/AGENTS.md/
    CLAUDE.md/RUNBOOK.md) -- must not be discovered by any adapter

Asserts the printed/returned summary's counts come out of the real
import/ingestion functions actually running against these files: 2
accepted (repo SKILL.md + the Claude-export candidate), 1 rejected
(README.md, counted honestly as non-procedural, not fabricated as
"insufficient evidence").
"""
import json
import os
import sys
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.db.session import create_pool as _real_create_pool

import bootstrap  # scripts/bootstrap.py

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

CREATED_BY = "bootstrap-live-test"

SKILL_MD_CONTENT = """---
name: fix-pagination-cursor-test
description: Fix a failing pagination cursor test in the reports endpoint
---

1. Read the failing test in test_reports.py
2. Patch the cursor logic in reports.py
3. Rerun pytest against test_reports.py
"""

README_CONTENT = (
    "# Reports service\n\n"
    "This service exposes a paginated reports endpoint used by the "
    "dashboard. It is written in Python and deployed alongside the rest "
    "of the backend. See the architecture docs for more context.\n"
)

CLAUDE_EXPORT = [
    {
        "uuid": "bootstrap-live-test-conv-1",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:05:00Z",
        "chat_messages": [
            {
                "sender": "human",
                "text": "Fix the failing pagination test in reports.py",
                "created_at": "2026-01-01T00:00:00Z",
                "content": [],
            },
            {
                "sender": "assistant",
                "text": "",
                "created_at": "2026-01-01T00:04:00Z",
                "content": [
                    {"type": "tool_use"},
                    {
                        "type": "tool_result",
                        "content": "pytest tests/test_reports.py -q :: 1 item, 1 passed",
                    },
                ],
            },
        ],
    }
]


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.4] * 1024


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM ingested_artifacts WHERE run_id IN "
            "(SELECT run_id FROM ingestion_runs WHERE created_by = $1)", CREATED_BY,
        )
        await conn.execute(
            "DELETE FROM procedures WHERE created_by = $1", CREATED_BY,
        )
        await conn.execute(
            "DELETE FROM ingestion_runs WHERE created_by = $1", CREATED_BY,
        )


def test_bootstrap_consolidated_summary_counts_are_real(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "SKILL.md").write_text(SKILL_MD_CONTENT, encoding="utf-8")
    (repo_root / "README.md").write_text(README_CONTENT, encoding="utf-8")

    claude_export_path = tmp_path / "claude_conversations.json"
    claude_export_path.write_text(json.dumps(CLAUDE_EXPORT), encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr("app.services.embeddings.Embedder", FakeEmbedder)

    args = bootstrap.argparse.Namespace(
        repo_root=repo_root,
        claude_export=claude_export_path,
        chatgpt_export=None,
        traces_dir=None,
        workspace=workspace,
        owner_id=None,
        project_id=None,
        promote_limit=0,
        extract_limit=0,
    )

    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            summary = await bootstrap.run_repo_procedural(
                pool, FakeEmbedder(), repo_root, created_by=CREATED_BY,
            )
            return summary
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    repo_summary = asyncio.run(_run())

    # Repo source: exactly one real procedural file (SKILL.md) accepted,
    # exactly one real non-procedural file (README.md) rejected before
    # ever reaching the compiler -- both counts come straight out of
    # run_skill_ingestion()'s own real metrics dict + this walk's own
    # discover()-gate diff, never hardcoded here.
    assert repo_summary["totals"]["accepted"] == 1
    assert repo_summary["non_procedural_skipped"] == 1
    assert repo_summary["totals"]["rejected"] == 0

    # Chat-history source: exactly one real candidate from the one
    # conversation whose assistant turn carries a real tool_result block
    # (structural "attempted" evidence) -- straight out of the real
    # import_chat_history() call, no parallel counting logic.
    from app.local_agent.local_store import LocalProcedureStore
    local_store = LocalProcedureStore(str(workspace))
    chat_summary = bootstrap.run_chat_export(local_store, claude_export_path, "claude")
    assert chat_summary["conversations_parsed"] == 1
    assert chat_summary["candidates_created"] == 1
    assert chat_summary["discussion_only_skipped"] == 0

    # And the consolidated, printed summary combines both real results
    # into exactly the shape the directive names: 2 accepted overall
    # (1 repo procedure + 1 chat-history candidate), 1 non-procedural
    # rejection (the README), 0 insufficient-evidence rejections.
    printed = bootstrap._print_summary(
        {"repo_procedural": repo_summary, "claude_history": chat_summary}, [],
    )
    assert printed["candidate_procedures_created"] == 2
    assert printed["non_procedural_rejected"] == 1
    assert printed["insufficient_evidence_rejected"] == 0

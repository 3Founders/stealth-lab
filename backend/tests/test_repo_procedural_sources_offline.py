"""
Offline tests for app/services/ingestion_sources/repo_procedural.py --
the historical-bootstrap adapters beyond SKILL.md (AGENTS.md/CLAUDE.md,
CI workflow job steps, runbook-shaped docs). No network, no DB.

The core claim under test: the "is this really procedural" gate is real,
not aspirational -- a plain prose README with no commands and no numbered
steps must yield ZERO candidates from every one of these adapters, while
a real AGENTS.md, a real CI workflow, and a real runbook each yield at
least one.
"""
from __future__ import annotations

import pytest

from app.services.ingestion_sources import SourceArtifact, compute_content_hash
from app.services.ingestion_sources.repo_procedural import (
    LocalDirAgentsMdSource,
    LocalDirCIWorkflowSource,
    LocalDirRunbookSource,
    SOURCE_ADAPTERS,
    _passes_runbook_content_gate,
)
from app.services.skill_ingestion import parse_skill_md

AGENTS_MD = """# Agent instructions

When working in this repository:

1. Read the top-level README before making changes.
2. Run the test suite before committing.
3. Never commit secrets or API keys.
"""

CI_WORKFLOW_YML = """\
name: CI

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: pytest tests -q
"""

RUNBOOK_MD = """# Restart the ingestion worker

Use this when the ingestion worker queue is stuck.

1. Check current status:
   ```
   systemctl status ingestion-worker
   ```
2. Restart the service:
   ```
   systemctl restart ingestion-worker
   ```
3. Confirm the queue is draining:
   ```
   curl -s localhost:8080/queue/depth
   ```
"""

PLAIN_README_MD = """# My Project

This project does a bunch of useful things for its users. It was started
a while ago and has grown organically over time.

We welcome contributions from the community. Please be respectful in all
interactions, and remember that everyone was new once.

There is no fixed roadmap; priorities shift based on what people need
most at any given time.
"""


def _make_tree(tmp_path):
    (tmp_path / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(CI_WORKFLOW_YML, encoding="utf-8")
    (tmp_path / "docs" / "runbooks").mkdir(parents=True)
    (tmp_path / "docs" / "runbooks" / "restart-worker.md").write_text(
        RUNBOOK_MD, encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(PLAIN_README_MD, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# (a) AGENTS.md / CLAUDE.md
# --------------------------------------------------------------------------
def test_agents_md_source_yields_candidate(tmp_path):
    root = _make_tree(tmp_path)
    src = LocalDirAgentsMdSource(root)
    refs = list(src.discover())
    assert len(refs) >= 1
    art = src.fetch(refs[0])
    assert isinstance(art, SourceArtifact)
    assert art.source_type == "agents_md"
    assert art.content == AGENTS_MD
    assert art.content_hash == compute_content_hash(AGENTS_MD)
    # Real content the shared compiler's parser can already use, unmodified.
    parsed = parse_skill_md(art.content, fallback_name="agents-fallback")
    assert len(parsed.steps) >= 1


def test_agents_md_source_ignores_claude_and_plain_readme(tmp_path):
    root = _make_tree(tmp_path)
    (root / "CLAUDE.md").write_text(AGENTS_MD, encoding="utf-8")
    src = LocalDirAgentsMdSource(root)
    paths = sorted(r.path for r in src.discover())
    assert "AGENTS.md" in paths
    assert "CLAUDE.md" in paths
    assert "README.md" not in paths


# --------------------------------------------------------------------------
# (b) CI workflow job steps
# --------------------------------------------------------------------------
def test_ci_workflow_source_yields_candidate_with_real_steps(tmp_path):
    root = _make_tree(tmp_path)
    src = LocalDirCIWorkflowSource(root)
    refs = list(src.discover())
    assert len(refs) >= 1
    art = src.fetch(refs[0])
    assert art.source_type == "ci_workflow"
    # NOT a full YAML dump -- a synthesized step sequence.
    assert "runs-on:" not in art.content
    assert "\non:" not in art.content
    assert "branches:" not in art.content
    parsed = parse_skill_md(art.content, fallback_name="ci-fallback")
    assert len(parsed.steps) == 2
    assert any("Install dependencies" in s for s in parsed.steps)
    assert any("Run tests" in s for s in parsed.steps)


def test_ci_workflow_source_skips_jobs_with_no_steps(tmp_path):
    root = _make_tree(tmp_path)
    (root / ".github" / "workflows" / "empty.yml").write_text(
        "name: Empty\njobs:\n  noop:\n    runs-on: ubuntu-latest\n", encoding="utf-8",
    )
    src = LocalDirCIWorkflowSource(root)
    refs = list(src.discover())
    # only the real "build" job from ci.yml, never "noop"
    assert all("noop" not in r.path for r in refs)
    assert len(refs) >= 1


# --------------------------------------------------------------------------
# (c) runbook-shaped docs
# --------------------------------------------------------------------------
def test_runbook_source_yields_candidate(tmp_path):
    root = _make_tree(tmp_path)
    src = LocalDirRunbookSource(root)
    refs = list(src.discover())
    assert len(refs) >= 1
    ref = next(r for r in refs if r.path.endswith("restart-worker.md"))
    art = src.fetch(ref)
    assert art.source_type == "runbook"
    assert art.content == RUNBOOK_MD
    parsed = parse_skill_md(art.content, fallback_name="runbook-fallback")
    assert len(parsed.steps) >= 1


def test_runbook_source_rejects_plain_prose_readme(tmp_path):
    root = _make_tree(tmp_path)
    src = LocalDirRunbookSource(root)
    refs = list(src.discover())
    assert not any(r.path == "README.md" for r in refs)


def test_runbook_named_file_outside_docs_runbooks_is_still_eligible(tmp_path):
    root = _make_tree(tmp_path)
    (root / "RUNBOOK.md").write_text(RUNBOOK_MD, encoding="utf-8")
    src = LocalDirRunbookSource(root)
    paths = [r.path for r in src.discover()]
    assert "RUNBOOK.md" in paths


def test_runbook_content_gate_requires_both_numbered_steps_and_code_fence():
    numbered_only = "1. Do the thing.\n2. Do another thing.\n"
    fence_only = "Some notes.\n\n```\nls -la\n```\n"
    both = "1. Run it:\n   ```\n   ls -la\n   ```\n"
    assert not _passes_runbook_content_gate(numbered_only)
    assert not _passes_runbook_content_gate(fence_only)
    assert not _passes_runbook_content_gate(PLAIN_README_MD)
    assert _passes_runbook_content_gate(both)


# --------------------------------------------------------------------------
# the actual proving assertion: plain prose yields ZERO across all three
# --------------------------------------------------------------------------
def test_plain_readme_yields_zero_candidates_from_every_adapter(tmp_path):
    root = _make_tree(tmp_path)

    agents_refs = [
        r for r in LocalDirAgentsMdSource(root).discover() if r.path == "README.md"
    ]
    ci_refs = list(LocalDirCIWorkflowSource(root).discover())
    runbook_refs = [
        r for r in LocalDirRunbookSource(root).discover() if r.path == "README.md"
    ]

    assert agents_refs == []
    assert all("README" not in r.path for r in ci_refs)
    assert runbook_refs == []


# --------------------------------------------------------------------------
# dispatch table
# --------------------------------------------------------------------------
def test_source_adapters_dispatch():
    assert SOURCE_ADAPTERS["agents_md_dir"] is LocalDirAgentsMdSource
    assert SOURCE_ADAPTERS["ci_workflow_dir"] is LocalDirCIWorkflowSource
    assert SOURCE_ADAPTERS["runbook_dir"] is LocalDirRunbookSource

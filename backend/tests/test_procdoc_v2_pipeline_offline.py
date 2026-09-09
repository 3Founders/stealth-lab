"""
procdoc_v2 pipeline: SKILL.md parser -> ParsedSkill -> procedure fields ->
canonical retrieval document.

Deterministic, DB-free, provider-free. Proves the audit's P0/P1 fixes:
  - the parser recognises realistic section spellings and keeps the
    source-authored WHAT / WHY / WHEN / WHEN-NOT / PREREQ / LIMITATION /
    FAILURE / OUTCOME instead of hard-coding them empty;
  - non-step bullets (limitations, "do not", selection criteria) never
    become procedure steps;
  - the canonical builder renders the richer fields, omits empty sections,
    strips Markdown/link/path noise, and stays byte-deterministic;
  - nothing is fabricated -- a field the source did not state stays empty;
  - there is ONE procedure embedding recipe (regression guard).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.retrieval_document import (
    RETRIEVAL_DOCUMENT_VERSION,
    build_procedure_retrieval_document,
    retrieval_document_sha256,
)
from app.services.skill_ingestion import (
    _parsed_skill_procedure_shape,
    _structured_fields_from_parsed,
    parse_skill_md,
)

# --- a rich, realistically-messy SKILL.md ---------------------------------
RICH_MD = """---
name: qdrant-vertical-scaling
description: Guide Qdrant vertical scaling decisions.
---

## Why this works
Vertical scaling avoids the permanent operational complexity of sharding.

## Use this when
Current node RAM/CPU/disk is insufficient but the workload does not yet
need distribution. See the [console guide](https://cloud.qdrant.io/docs)
and ../../qdrant-monitoring/SKILL.md.

## Prerequisites
- replication_factor >= 2 before resizing
- monitoring via Grafana/Prometheus already in place

## Procedure
1. Log into the Qdrant Cloud Console
2. Select the cluster to resize
3. Choose a larger node configuration

## When NOT to use
- data volume exceeds a single node even with quantization
- you need fault tolerance across nodes

## Limitations
- a rolling restart without replicas causes downtime

## Troubleshooting
- scaling down RAM without load testing causes severe latency for days

## Expected result
The cluster runs on a larger node with no downtime when replication is
configured.
"""


def _parsed():
    return parse_skill_md(RICH_MD)


# ============================ PARSER ====================================


def test_multiple_heading_spellings_are_recognised():
    p = _parsed()
    assert p.purpose and "avoids the permanent operational complexity" in p.purpose
    assert p.applies_when and "insufficient but the workload" in p.applies_when
    assert p.when_not_to_use and "fault tolerance across nodes" in p.when_not_to_use
    assert any("replication_factor" in x for x in p.prerequisites)
    assert any("rolling restart without replicas" in x for x in p.limitations)
    assert any("scaling down RAM" in x for x in p.failure_modes)
    assert p.expected_outcome and "no downtime" in p.expected_outcome


def test_only_real_workflow_actions_become_steps():
    p = _parsed()
    assert p.steps == [
        "Log into the Qdrant Cloud Console",
        "Select the cluster to resize",
        "Choose a larger node configuration",
    ]
    # none of the non-step bullets leaked in
    joined = " ".join(p.steps).lower()
    for leak in ("fault tolerance", "rolling restart", "scaling down ram", "replication_factor"):
        assert leak not in joined


def test_parser_is_deterministic():
    a = _parsed()
    b = parse_skill_md(RICH_MD)
    assert (a.steps, a.purpose, a.when_not_to_use, a.prerequisites,
            a.limitations, a.failure_modes, a.expected_outcome) == (
        b.steps, b.purpose, b.when_not_to_use, b.prerequisites,
        b.limitations, b.failure_modes, b.expected_outcome)


def test_parser_fabricates_nothing_when_sections_absent():
    flat = (
        "---\nname: agent-browser\n"
        "description: Browser automation CLI. Use when you need to click, fill, screenshot.\n---\n\n"
        "1. open the url\n2. snapshot\n3. click a ref\n"
    )
    p = parse_skill_md(flat)
    assert p.steps == ["open the url", "snapshot", "click a ref"]
    assert p.purpose is None
    assert p.when_not_to_use is None
    assert p.prerequisites == []
    assert p.limitations == []
    assert p.failure_modes == []
    assert p.expected_outcome is None


def test_structured_fields_are_honest_prose_not_predicates():
    sf = _structured_fields_from_parsed(_parsed())
    for entry in sf["preconditions"] + sf["failure_conditions"] + sf["postconditions"] + sf["exclusions"]:
        assert set(entry) == {"description", "source"}
        assert entry["source"].startswith("skill_md:")
        # prose, not a {subject,predicate,object} predicate
        assert "subject" not in entry and "predicate" not in entry
    assert len(sf["preconditions"]) == 2
    assert len(sf["failure_conditions"]) == 2   # troubleshooting + limitation
    assert len(sf["postconditions"]) == 1
    assert len(sf["exclusions"]) == 1


# ==================== RETRIEVAL DOCUMENT ================================


def _doc():
    return build_procedure_retrieval_document(
        _parsed_skill_procedure_shape(_parsed(), domain="vector-db")
    )


def test_version_bumped_to_v2():
    assert RETRIEVAL_DOCUMENT_VERSION == "procdoc_v2"


def test_richer_sections_appear_when_present():
    d = _doc()
    for label in ("Name:", "Purpose:", "When to use:", "When not to use:",
                  "Domain:", "Steps:", "Expected outcome:", "Fails when:"):
        assert label in d, f"missing {label}\n{d}"


def test_empty_fields_do_not_create_junk_sections():
    d = build_procedure_retrieval_document({"name": "bare", "goal": "do a bare thing"})
    assert d == "Name: bare\nPurpose: do a bare thing"
    assert "When not to use:" not in d
    assert "Expected outcome:" not in d
    assert "Fails when:" not in d


def test_markdown_link_and_path_noise_is_stripped():
    d = _doc()
    assert "https://" not in d
    assert "SKILL.md" not in d
    assert "](" not in d
    assert "console guide" in d  # link TEXT is kept, target dropped


def test_retrieval_document_is_deterministic():
    d1 = _doc()
    d2 = build_procedure_retrieval_document(
        _parsed_skill_procedure_shape(parse_skill_md(RICH_MD), domain="vector-db")
    )
    assert d1 == d2
    assert retrieval_document_sha256(d1) == retrieval_document_sha256(d2)


def test_exclusions_required_state_expected_effects_are_represented():
    d = build_procedure_retrieval_document({
        "name": "x", "goal": "g",
        "exclusions": [{"description": "the input is already sorted"}],
        "required_state": {"index": "built"},
        "expected_effects": [{"description": "rows are deduplicated"}],
    })
    assert "When not to use: the input is already sorted" in d
    assert "requires state index built" in d
    assert "Expected outcome: rows are deduplicated" in d


def test_exclusions_not_duplicated_across_column_and_domain_payload():
    d = build_procedure_retrieval_document({
        "name": "x", "goal": "g",
        "exclusions": [{"description": "a big static tool set"}],
        "domain_payload": {"when_not_to_use": "a big static tool set"},
    })
    assert d.count("a big static tool set") == 1


# ==================== ONE EMBEDDING RECIPE (regression guard) ===========


def test_no_non_canonical_procedure_embedding_recipe_in_app():
    """Fail if a NEW production path embeds a bare goal / task string and
    stores it as a procedure vector. The canonical path is
    build_procedure_retrieval_document -> Embedder -> capture_procedure /
    supersede_procedure. Known, allowed exceptions are listed explicitly."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    # Scope: the GLOBAL procedures table only. app/local_agent/* is the
    # per-workspace SQLite LocalProcedureStore (plain-python cosine, not
    # pgvector, not the canonical corpus) -- a separate subsystem by
    # design; its vectors never become authoritative global procedure
    # vectors (publish_local_procedure drops them).
    scan_roots = ("app/services", "app/api", "app/mcp_server", "app/execution")
    for py in app_dir.rglob("*.py"):
        rel = py.relative_to(app_dir.parents[0]).as_posix()
        if not any(rel.startswith(r) for r in scan_roots):
            continue
        text = py.read_text(encoding="utf-8")
        if "capture_procedure(" not in text and "changed_fields" not in text:
            continue
        if "embedding=" not in text and '"embedding"' not in text:
            continue
        if "build_procedure_retrieval_document" in text:
            continue
        # publish.py deliberately forwards NO embedding (pending re-embed).
        if rel == "app/services/publish.py":
            continue
        offenders.append(rel)
    assert not offenders, (
        "these files persist a procedure embedding without building the "
        f"canonical retrieval document: {offenders}"
    )


def test_reembed_gemini_script_delegates_to_canonical_backfill():
    script = (Path(__file__).resolve().parents[1] / "scripts"
              / "reembed_procedures_gemini.py").read_text(encoding="utf-8")
    assert "backfill_representation" in script
    assert "DEPRECATED" in script
    # the old inline recipe is gone from the executable body: no local
    # Embedder().embed(...) loop, no manual UPDATE procedures SET embedding.
    assert "UPDATE procedures SET embedding" not in script
    assert "await embedder.embed(" not in script


@pytest.mark.parametrize("spelling", [
    "## When to use", "## Use when", "## Applies when", "## Best for",
    "## When this is useful", "## Appropriate when",
])
def test_when_to_use_spelling_variants(spelling):
    md = f"---\nname: s\ndescription: d\n---\n\n{spelling}\nthe widget count is high\n\n## Steps\n1. do it\n"
    p = parse_skill_md(md)
    assert p.applies_when == "the widget count is high"
    assert p.steps == ["do it"]


@pytest.mark.parametrize("spelling", [
    "## When NOT to use", "## Do not use", "## Avoid when",
    "## Not appropriate for", "## Anti-patterns",
])
def test_when_not_to_use_spelling_variants(spelling):
    md = f"---\nname: s\ndescription: d\n---\n\n## Steps\n1. do it\n\n{spelling}\nthe dataset is tiny\n"
    p = parse_skill_md(md)
    assert p.when_not_to_use == "the dataset is tiny"
    assert p.steps == ["do it"]

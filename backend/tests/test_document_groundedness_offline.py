"""
G7 -- pure-logic tests for the document-path groundedness check
(`app.services.skill_ingestion._check_document_groundedness`). No DB, no
network: a deterministic sanity check that a captured Procedure's steps
actually come from the source document rather than being synthesized.
"""
from __future__ import annotations

from app.services.skill_ingestion import ParsedSkill, _check_document_groundedness

_CONTENT = """
# Deploy the service

## Steps

1. Build the container image
2. Push the image to the registry
3. Apply the Kubernetes manifest
4. Verify the rollout status
"""


def _parsed(**over) -> ParsedSkill:
    defaults = dict(
        name="deploy-service",
        description="Deploy the service to the cluster",
        steps=[
            "Build the container image",
            "Push the image to the registry",
            "Apply the Kubernetes manifest",
            "Verify the rollout status",
        ],
    )
    defaults.update(over)
    return ParsedSkill(**defaults)


def test_steps_grounded_in_source_pass_clean():
    result = _check_document_groundedness(_parsed(), _CONTENT)
    assert result == {"grounded": True, "reasons": []}


def test_a_step_with_no_source_text_is_flagged():
    parsed = _parsed(steps=[
        "Build the container image",
        "Sacrifice a goat to the deployment gods",  # not in _CONTENT anywhere
    ])
    result = _check_document_groundedness(parsed, _CONTENT)
    assert result["grounded"] is False
    assert any("goat" in r for r in result["reasons"])


def test_empty_name_or_fallback_placeholder_is_flagged():
    result = _check_document_groundedness(_parsed(name=""), _CONTENT)
    assert result["grounded"] is False
    assert any("name" in r for r in result["reasons"])

    result2 = _check_document_groundedness(_parsed(name="unnamed-skill"), _CONTENT)
    assert result2["grounded"] is False
    assert any("name" in r for r in result2["reasons"])


def test_empty_description_is_flagged():
    result = _check_document_groundedness(_parsed(description=""), _CONTENT)
    assert result["grounded"] is False
    assert any("description" in r for r in result["reasons"])


def test_step_count_wildly_exceeding_raw_list_lines_is_flagged():
    # _CONTENT has 4 numbered lines; claim 20 steps -> way over budget.
    parsed = _parsed(steps=[f"Build the container image variant {i}" for i in range(20)])
    result = _check_document_groundedness(parsed, _CONTENT)
    assert result["grounded"] is False
    assert any("numbered/bulleted lines" in r for r in result["reasons"])


def test_a_little_slack_on_step_count_is_allowed():
    # 4 raw numbered lines + slack of 3 -> up to 7 steps tolerated, as
    # long as every step's own text is still grounded in the source
    # (repeating real steps exercises the count-slack path in isolation
    # from the per-step overlap check).
    parsed = _parsed(steps=[
        "Build the container image", "Push the image to the registry",
        "Apply the Kubernetes manifest", "Verify the rollout status",
        "Build the container image", "Push the image to the registry",
        "Apply the Kubernetes manifest",
    ])
    result = _check_document_groundedness(parsed, _CONTENT)
    assert result["grounded"] is True


def test_case_and_whitespace_insensitive_overlap():
    parsed = _parsed(steps=["  BUILD   the container    IMAGE  "])
    result = _check_document_groundedness(parsed, _CONTENT)
    assert result["grounded"] is True


def test_empty_step_list_is_not_itself_flagged_by_overlap_or_count_checks():
    parsed = _parsed(steps=[])
    result = _check_document_groundedness(parsed, _CONTENT)
    # no steps -> nothing to overlap-check, and 0 <= raw_count + 3
    assert all("step " not in r or "not found" not in r for r in result["reasons"])

"""Step 6: turn a discovered CI/dependency artifact into knowledge.

This is the module that decides what a step-6 artifact *is*, and it is
deliberately boring: it mostly refuses.

Three rules, all load-bearing
-----------------------------
1. **Nothing from step 6 is ever a verified Procedure.** Both sources lack
   verified execution. The Zenodo workflow corpus has no run outcome at all;
   a merged Dependabot PR's green CI is a host self-report observed from
   outside this system. So `compile_artifact` produces a *candidate* Procedure
   (`provenance="system_pending_review"`, no `verification_state` promotion) or
   a failure Claim. Anything that tried to set `verification_state="verified"`
   here would be laundered trust, and `db/30`'s trigger plus
   `evidence_trust.is_trusted_writer` are the teeth that make laundering
   detectable -- this module just declines to attempt it.

2. **License is decided per item, never per compilation.**
   `repo_license_policy.classify_spdx` is called for each artifact. The step-6
   corpus is CC-BY-4.0, which is **not** on `DEFAULT_ALLOWLIST`, so it returns
   QUARANTINE and the artifact is not ingested. That is the allowlist working
   correctly. This module does not widen it; the board question is
   `.scratch/build-board.md` Q-STEP6-LICENSE.

3. **Scope + provenance on everything** (hard rule 2): source id, revision,
   row id, extractor version, and SPDX in the `source_locator`.

The extraction decision
-----------------------
`compile_artifact` needs a Goal to hang the knowledge off, which means an LLM
call in the general case. That is spend, so it is behind `ingest_budget.guard`
and the caller must pass a `client`. When `client is None` this module takes the
same deterministic path `compile_skill_artifact` takes: refuse, loudly, with a
count. A pilot that produced zero Procedures because nobody wired a client is a
truthful result; a pilot that invented a Goal name from a title is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.ingestion_sources.base import SourceArtifact

# Bump the version when the compile rules change, so a stored
# `extraction_version` says which rules produced it.
COMPILER_VERSION = "step6_workflow_knowledge/v1"
CREATED_BY = "step6_workflow_knowledge"

#: The only provenance a step-6 artifact may carry. Not "verified", not
#: "public_generated" -- both would overstate what a workflow file or an
#: externally-observed CI run proves.
CANDIDATE_PROVENANCE = "system_pending_review"

#: Per-artifact outcome vocabulary. Small on purpose: these are the only
#: shapes step 6 can honestly produce.
OUTCOME_CANDIDATE = "candidate"
OUTCOME_QUARANTINED_LICENSE = "quarantined_license"
OUTCOME_REJECTED_SHAPE = "rejected_shape"
OUTCOME_NO_GOAL = "no_goal"


@dataclass
class CompileReport:
    """Per-item accounting. Every count here is an exit criterion."""

    considered: int = 0
    candidates: int = 0
    goals_created: int = 0
    goals_matched: int = 0
    claims_created: int = 0
    quarantined_license: int = 0
    rejected_shape: int = 0
    no_goal: int = 0
    duplicates: int = 0
    bytes_content: int = 0
    license_reasons: dict[str, int] = field(default_factory=dict)
    spent_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "candidates": self.candidates,
            "goals_created": self.goals_created,
            "goals_matched": self.goals_matched,
            "claims_created": self.claims_created,
            "quarantined_license": self.quarantined_license,
            "rejected_shape": self.rejected_shape,
            "no_goal": self.no_goal,
            "duplicates": self.duplicates,
            "bytes_content": self.bytes_content,
            "license_reasons": dict(self.license_reasons),
            "spent_usd": round(self.spent_usd, 6),
        }


class Step6Rejected(RuntimeError):
    """Refused, with a reason that belongs in the report's reason counts."""


# --------------------------------------------------------------------------
# Shape checks
# --------------------------------------------------------------------------

_MIN_CONTENT_BYTES = 80
_HEADING_RE = re.compile(r"^#\s+(?P<title>.+)$", re.MULTILINE)
_FIELD_RE = re.compile(r"^(?P<key>[a-z_]+):\s*(?P<value>.*)$", re.MULTILINE)


def parse_artifact_document(content: str) -> dict[str, Any]:
    """Pull the structured fields back out of a rendered artifact.

    Round-tripping through text is deliberate: the rendered document is what
    any downstream extractor or human actually sees, so parsing it back proves
    the document actually carries what we claim it carries. If a field is
    missing from the text, it is missing, and that surfaces here.

    The package name lives in the heading, not in a `package:` field, so it is
    recovered from the heading pattern -- reading it from the rendered text is
    the point, and the two must not drift.
    """
    title_match = _HEADING_RE.search(content)
    fields = {m.group("key"): m.group("value").strip()
              for m in _FIELD_RE.finditer(content)}
    title = title_match.group("title").strip() if title_match else ""
    package = fields.get("package", "")
    if not package:
        bump = _GOAL_RE.search(content)
        if bump:
            package = bump.group("pkg").strip()
    return {
        "title": title,
        "package": package,
        "repository": fields.get("repository", ""),
        "path": fields.get("path", ""),
        "commit": fields.get("commit", ""),
        "ecosystem": fields.get("ecosystem", ""),
        "from_version": fields.get("from_version", ""),
        "to_version": fields.get("to_version", ""),
        "fields": fields,
    }


def artifact_is_substantive(content: str) -> bool:
    """Reject stubs. A one-line workflow or an empty PR is not knowledge, and
    ingesting it would inflate the candidate count with nothing a reviewer can
    use."""
    if len(content.strip()) < _MIN_CONTENT_BYTES:
        return False
    return "verification status" in content or "observed CI verdict" in content


def is_bump_artifact(artifact: SourceArtifact) -> bool:
    return artifact.source_type == "dependency_bump_pr"


def is_workflow_artifact(artifact: SourceArtifact) -> bool:
    return artifact.source_type == "ci_workflow_history"


# --------------------------------------------------------------------------
# License gating -- per item
# --------------------------------------------------------------------------

def gate_license(artifact: SourceArtifact) -> "tuple[str, Optional[str]]":
    """Return `(decision, reason)` from the allowlist, per artifact.

    The caller's configured allowlist can extend what is allowed but can never
    buy an ALLOW for a reject-family license -- that subtraction is
    `_effective_allowlist`'s job inside `classify_spdx`, not something to
    reimplement here.
    """
    from app.services.repo_license_policy import classify_spdx

    metadata = artifact.license_metadata or {}
    spdx = metadata.get("spdx_id") or metadata.get("license")
    if artifact.source_type == "ci_workflow_history":
        # A workflow file's license is the *repository's*, which this source
        # does not resolve (Zenodo is a compilation; per-repo licensing is
        # unknowable from it). The corpus license is recorded as provenance,
        # but the item license is genuinely unknown -> QUARANTINE, not ALLOW.
        return classify_spdx(None, source_path=artifact.path or "unknown").decision, \
            "per-repository license is not resolvable from a Zenodo compilation"
    return classify_spdx(spdx, source_path=artifact.path or artifact.uri).decision, None


# --------------------------------------------------------------------------
# Locator
# --------------------------------------------------------------------------

def build_locator(artifact: SourceArtifact) -> Optional[dict]:
    """`source_locator` for the knowledge this produces (hard rule 2)."""
    from app.services.source_locators import procedure_locator_from_source

    metadata = artifact.license_metadata or {}
    return procedure_locator_from_source(
        source_key=artifact.source_id or artifact.source_type,
        uri=artifact.uri,
        content_hash=artifact.content_hash,
        commit=artifact.commit,
        path=artifact.path,
        object_locator=(metadata.get("zenodo_record")
                        if artifact.source_type == "ci_workflow_history" else None),
    )


# --------------------------------------------------------------------------
# Goal proposal
# --------------------------------------------------------------------------

_GOAL_RE = re.compile(r"^#\s+dependency bump:\s*(?P<pkg>.+)$", re.MULTILINE | re.IGNORECASE)
_WORKFLOW_GOAL_RE = re.compile(r"^#\s+github actions workflow:\s*(?P<name>.+)$",
                               re.MULTILINE | re.IGNORECASE)

#: A goal name longer than this is a paragraph, not a goal.
_MAX_GOAL_NAME = 160


def propose_goal(artifact: SourceArtifact) -> Optional[tuple[str, Optional[str]]]:
    """Deterministically propose `(canonical_name, description)`.

    Returns None when no name can be derived. **No LLM is called here.** Goal
    naming from a bot's regular title is mechanical, and a mechanical proposal
    costs nothing, is reproducible, and cannot hallucinate. If a caller wants
    LLM-refined naming it must pass a `judge` and pay for it through
    `ingest_budget` -- but the pilot's yield numbers should be measured on the
    free path first, or they measure the naming model rather than the corpus.
    """
    content = artifact.content or ""
    if is_bump_artifact(artifact):
        match = _GOAL_RE.search(content)
        if not match:
            return None
        package = match.group("pkg").strip()
        if not package or package.lower() == "unknown package":
            return None
        # Some bumps name a FILE, not a package ("bump
        # esacteksab/.github/.github/workflows/pre-commit.yml from 0.84.1"). A
        # goal that names a file path is not a goal, and `find_or_create_goal`'s
        # own quality gate rejects it ("names a hyper-specific literal file
        # path, not a generalizable outcome"). Caught here rather than as an
        # exception, so the item is counted instead of aborting the batch.
        if "/" in package and package.count("/") > 1 and "." in package.rsplit("/", 1)[-1]:
            return None
        parsed = parse_artifact_document(content)
        name = f"upgrade {package}"
        if parsed.get("ecosystem") and parsed["ecosystem"] != "unknown":
            name += f" in {parsed['ecosystem']} projects"
        description = (
            f"Bump {package} from {parsed.get('from_version') or 'an earlier version'} "
            f"to {parsed.get('to_version') or 'a later version'} as merged by a "
            f"dependency bot, observed in {artifact.repository or 'an unnamed repository'}. "
            f"Evidence is an externally observed CI verdict, not our own execution."
        )
        return name[:_MAX_GOAL_NAME], description

    match = _WORKFLOW_GOAL_RE.search(content)
    if not match:
        return None
    workflow_name = match.group("name").strip()
    if not workflow_name:
        return None
    parsed = parse_artifact_document(content)
    name = f"maintain CI workflow: {workflow_name}"
    description = (
        f"GitHub Actions workflow {workflow_name} in {artifact.repository or 'an unnamed repository'} "
        f"({artifact.path or 'unknown path'}). Source records workflow file revisions only; "
        f"no run outcome is available, so this is candidate material awaiting review."
    )
    return name[:_MAX_GOAL_NAME], description


# --------------------------------------------------------------------------
# Preconditions -- the reason this knowledge is not blindly applicable
# --------------------------------------------------------------------------

def propose_preconditions(artifact: SourceArtifact, parsed: dict[str, Any]) -> list[str]:
    """Preconditions a *user* of this knowledge must actually hold.

    These are the checkable, non-compensatory gates the applicability cascade
    enforces. A version bump is not applicable to a project on a different
    major version, and saying so here is the difference between a Procedure and
    a search result.
    """
    if is_bump_artifact(artifact):
        preconditions: list[str] = []
        if parsed.get("from_version"):
            preconditions.append(
                f"the project's current {parsed.get('package') or 'dependency'} version is "
                f"{parsed['from_version']} (the version this bump was authored against)")
        if parsed.get("ecosystem") and parsed["ecosystem"] != "unknown":
            preconditions.append(
                f"the project builds with the {parsed['ecosystem']} ecosystem toolchain")
        return preconditions

    if parsed.get("repository"):
        return [f"the project is the repository {parsed['repository']} or a fork of it"]
    return []


def propose_postconditions(artifact: SourceArtifact, parsed: dict[str, Any]) -> list[str]:
    """What a user should observe if the knowledge helped.

    Stated as observable checks rather than as "it works", because
    `execution/evidence.py` bans bare model-asserted success and a Procedure
    whose postcondition is unfalsifiable can never earn real evidence.
    """
    if is_bump_artifact(artifact):
        conditions = ["the dependency resolves at the new version"]
        if parsed.get("ecosystem") and parsed["ecosystem"] != "unknown":
            conditions.append(f"the {parsed['ecosystem']} build and existing tests still pass")
        else:
            conditions.append("the existing test suite still passes")
        return conditions
    return ["the workflow runs to completion on a push to the default branch"]


def propose_failure_conditions(artifact: SourceArtifact, parsed: dict[str, Any]) -> list[str]:
    """Failure knowledge. A version bump that merged with green CI and still
    broke downstream builds is the single most common real outcome of this
    source, so it is named explicitly rather than left implicit."""
    if is_bump_artifact(artifact):
        return [
            f"the new {parsed.get('to_version') or 'target'} version removes an API this project uses",
            "the upgrade requires a migration step not captured in the manifest diff",
            "a transitive dependency conflict appears that the manifest does not show",
        ]
    return ["the workflow references an action version that no longer exists"]


def propose_steps(artifact: SourceArtifact, parsed: dict[str, Any]) -> list[str]:
    """Ordered, concrete steps. A Procedure whose steps are prose is a note,
    not a procedure."""
    if is_bump_artifact(artifact):
        package = parsed.get("package") or "the dependency"
        to_version = parsed.get("to_version") or "the target version"
        return [
            f"read the changelog and release notes for {package} {to_version}",
            f"update the {parsed.get('package') or 'dependency'} declaration to {to_version}",
            "refresh the lockfile and reinstall dependencies",
            "run the existing test suite and confirm no new failures",
            "run the build and confirm it completes",
        ]
    return [
        "review the workflow's triggers and permissions",
        "verify every referenced action is pinned to an existing version",
        "confirm required repository secrets are configured",
        "run the workflow on a branch before relying on it",
    ]


# --------------------------------------------------------------------------
# The compile entry point
# --------------------------------------------------------------------------

async def compile_artifact(
    pool: Any,
    artifact: SourceArtifact,
    *,
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    judge: Any = None,
    run_id: Optional[str] = None,
    report: Optional[CompileReport] = None,
) -> dict[str, Any]:
    """Compile one artifact into candidate knowledge.

    Returns a dict with an `outcome` in the `OUTCOME_*` vocabulary. Never
    raises for a policy refusal (license, shape) -- those are counted, because
    a run that aborts on the first CC-BY row produces no accounting at all.
    Raises only on a genuine infrastructure fault.
    """
    report = report if report is not None else CompileReport()
    report.considered += 1

    if not artifact_is_substantive(artifact.content or ""):
        report.rejected_shape += 1
        return {"outcome": OUTCOME_REJECTED_SHAPE, "reason": "artifact below size/shape floor"}

    decision, reason = gate_license(artifact)
    if decision != "ALLOW":
        report.quarantined_license += 1
        report.license_reasons[reason or decision] = (
            report.license_reasons.get(reason or decision, 0) + 1)
        return {"outcome": OUTCOME_QUARANTINED_LICENSE, "reason": reason, "decision": decision}

    if not (is_bump_artifact(artifact) or is_workflow_artifact(artifact)):
        report.rejected_shape += 1
        return {"outcome": OUTCOME_REJECTED_SHAPE, "reason": f"unknown source_type {artifact.source_type!r}"}

    proposal = propose_goal(artifact)
    if proposal is None:
        report.no_goal += 1
        return {"outcome": OUTCOME_NO_GOAL, "reason": "no goal name derivable from artifact"}
    canonical_name, description = proposal

    parsed = parse_artifact_document(artifact.content or "")
    report.bytes_content += len(artifact.content or "")

    locator = build_locator(artifact)

    if judge is not None:
        from app.services import ingest_budget
        await ingest_budget.guard("step6_goal_judge")

    from app.services.goals import GoalQualityRejected, find_or_create_goal

    try:
        goal = await find_or_create_goal(
            pool,
            canonical_name=canonical_name,
            scope_type=scope_type,
            scope_entity_id=scope_entity_id,
            provenance=CANDIDATE_PROVENANCE,
            description=description,
            created_from=artifact.source_type,
            metadata={
                "extractor_version": COMPILER_VERSION,
                "source_locator": locator,
                "verification_status": "unverified_no_local_execution",
            },
            owner_id=owner_id,
            visibility=visibility,
            created_by=CREATED_BY,
            judge=judge,
        )
    except GoalQualityRejected as exc:
        # One unusable goal name must never abort the batch -- the same
        # discipline `dispatch.py` uses for a malformed trajectory. Counted as
        # `no_goal` so the rate is visible instead of silent.
        report.no_goal += 1
        report.license_reasons[f"goal_quality_rejected: {exc}"] = (
            report.license_reasons.get(f"goal_quality_rejected: {exc}", 0) + 1)
        return {"outcome": OUTCOME_NO_GOAL, "reason": f"goal quality rejected: {exc}"}
    if goal.get("created"):
        report.goals_created += 1
    else:
        report.goals_matched += 1

    outcome = await _write_procedure(
        pool, artifact=artifact, goal=goal, parsed=parsed, locator=locator,
        scope_type=scope_type, scope_entity_id=scope_entity_id,
        owner_id=owner_id, visibility=visibility, run_id=run_id,
    )

    report.candidates += 1
    return outcome


async def _write_procedure(
    pool: Any, *, artifact: SourceArtifact, goal: dict[str, Any], parsed: dict[str, Any],
    locator: Optional[dict], scope_type: str, scope_entity_id: Optional[str],
    owner_id: Optional[str], visibility: str, run_id: Optional[str],
) -> dict[str, Any]:
    """Write the candidate Procedure.

    `verification_state` is never set. `capture_procedure` leaves it at the
    schema default, which is not `verified`, and `db/30`'s trigger would
    refuse a `verified` transition with no qualifying evidence anyway -- so the
    trust floor holds even if this call is wrong about something else.
    """
    from app.services.procedures import capture_procedure

    # `capture_procedure` resolves the scope entity as
    # `scope_entity_id or domain`, and the V0 gate forbids `global` scope from
    # carrying an entity id at all. So a globally-scoped step-6 Procedure must
    # NOT also pass `domain`, or the domain is silently promoted into an entity
    # id and the write is refused (`V0: global scope cannot carry an
    # entity_id`). The ecosystem therefore travels in the display fields and
    # the steps rather than in `domain`, which is also where a reader wants it.
    ecosystem = parsed.get("ecosystem") or "ci"
    scope_entity = scope_entity_id
    if scope_type == "global":
        scope_entity = None

    procedure = await capture_procedure(
        pool,
        name=(parsed.get("title") or artifact.path or "workflow knowledge")[:200],
        goal=goal["canonical_name"],
        steps=propose_steps(artifact, parsed),
        preconditions=propose_preconditions(artifact, parsed),
        postconditions=propose_postconditions(artifact, parsed),
        failure_conditions=propose_failure_conditions(artifact, parsed),
        scope_type=scope_type,
        scope_entity_id=scope_entity,
        expected_effects=propose_postconditions(artifact, parsed),
        provenance=CANDIDATE_PROVENANCE,
        domain=None if scope_type == "global" else ecosystem,
        source_key=artifact.uri,
        source_locator=locator,
        source_artifacts=[],
        display_description=(
            f"{ecosystem} knowledge from {artifact.repository or 'an unnamed repository'}. "
            f"Extracted by {COMPILER_VERSION}. Unverified: no local execution."
        ),
        created_by=CREATED_BY,
        owner_id=owner_id,
        visibility=visibility,
    )
    return {
        "outcome": OUTCOME_CANDIDATE,
        "goal_id": goal["id"],
        "goal_created": goal.get("created", False),
        "procedure_id": (procedure or {}).get("id") if isinstance(procedure, dict) else procedure,
        "verification_state": "unverified",
        "extraction_version": COMPILER_VERSION,
    }

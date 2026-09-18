"""
Cases A-E from the product spec's Sec 17 -- fixture data only (goal, local
Claims, candidates with structured preconditions, and the expected outcome),
consumed by test_baselines_offline.py's A/B/C comparison. No DB, no live
model calls: candidates are plain dicts shaped like `find_applicable_
procedures()`'s own survivor rows (the same shape applicability_judge.py's
`extract_requirement_conditions` already reads), so these fixtures double as
regression data for the real pipeline if a future e2e pass wants to load
them against a live Postgres instance.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalCandidate:
    candidate_id: str
    goal: str
    similarity: float  # the ONLY signal baseline A ever sees
    preconditions: list[dict] = field(default_factory=list)  # REQUIRED, structured
    hard_invariant_violated: bool = False  # simulates baseline B's structured-precondition cascade


@dataclass
class EvalCase:
    name: str
    goal: str
    claims: list[dict]
    candidates: list[EvalCandidate]
    # ground truth for metrics: ids that SHOULD be relevant/applicable, and
    # ids that MUST be excluded from a claim-aware ranking's top results.
    relevant_ids: set[str]
    must_reject_ids: set[str]
    must_not_reject_ids: set[str]


CASE_A = EvalCase(
    name="A_generated_client_vs_schema",
    goal="modify generated API client",
    claims=[
        {"claim_id": "C17", "statement": "generated_client.ts is derived output, do not hand-edit"},
        {"claim_id": "C21", "statement": "schema_api_yaml is the source of truth for the client"},
        {"claim_id": "C29", "statement": "generator command is pnpm generate-api"},
    ],
    candidates=[
        EvalCandidate(
            candidate_id="edit_generated_file", goal="edit generated_client.ts directly",
            similarity=0.92,  # highest surface similarity -- the exact trap the spec names
            preconditions=[{"subject": "generated_client.ts", "predicate": "is", "object": "hand-editable"}],
        ),
        EvalCandidate(
            candidate_id="edit_schema_and_regenerate", goal="edit schema_api_yaml then run generator command",
            similarity=0.81,
            preconditions=[{"subject": "schema_api_yaml", "predicate": "is", "object": "source of truth"}],
        ),
    ],
    relevant_ids={"edit_schema_and_regenerate"},
    must_reject_ids={"edit_generated_file"},
    must_not_reject_ids={"edit_schema_and_regenerate"},
)

CASE_B = EvalCase(
    name="B_docker_unavailable_rejects",
    goal="run integration tests",
    claims=[{"claim_id": "C-docker", "statement": "docker engine is unavailable on this host"}],
    candidates=[
        EvalCandidate(
            candidate_id="docker_integration_tests", goal="run integration tests via docker compose",
            similarity=0.88,
            preconditions=[{"subject": "docker", "predicate": "engine", "object": "available"}],
        ),
    ],
    relevant_ids=set(),
    must_reject_ids={"docker_integration_tests"},
    must_not_reject_ids=set(),
)

CASE_C = EvalCase(
    name="C_no_docker_claim_is_unknown_not_reject",
    goal="run integration tests",
    claims=[],  # deliberately empty -- no Docker claim exists at all
    candidates=[
        EvalCandidate(
            candidate_id="docker_integration_tests", goal="run integration tests via docker compose",
            similarity=0.88,
            preconditions=[{"subject": "docker", "predicate": "engine", "object": "available"}],
        ),
    ],
    relevant_ids={"docker_integration_tests"},
    must_reject_ids=set(),
    must_not_reject_ids={"docker_integration_tests"},
)

CASE_D = EvalCase(
    name="D_lsp_available_both_valid",
    goal="find symbol references",
    claims=[{"claim_id": "C-lsp", "statement": "working lsp server is available for this repository"}],
    candidates=[
        EvalCandidate(candidate_id="ripgrep_search", goal="grep for symbol text across the repo", similarity=0.70),
        EvalCandidate(
            candidate_id="lsp_references", goal="use lsp server to find symbol references", similarity=0.75,
            preconditions=[{"subject": "lsp_server", "predicate": "is", "object": "available"}],
        ),
    ],
    relevant_ids={"ripgrep_search", "lsp_references"},
    must_reject_ids=set(),
    must_not_reject_ids={"ripgrep_search", "lsp_references"},
)

CASE_E = EvalCase(
    name="E_lsp_unavailable_demoted_or_rejected",
    goal="find symbol references",
    claims=[{"claim_id": "C-lsp-down", "statement": "lsp server is unavailable for this repository"}],
    candidates=[
        EvalCandidate(candidate_id="ripgrep_search", goal="grep for symbol text across the repo", similarity=0.70),
        EvalCandidate(
            candidate_id="lsp_references", goal="use lsp server to find symbol references", similarity=0.75,
            preconditions=[{"subject": "lsp_server", "predicate": "is", "object": "available"}],
        ),
    ],
    relevant_ids={"ripgrep_search"},
    must_reject_ids={"lsp_references"},
    must_not_reject_ids={"ripgrep_search"},
)

ALL_CASES = [CASE_A, CASE_B, CASE_C, CASE_D, CASE_E]

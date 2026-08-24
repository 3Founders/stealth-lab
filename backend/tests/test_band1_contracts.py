"""Band 1 proving tests — contracts proven offline (no database).

Covers:
  Band 1.3/1.2 — V0 gate: scope + provenance validation, every rejection
                 path and the accept paths.
  Band 1.5    — UUIDv7: RFC 9562 field positions, ordering, uniqueness.
  Migration   — static assertions that db/21_band1_contracts.sql contains the
                DDL each contract depends on (the DB application itself is an
                integration concern; the contract text is checkable here).

Appendix C rows exercised: #6 (provenance at boundary), partially #14/#19
(birth discipline), and the V0 gate referenced by rows for #15/#18.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from app.services.v0_gate import (
    V0Violation,
    validate_provenance,
    validate_scope,
)


# ---------------------------------------------------------------- V0 scope

def test_v0_rejects_missing_scope():
    with pytest.raises(V0Violation, match="scope_type is required"):
        validate_scope(None)


def test_v0_rejects_unknown_scope_type():
    with pytest.raises(V0Violation, match="unknown scope_type"):
        validate_scope("galaxy")


def test_v0_nonglobal_requires_entity():
    with pytest.raises(V0Violation, match="requires scope_entity_id"):
        validate_scope("project", None)
    assert validate_scope("project", "repo_42") == ("project", "repo_42")


def test_v0_global_cannot_carry_entity():
    with pytest.raises(V0Violation, match="cannot carry an entity_id"):
        validate_scope("global", "repo_42")
    assert validate_scope("global", None) == ("global", None)


def test_v0_all_ten_scope_types_accepted():
    for st in ("global", "organization", "team", "project", "repository",
               "branch", "user", "session", "task", "entity"):
        entity = None if st == "global" else f"{st}_x"
        assert validate_scope(st, entity) == (st, entity)


# ----------------------------------------------------------- V0 provenance

def test_v0_rejects_missing_provenance():
    with pytest.raises(V0Violation, match="provenance is required"):
        validate_provenance(None)


def test_v0_rejects_unknown_provenance():
    with pytest.raises(V0Violation, match="unknown provenance"):
        validate_provenance("company_vibes")


@pytest.mark.parametrize("p", [
    "company_ingested", "company_debate", "public_generated",
    "prior_library", "system_pending_review",
])
def test_v0_accepts_all_enum_values(p):
    assert validate_provenance(p) == p


def test_v0_derived_objects_require_extractor_version():
    with pytest.raises(V0Violation, match="extractor_version"):
        validate_provenance("public_generated", derived=True)


@pytest.mark.parametrize("p", ["company_ingested"])
def test_v0_derived_objects_cannot_be_company_ingested(p):
    with pytest.raises(V0Violation, match="cannot be company_ingested"):
        validate_provenance(p, derived=True, extractor_version="x@1")


def test_v0_derived_accepts_public_generated_with_version():
    assert validate_provenance(
        "public_generated", derived=True, extractor_version="det@1"
    ) == "public_generated"


# ---------------------------------------------------------------- UUIDv7

def test_uuid7_rfc_fields():
    u = uuid.uuid7 if hasattr(uuid, "uuid7") else None  # stdlib may shadow
    from app.utils.ids import uuid7 as our_uuid7
    got = our_uuid7()
    b = got.bytes
    assert (b[6] >> 4) == 7, "version nibble must be 7"
    assert (b[8] >> 6) == 0b10, "variant bits must be 10"


def test_uuid7_ordering_within_and_across_ms():
    from app.utils.ids import uuid7 as our_uuid7
    ids = [our_uuid7() for _ in range(50)]
    assert ids == sorted(ids), "same-process v7 ids must be time-ordered"


def test_uuid7_uniqueness_bulk():
    from app.utils.ids import uuid7 as our_uuid7
    seen = {str(our_uuid7()) for _ in range(5000)}
    assert len(seen) == 5000


# --------------------------------------------- migration DDL static checks

def _ddl() -> str:
    p = Path(__file__).resolve().parents[1] / "db" / "21_band1_contracts.sql"
    return p.read_text(encoding="utf-8")


def test_migration_adds_scope_columns_to_all_seven_tables():
    ddl = _ddl()
    for t in ("knowledge_nodes", "task_nodes", "edges", "procedures",
              "observations", "episodes", "agent_traces"):
        # whitespace-tolerant: SQL column alignment is legal, must not
        # break the contract assertion (caught via task_nodes padding)
        pattern = rf"ALTER\s+TABLE\s+{t}\s+ADD COLUMN IF NOT EXISTS scope_type\b"
        assert re.search(pattern, ddl), t


def test_migration_adds_claim_shape_columns():
    ddl = _ddl()
    for col in ("subject", "predicate", "object", "proposition_type",
                "claim_status", "belief_score", "belief_method"):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in ddl, col


def test_migration_adds_embedding_stamps():
    ddl = _ddl()
    assert "embedding_model_id" in ddl and "embedding_dim" in ddl


def test_migration_adds_pending_review_provenance_value():
    ddl = _ddl()
    assert "system_pending_review" in ddl


def test_migration_has_no_backfill_statements():
    ddl = _ddl()
    assert "UPDATE " not in ddl.upper().replace("UPPER", ""), \
        "fresh-start ruling: migration must not contain data backfills"


# --------------------------------------- review-fix migration static checks

def _ddl22() -> str:
    p = Path(__file__).resolve().parents[1] / "db" / "22_band1_review_fixes.sql"
    return p.read_text(encoding="utf-8")


def test_review_fixes_add_scope_check_constraints_for_all_seven_tables():
    ddl = _ddl22()
    for t in ("knowledge_nodes", "task_nodes", "edges", "procedures",
              "observations", "episodes", "agent_traces"):
        assert f"scope_type_chk_{t}" in ddl, t


def test_review_fixes_drop_duplicate_validity_columns():
    ddl = _ddl22()
    assert "DROP COLUMN IF EXISTS valid_from" in ddl
    assert "DROP COLUMN IF EXISTS valid_until" in ddl


def test_v0_gate_no_dead_constants():
    import app.services.v0_gate as gate
    assert not hasattr(gate, "DERIVED_PROVENANCE"), (
        "dead constant removed by review — derived rules live in "
        "validate_provenance(derived=True), not in unused tuples"
    )

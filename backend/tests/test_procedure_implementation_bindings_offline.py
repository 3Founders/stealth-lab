"""
MCP hardening B23/B24: pure-logic half of the Procedure<->Implementation
relation -- role validation needs no database.
"""
import pytest

from app.services.procedure_implementation_bindings import (
    ROLES,
    STATUSES,
    ProcedureImplementationBindingError,
    link_implementation,
)


def test_roles_and_statuses_match_the_real_procedure_implementations_check_constraints():
    assert ROLES == ("primary", "supporting", "partial", "verification")
    assert STATUSES == ("candidate", "active", "deprecated", "disabled", "quarantined")


def test_link_implementation_rejects_invalid_role():
    import asyncio

    async def _run():
        with pytest.raises(ProcedureImplementationBindingError, match="role"):
            await link_implementation(
                pool=None, procedure_id="00000000-0000-0000-0000-000000000000",
                implementation_id="00000000-0000-0000-0000-000000000000",
                role="not_a_real_role",
            )

    asyncio.run(_run())

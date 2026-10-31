"""Pilot enrolment: everyone who signs in joins the capped pilot organization, and nothing else changes."""
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

from app.services import pilot_enrol

ORG = "11111111-2222-3333-4444-555555555555"
USER = "99999999-8888-7777-6666-555555555555"


class FakeConn:
    def __init__(self, org_exists=True, insert_returns=1, boom=False):
        self.org_exists, self.insert_returns, self.boom = org_exists, insert_returns, boom
        self.inserts = []

    async def fetchval(self, sql, *args):
        if self.boom:
            raise RuntimeError("db down")
        if sql.lstrip().upper().startswith("SELECT"):
            return 1 if self.org_exists else None
        self.inserts.append((sql, args))
        return self.insert_returns


def _patch(monkeypatch, conn):
    @contextlib.asynccontextmanager
    async def fake_tx(pool, scope):
        yield conn
    monkeypatch.setattr(pilot_enrol, "tenant_transaction", fake_tx)


def run(coro):
    return asyncio.run(coro)


def test_pilot_org_id_reads_only_a_valid_uuid():
    assert pilot_enrol.pilot_org_id({}) is None
    assert pilot_enrol.pilot_org_id({pilot_enrol.PILOT_ORG_ENV: "not-a-uuid"}) is None
    assert pilot_enrol.pilot_org_id({pilot_enrol.PILOT_ORG_ENV: ORG.upper()}) == ORG


def test_off_without_the_variable(monkeypatch):
    conn = FakeConn()
    _patch(monkeypatch, conn)
    assert run(pilot_enrol.ensure_pilot_member(object(), user_id=USER, env={})) is False
    assert conn.inserts == []


def test_enrols_a_new_member_as_member_only(monkeypatch):
    conn = FakeConn()
    _patch(monkeypatch, conn)
    env = {pilot_enrol.PILOT_ORG_ENV: ORG}
    assert run(pilot_enrol.ensure_pilot_member(object(), user_id=USER, org_ids=(), env=env)) is True
    (sql, args), = conn.inserts
    assert "INSERT INTO org_memberships" in sql and "ON CONFLICT DO NOTHING" in sql
    assert args == (ORG, USER, "10000000-0000-0000-0000-000000000003")      # the member role, never admin or owner


def test_already_a_member_writes_nothing(monkeypatch):
    conn = FakeConn()
    _patch(monkeypatch, conn)
    env = {pilot_enrol.PILOT_ORG_ENV: ORG}
    assert run(pilot_enrol.ensure_pilot_member(object(), user_id=USER, org_ids=(ORG.upper(),), env=env)) is False
    assert conn.inserts == []


def test_a_missing_org_or_a_database_error_never_breaks_sign_in(monkeypatch):
    env = {pilot_enrol.PILOT_ORG_ENV: ORG}
    conn = FakeConn(org_exists=False)
    _patch(monkeypatch, conn)
    assert run(pilot_enrol.ensure_pilot_member(object(), user_id=USER, env=env)) is False
    assert conn.inserts == []
    _patch(monkeypatch, FakeConn(boom=True))
    assert run(pilot_enrol.ensure_pilot_member(object(), user_id=USER, env=env)) is False


def test_the_shipped_connections_file_loads_and_is_platform_owned_with_prices():
    from app.providers.registry import load_connections_file

    path = Path(__file__).resolve().parents[1] / "deploy" / "provider_connections.json"
    conns = {c.connection_id: c for c in load_connections_file(str(path))}
    assert set(conns) == {"zai-glm", "general-compute"}
    for c in conns.values():
        assert c.owner == "platform" and c.credential_owner == "platform"
        assert c.credential_ref.startswith("env:")                    # a reference, never the secret
        assert "PUBLIC_SOURCE" in c.allowed_data_classes
        # private code may go to a connection only if it DECLARES zero retention (the founders' statement, 2026-11-01), and the
        # classes above that (confidential, personal) never ride the shipped file
        if {"USER_PRIVATE", "ORG_PRIVATE"} & set(c.allowed_data_classes):
            assert c.zdr is True
        assert not {"CONFIDENTIAL_DATA", "PERSONAL_DATA"} & set(c.allowed_data_classes)
        assert all(u.input_per_mtok and u.output_per_mtok for u in c.units)   # priced, so the budget can be held
    assert "glm-5.3-flash|direct" in [u.unit for u in conns["zai-glm"].units]
    assert "sk-" not in path.read_text(encoding="utf-8")


def test_setup_pilot_dry_run_writes_nothing():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import setup_pilot

    class Conn:
        writes = 0

        async def fetchrow(self, *a):
            return None

        async def execute(self, *a):
            Conn.writes += 1

    out = run(setup_pilot.setup(Conn(), apply=False))
    assert out["dry_run"] is True and Conn.writes == 0
    assert out["per_user_daily_usd"] == "1.00" and "USER_PRIVATE" not in out["data_classes"]
    assert set(out["models"]) == {"glm-5.3-flash", "gemma-4-31b-it"}

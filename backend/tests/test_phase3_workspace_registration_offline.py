"""Phase 3 (launch compliance) — hosted workspace registration (LC-001).

Offline. Registration binds a SERVER-SIDE path, so: owner/admin role only;
path is canonicalized and must sit under an allowed root (no '/etc', no
home dir, no traversal); a `workspace_registered` audit event is emitted.
Resolution/isolation (foreign tenant -> NotFound, path mismatch refused)
stays covered by test_phase1_security_boundaries_offline.py.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.services import workspace_registry as wr


class _Pool:
    def __init__(self):
        self.audits = []

    async def fetchrow(self, sql, *a):
        f = " ".join(sql.split())
        if "INSERT INTO registered_workspaces" in f:
            return {"id": "ws-1", "tenant_id": a[0], "name": a[1],
                    "storage_path": a[2], "default_branch": a[3]}
        if "INSERT INTO audit_events" in f:
            self.audits.append((a[2], a[4]))
            return {"id": len(self.audits)}
        raise AssertionError(f[:70])


def _reg(pool, root, sub="main-repo", **over):
    kw = dict(
        actor_subject="alice", actor_user_id="u1", tenant_id="org-1",
        actor_role="owner", name="main-repo",
        storage_path=os.path.join(root, sub), default_branch="main",
        root_allowlist=(str(root),),
    )
    kw.update(over)
    return asyncio.run(wr.register_workspace(pool, **kw))


def test_owner_registers_a_workspace_under_an_allowed_root(tmp_path):
    root = tmp_path / "workspaces"
    (root / "main-repo").mkdir(parents=True)
    pool = _Pool()
    ws = _reg(pool, root)
    assert ws.storage_path == os.path.realpath(str(root / "main-repo"))
    assert ("workspace_registered", "ws-1") in pool.audits


def test_plain_member_cannot_register(tmp_path):
    with pytest.raises(wr.WorkspaceNotAuthorized):
        _reg(_Pool(), tmp_path, actor_role="member")


def test_path_outside_the_allowlist_is_rejected(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(wr.WorkspacePathRejected):
        _reg(_Pool(), tmp_path / "workspaces", sub="x",
             storage_path=str(other), root_allowlist=(str(tmp_path / "workspaces"),))


def test_traversal_in_path_is_rejected(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    with pytest.raises(wr.WorkspacePathRejected):
        _reg(_Pool(), root, storage_path=str(root / ".." / ".." / "etc"))

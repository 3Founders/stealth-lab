p = r"C:\Users\user\stealth-lab\backend\tests\test_tenant_isolation_p1a_e2e.py"
s = open(p, encoding="utf-8").read()

a = '''            # fallback (migration 29): sl_tenant_scope_allows() allows any row when app.tenant_id is unset. This is the
            # deferred gap of securityp1.md §5.1 #3 (final_prod_docs/p1_results.md). When it is closed, flip this.
            assert await pool.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", str(uuid.uuid4())) is True'''
assert a in s
s = s.replace(a, '''            # migration 150 closed securityp1.md §5.1 #3: with app.tenant_id unset, the db/29 tables allow ONLY the shared
            # commons tenant's rows -- any other tenant's row is invisible -- and the system scope is explicit
            commons, other = "00000000-0000-0000-0000-000000000001", str(uuid.uuid4())
            assert await pool.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", commons) is True
            assert await pool.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", other) is False
            async with tenant_transaction(pool, TenantScope.for_tenant(other)) as conn:
                assert await conn.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", other) is True
                assert await conn.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", commons) is False
            async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:      # the explicit system scope
                assert await conn.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", other) is True''')
s = s.replace("def test_without_a_tenant_setting_strict_tables_see_nothing_and_fallback_tables_are_the_known_gap():",
              "def test_without_a_tenant_setting_strict_tables_see_nothing_and_the_db29_tables_see_only_the_commons():")
s = s.replace('''  #6  a transaction that forgets tenant_transaction: strict (migration 136) tables see and write nothing; the
      migration-29 fallback tables still allow it -- pinned here as the KNOWN, deferred gap (securityp1.md §5.1 #3)''',
              '''  #6  a transaction that forgets tenant_transaction: strict (migration 136) tables see and write nothing; the
      db/29 tables see only the shared commons (migration 150 closed the permissive fallback)
  RLS migration 150: sync tables deny without an owner bound; routing logs show 'public' rows to everyone and other
      rows only to their owner's / organisation's readers''')

s += '''


# ---------------------------------------------------------------- migration 150: sync and routing tables (database)

async def _restricted_pool():
    from app.db.session import create_pool

    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    if await pool.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"):
        await pool.close()
        pytest.skip("connected as a role that bypasses RLS; run as stealth_app (scripts/sql/create_app_role.sql)")
    return pool


@needs_db
def test_sync_rows_are_invisible_without_their_owner_bound_and_a_conflict_is_still_refused():
    from app.services.access import scoped_transaction
    from app.stealth import project_sync as ps

    async def go():
        pool = await _restricted_pool()
        project = str(uuid.uuid4())
        alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"
        try:
            await ps.sync_project(pool, project_id=project, owner_subject=alice)
            # unscoped and wrong-owner reads see nothing; the owner sees her row
            assert await pool.fetchval("SELECT count(*) FROM synced_projects WHERE project_id = $1::uuid", project) == 0
            async with scoped_transaction(pool, owner=bob) as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM synced_projects WHERE project_id = $1::uuid", project) == 0
            assert (await ps.get_synced_project(pool, project_id=project, owner_subject=alice)) is not None
            assert await ps.get_synced_project(pool, project_id=project, owner_subject=bob) is None
            assert [r["project_id"] for r in await ps.list_synced_projects(pool, owner_subject=bob)] == []
            # the cross-owner checks still work, without revealing the other owner
            prev = await ps.preview_sync(pool, project_id=project, owner_subject=bob)
            assert prev["synced_by_someone_else"] is True and "owner_subject" not in prev
            with pytest.raises(ps.AlreadySyncedToAnotherAccount):
                await ps.sync_project(pool, project_id=project, owner_subject=bob)
            # a write that names another owner is refused by the policy itself
            with pytest.raises(Exception):
                async with scoped_transaction(pool, owner=bob) as conn:
                    await conn.execute("INSERT INTO synced_projects (project_id, owner_subject) VALUES ($1::uuid, $2)",
                                       str(uuid.uuid4()), alice)
            assert await ps.unsync_project(pool, project_id=project, owner_subject=bob) is False
            assert await ps.unsync_project(pool, project_id=project, owner_subject=alice) is True
        finally:
            async with scoped_transaction(pool, system=True) as conn:
                await conn.execute("DELETE FROM synced_projects WHERE project_id = $1::uuid", project)
            await pool.close()
    run(go())


@needs_db
def test_routing_logs_show_public_rows_to_all_and_private_rows_only_to_their_readers():
    from app.routing import store
    from app.services.access import AccessScope as Scope

    async def go():
        pool = await _restricted_pool()
        gid, org = str(uuid.uuid4()), str(uuid.uuid4())
        alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"
        keys = {}
        try:
            for name, vis, owner in (("pub", "public", None), ("priv", "private", alice), ("org", "org", org)):
                keys[name] = f"{gid}.{uuid.uuid4().hex[:16]}"
                with store.routing_scope(system=True):
                    await store.record_decision(pool, {
                        "id": str(uuid.uuid4()), "goal_id": gid, "instance_key": keys[name], "candidates": ["m|s"],
                        "ladder": ["m|s"], "propensity": 1.0, "meets_target": True, "predicted": {},
                        "constraints": {}, "visibility": vis, "owner_id": owner})

            async def seen(scope=None, system=False):
                with store.routing_scope(scope, system=system):
                    out = set()
                    for name, key in keys.items():
                        if await store.instance_decision(pool, gid, key) is not None:
                            out.add(name)
                    return out

            assert await seen() == {"pub"}                                          # no scope: public only
            assert await seen(Scope.anonymous()) == {"pub"}
            assert await seen(Scope.for_user(bob)) == {"pub"}                       # another user
            assert await seen(Scope.for_user(alice)) == {"pub", "priv"}             # the owner
            assert await seen(Scope.for_org_member(bob, [org])) == {"pub", "org"}  # an org member
            assert await seen(system=True) == {"pub", "priv", "org"}                # workers
            # a private write outside its owner's scope is refused by the policy (fails closed)
            with pytest.raises(Exception):
                with store.routing_scope(Scope.for_user(bob)):
                    await store.record_decision(pool, {
                        "id": str(uuid.uuid4()), "goal_id": gid, "instance_key": f"{gid}.x", "candidates": [],
                        "ladder": [], "propensity": 1.0, "meets_target": True, "predicted": {}, "constraints": {},
                        "visibility": "private", "owner_id": alice})
        finally:
            with store.routing_scope(system=True):
                log = await store._goal_log_pool(pool, gid)
                await store._log_call(log, "execute", "DELETE FROM routing_decisions WHERE goal_id = $1::uuid", gid)
            await pool.close()
    run(go())


def test_routing_rows_of_an_org_goal_are_owned_by_the_organisation():
    from app.routing.store import routing_owner

    assert routing_owner("org", "creator-user", "org-1") == "org-1"
    assert routing_owner("private", "u1", "org-1") == "u1"
    assert routing_owner("public", None, None) is None
    assert routing_owner("org", "creator-user", None) == "creator-user"      # no tenant recorded: the owner
'''
open(p, "w", encoding="utf-8").write(s)
print("ok")

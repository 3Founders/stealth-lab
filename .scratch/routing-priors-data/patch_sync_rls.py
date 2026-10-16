"""Adopt the migration-150 owner scope in the sync code (project_sync.py, sync_device_identity.py, api/me.py)."""
B = r"C:\Users\user\stealth-lab\backend"


def patch(path, reps):
    p = f"{B}\\{path}"
    s = open(p, encoding="utf-8").read()
    for a, b in reps:
        assert s.count(a) == 1, (path, a[:80])
        s = s.replace(a, b)
    open(p, "w", encoding="utf-8", newline="").write(s)


patch(r"app\stealth\project_sync.py", [
    # preview: a cross-owner EXISTENCE check -- system scope, returns only booleans
    ('''    row = await pool.fetchrow(
        "SELECT owner_subject FROM synced_projects WHERE project_id = $1::uuid", project_id,
    )
    if row is None:
        return {"project_id": project_id, "already_synced_by_you": False, "synced_by_someone_else": False}''',
     '''    from app.services.access import scoped_transaction

    # Cross-owner by design (it must say "synced by someone else" without naming them): the one place this module
    # reads under the system scope (migration 150), and it returns only booleans.
    async with scoped_transaction(pool, system=True) as conn:
        row = await conn.fetchrow(
            "SELECT owner_subject FROM synced_projects WHERE project_id = $1::uuid", project_id,
        )
    if row is None:
        return {"project_id": project_id, "already_synced_by_you": False, "synced_by_someone_else": False}'''),
    # sync_project: insert-or-verify needs to see a conflicting owner's row -> system scope, compared in Python
    ('''    row = await pool.fetchrow(
        """
        INSERT INTO synced_projects (project_id, owner_subject)
        VALUES ($1::uuid, $2)
        ON CONFLICT (project_id) DO NOTHING
        RETURNING *
        """,
        project_id, owner_subject,
    )
    if row is None:
        row = await pool.fetchrow("SELECT * FROM synced_projects WHERE project_id = $1::uuid", project_id)
        if row["owner_subject"] != owner_subject:
            raise AlreadySyncedToAnotherAccount(project_id)
    return dict(row)''',
     '''    from app.services.access import scoped_transaction

    # The conflict check must see another owner's row to refuse it; the system scope is confined to this
    # transaction, and nothing of the other owner's row leaves this function.
    async with scoped_transaction(pool, system=True) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO synced_projects (project_id, owner_subject)
            VALUES ($1::uuid, $2)
            ON CONFLICT (project_id) DO NOTHING
            RETURNING *
            """,
            project_id, owner_subject,
        )
        if row is None:
            row = await conn.fetchrow("SELECT * FROM synced_projects WHERE project_id = $1::uuid", project_id)
    if row["owner_subject"] != owner_subject:
        raise AlreadySyncedToAnotherAccount(project_id)
    return dict(row)'''),
    # record_sync_upload: runs as the device's owner
    ('''async def record_sync_upload(
    pool: asyncpg.Pool, *, project_id: str, revision: int, ciphertext: bytes,''',
     '''async def record_sync_upload(
    pool: asyncpg.Pool, *, project_id: str, owner_subject: str, revision: int, ciphertext: bytes,'''),
    ('''    current = await pool.fetchrow("SELECT revision FROM synced_projects WHERE project_id = $1::uuid", project_id)
    if current is None:''',
     '''    from app.services.access import scoped_transaction

    async with scoped_transaction(pool, owner=owner_subject) as conn:
        current = await conn.fetchrow("SELECT revision FROM synced_projects WHERE project_id = $1::uuid", project_id)
    if current is None:'''),
    ('''    row = await pool.fetchrow(
        """
        UPDATE synced_projects
        SET snapshot_sha256 = $2, snapshot_locator = $3, snapshot_size_bytes = $4,
            bootstrapped_at = COALESCE(bootstrapped_at, now()), revision = $5,
            wrapped_p_dek = COALESCE($6, wrapped_p_dek),
            recovery_salt = COALESCE($7, recovery_salt),
            kdf_params = COALESCE($8::jsonb, kdf_params)
        WHERE project_id = $1::uuid
        RETURNING *
        """,
        project_id, blob["sha256"], blob["locator"], blob["size"], revision,
        wrapped_p_dek, recovery_salt, json.dumps(kdf_params) if kdf_params is not None else None,
    )
    return dict(row)''',
     '''    async with scoped_transaction(pool, owner=owner_subject) as conn:
        row = await conn.fetchrow(
            """
            UPDATE synced_projects
            SET snapshot_sha256 = $2, snapshot_locator = $3, snapshot_size_bytes = $4,
                bootstrapped_at = COALESCE(bootstrapped_at, now()), revision = $5,
                wrapped_p_dek = COALESCE($6, wrapped_p_dek),
                recovery_salt = COALESCE($7, recovery_salt),
                kdf_params = COALESCE($8::jsonb, kdf_params)
            WHERE project_id = $1::uuid
            RETURNING *
            """,
            project_id, blob["sha256"], blob["locator"], blob["size"], revision,
            wrapped_p_dek, recovery_salt, json.dumps(kdf_params) if kdf_params is not None else None,
        )
    if row is None:
        raise ValueError(f"project {project_id} is not synced")
    return dict(row)'''),
    ('''    rows = await pool.fetch(
        "SELECT * FROM synced_projects WHERE owner_subject = $1 ORDER BY synced_at DESC",
        owner_subject,
    )
    return [dict(r) for r in rows]''',
     '''    from app.services.access import scoped_transaction

    async with scoped_transaction(pool, owner=owner_subject) as conn:
        rows = await conn.fetch(
            "SELECT * FROM synced_projects WHERE owner_subject = $1 ORDER BY synced_at DESC",
            owner_subject,
        )
    return [dict(r) for r in rows]'''),
    ('''    row = await pool.fetchrow(
        "SELECT * FROM synced_projects WHERE project_id = $1::uuid AND owner_subject = $2",
        project_id, owner_subject,
    )
    return dict(row) if row else None''',
     '''    from app.services.access import scoped_transaction

    async with scoped_transaction(pool, owner=owner_subject) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM synced_projects WHERE project_id = $1::uuid AND owner_subject = $2",
            project_id, owner_subject,
        )
    return dict(row) if row else None'''),
    ('''    row = await pool.fetchrow(
        "DELETE FROM synced_projects WHERE project_id = $1::uuid AND owner_subject = $2 RETURNING snapshot_sha256",
        project_id, owner_subject,
    )
    if row is None:
        return False''',
     '''    from app.services.access import scoped_transaction

    async with scoped_transaction(pool, owner=owner_subject) as conn:
        row = await conn.fetchrow(
            "DELETE FROM synced_projects WHERE project_id = $1::uuid AND owner_subject = $2 RETURNING snapshot_sha256",
            project_id, owner_subject,
        )
    if row is None:
        return False'''),
])

patch(r"app\services\sync_device_identity.py", [
    ('''    token, jti = _mint(cfg, owner_subject=owner_subject, project_id=project_id, ttl_seconds=ttl_seconds)
    await pool.execute(
        "INSERT INTO sync_device_credentials (credential_id, owner_subject, project_id, fingerprint, scope, expires_at) "
        "VALUES ($1, $2, $3::uuid, $4, $5, now() + make_interval(secs => $6))",
        jti, owner_subject, project_id, token_fingerprint(token), SYNC_UPLOAD_SCOPE, float(ttl_seconds),
    )
    return token''',
     '''    from app.services.access import scoped_transaction

    token, jti = _mint(cfg, owner_subject=owner_subject, project_id=project_id, ttl_seconds=ttl_seconds)
    async with scoped_transaction(pool, owner=owner_subject) as conn:
        await conn.execute(
            "INSERT INTO sync_device_credentials (credential_id, owner_subject, project_id, fingerprint, scope, "
            "expires_at) VALUES ($1, $2, $3::uuid, $4, $5, now() + make_interval(secs => $6))",
            jti, owner_subject, project_id, token_fingerprint(token), SYNC_UPLOAD_SCOPE, float(ttl_seconds),
        )
    return token'''),
    ('''    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = 'rotated' "''',
     '''    from app.services.access import bind_scope

    async with pool.acquire() as conn:
        async with conn.transaction():
            await bind_scope(conn, owner=ctx.owner_subject)
            await conn.execute(
                "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = 'rotated' "'''),
    ('''    result = await pool.execute(
        "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = $3 "
        "WHERE credential_id = $1 AND owner_subject = $2 AND revoked_at IS NULL",
        credential_id, owner_subject, reason,
    )
    return result.endswith(" 1")''',
     '''    from app.services.access import scoped_transaction

    async with scoped_transaction(pool, owner=owner_subject) as conn:
        result = await conn.execute(
            "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = $3 "
            "WHERE credential_id = $1 AND owner_subject = $2 AND revoked_at IS NULL",
            credential_id, owner_subject, reason,
        )
    return result.endswith(" 1")'''),
    ('''    await pool.execute(
        "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = $3 "
        "WHERE project_id = $1::uuid AND owner_subject = $2 AND revoked_at IS NULL",
        project_id, owner_subject, reason,
    )''',
     '''    from app.services.access import scoped_transaction

    async with scoped_transaction(pool, owner=owner_subject) as conn:
        await conn.execute(
            "UPDATE sync_device_credentials SET revoked_at = now(), revoked_reason = $3 "
            "WHERE project_id = $1::uuid AND owner_subject = $2 AND revoked_at IS NULL",
            project_id, owner_subject, reason,
        )'''),
    ('''    row = await pool.fetchrow(
        "SELECT revoked_at, expires_at FROM sync_device_credentials "
        "WHERE credential_id = $1 AND owner_subject = $2 AND project_id = $3::uuid",
        jti, owner_subject, project_id,
    )''',
     '''    from app.services.access import scoped_transaction

    # owner_subject comes from the signature-verified claims above, so the owner scope is the token's own
    async with scoped_transaction(pool, owner=owner_subject) as conn:
        row = await conn.fetchrow(
            "SELECT revoked_at, expires_at FROM sync_device_credentials "
            "WHERE credential_id = $1 AND owner_subject = $2 AND project_id = $3::uuid",
            jti, owner_subject, project_id,
        )'''),
])

patch(r"app\api\me.py", [
    ('''        row = await record_sync_upload(
            pool, project_id=project_id, revision=revision, ciphertext=ciphertext,''',
     '''        row = await record_sync_upload(
            pool, project_id=project_id, owner_subject=device.owner_subject, revision=revision, ciphertext=ciphertext,'''),
])
print("ok")

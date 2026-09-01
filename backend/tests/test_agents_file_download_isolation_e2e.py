"""
Live-database proving test for `GET /v1/agents/files/{file_id}`
(app/api/agents.py::download_file).

REAL GAP FIXED THIS PASS: `generated_files.scope_key` is documented at the
schema level (db/06_generated_files.sql) as "who generated it, for
cleanup/ownership", but the handler used to `SELECT ... WHERE id = $1`
and never actually checked it -- so anyone who obtained a file's opaque
id (proxy/browser logs, a shared link, a screen-shared response body)
could download another caller's generated output, including the
extracted-medical-report Excel this router's own module docstring names
as real PII. This test proves the fix through the real router: the
identity that generated a file (`scope_key_for`, the exact same key
stamped on the row at upload time) is the only one that can retrieve it
back; a different viewer -- authenticated as a different `X-Viewer-Id`,
or anonymous behind a different client IP -- gets the same 404 a
nonexistent id would (anti-enumeration, not a 403 confirming the id is
real).

Same pattern as every other `*_e2e.py` file in this suite: requires a
real DATABASE_URL, skips (not fails) without one. Bypasses the actual
PDF-upload/extraction pipeline (expensive, needs real files + LLM
credentials) and instead writes the `generated_files` row directly with
the same shape `run_medical_report_extraction` itself inserts -- this
test's own setup, not part of what it asserts. The assertion path is
100% the real `download_file` handler through a real ASGI request.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.agents import router as agents_router
from app.db.session import create_pool

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "agents-file-dl-iso-e2e"


async def _cleanup(pool) -> None:
    await pool.execute("DELETE FROM generated_files WHERE display_name LIKE $1", f"{PREFIX}%")


def _app(pool) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router)
    app.state.pool = pool
    return app


def test_download_is_scoped_to_the_generating_viewer_not_the_bare_file_id():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            fd, disk_path = tempfile.mkstemp(prefix=f"{PREFIX}-", suffix=".xlsx")
            os.close(fd)
            with open(disk_path, "wb") as f:
                f.write(b"alice's real extracted medical-report data\n")

            try:
                row = await pool.fetchrow(
                    "INSERT INTO generated_files (disk_path, display_name, content_type, scope_key) "
                    "VALUES ($1, $2, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', $3) "
                    "RETURNING id",
                    disk_path, f"{PREFIX}-combined.xlsx", "viewer:alice",
                )
                file_id = str(row["id"])

                app = _app(pool)
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    # alice generated it -- her own session can download it.
                    resp = await client.get(
                        f"/v1/agents/files/{file_id}", headers={"X-Viewer-Id": "alice"},
                    )
                    assert resp.status_code == 200, resp.text
                    assert resp.content == b"alice's real extracted medical-report data\n"

                    # bob obtained the same opaque id (log, shared link,
                    # screenshot) -- his own, real, authenticated session
                    # must NOT be able to download alice's file.
                    resp = await client.get(
                        f"/v1/agents/files/{file_id}", headers={"X-Viewer-Id": "bob"},
                    )
                    assert resp.status_code == 404, (
                        "bob's authenticated session downloaded alice's generated "
                        f"file -- cross-user isolation broken: {resp.text}"
                    )

                    # anonymous (no header, no matching IP-derived key either)
                    resp = await client.get(f"/v1/agents/files/{file_id}")
                    assert resp.status_code == 404, resp.text

                    # a random, never-issued id -- same 404, so the two cases
                    # above are indistinguishable from "this id doesn't exist"
                    # (anti-enumeration).
                    resp = await client.get(
                        f"/v1/agents/files/{uuid.uuid4()}", headers={"X-Viewer-Id": "bob"},
                    )
                    assert resp.status_code == 404
            finally:
                if os.path.exists(disk_path):
                    os.remove(disk_path)
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())

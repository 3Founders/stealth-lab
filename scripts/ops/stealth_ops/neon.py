"""Thin Neon API client + snapshot (branch) helper. stdlib only (urllib); transport is injectable for tests.

Auth: NEON_API_KEY (an account/org API key). Optional: NEON_ORG_ID (required by Neon for organization
accounts), NEON_REGION (default aws-us-east-2), NEON_PG_VERSION (default 17).

Project identity: Stealth shard ids (K000 control, K001, ...) are OURS and stable. A Neon project is found by
NAME (``<prefix>-<shard>``, prefix default ``stealth``), so re-running provisioning finds the existing project
instead of creating another, even if the local state file is lost. Neon project ids are recorded only as
non-secret state.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

from .core import OpsError

API = "https://console.neon.tech/api/v2"


class NeonError(OpsError):
    pass


Transport = Callable[[str, str, Optional[dict]], Any]


class Neon:
    def __init__(self, api_key: Optional[str] = None, *, transport: Optional[Transport] = None, org_id: Optional[str] = None):
        self.key = api_key or os.environ.get("NEON_API_KEY", "")
        self.org_id = org_id or os.environ.get("NEON_ORG_ID") or None
        self.prefix = os.environ.get("NEON_PROJECT_PREFIX", "stealth")
        self._transport = transport or self._http
        if not self.key and transport is None:
            raise NeonError("NEON_API_KEY is not set (Neon console -> Account settings -> API keys). Export it in this shell; it is never stored.")

    # ---- transport
    def _http(self, method: str, path: str, body: Optional[dict]) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.key}", "Accept": "application/json", "Content-Type": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                detail = e.read().decode("utf-8", "replace")[:400]
                raise NeonError(f"Neon API {method} {path} -> HTTP {e.code}: {detail}") from None
            except urllib.error.URLError as e:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise NeonError(f"Neon API unreachable: {e.reason}") from None
        raise NeonError("unreachable")

    def call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        return self._transport(method, path, body)

    # ---- projects
    def project_name(self, shard_id: str) -> str:
        return f"{self.prefix}-{'control' if shard_id == 'K000' else shard_id.lower()}"

    def list_projects(self) -> list[dict]:
        out, cursor = [], None
        while True:
            q = {"limit": "100"}
            if cursor:
                q["cursor"] = cursor
            if self.org_id:
                q["org_id"] = self.org_id
            res = self.call("GET", "/projects?" + urllib.parse.urlencode(q))
            out += res.get("projects", [])
            cursor = (res.get("pagination") or {}).get("next")
            if not cursor or not res.get("projects"):
                return out

    def find_project(self, name: str) -> Optional[dict]:
        hits = [p for p in self.list_projects() if p.get("name") == name]
        if len(hits) > 1:
            raise NeonError(f"{len(hits)} Neon projects are named {name!r}; refusing to guess -- delete or rename the extras")
        return hits[0] if hits else None

    def create_project(self, name: str, *, region: Optional[str] = None, autosuspend_off: bool = True) -> dict:
        project: dict[str, Any] = {"name": name, "region_id": region or os.environ.get("NEON_REGION", "aws-us-east-2"),
                                   "pg_version": int(os.environ.get("NEON_PG_VERSION", "17"))}
        if self.org_id:
            project["org_id"] = self.org_id
        if autosuspend_off:
            project["default_endpoint_settings"] = {"suspend_timeout_seconds": int(os.environ.get("NEON_SUSPEND_TIMEOUT_SECONDS", "0"))}
        try:
            return self.call("POST", "/projects", {"project": project})["project"]
        except NeonError as exc:
            if autosuspend_off and "suspend" in str(exc).lower():   # plan without the setting: create anyway, warn upstream
                project.pop("default_endpoint_settings", None)
                return self.call("POST", "/projects", {"project": project})["project"]
            raise

    def ensure_project(self, shard_id: str) -> tuple[dict, bool]:
        """(project, created). Idempotent by project name."""
        name = self.project_name(shard_id)
        found = self.find_project(name)
        if found:
            return found, False
        return self.create_project(name), True

    # ---- connection info (DIRECT, non-pooled; the queue needs session advisory locks)
    def connection_uri(self, project_id: str, *, database: Optional[str] = None, role: Optional[str] = None) -> str:
        branches = self.call("GET", f"/projects/{project_id}/branches").get("branches", [])
        primary = next((b for b in branches if b.get("primary") or b.get("default")), branches[0] if branches else None)
        if not primary:
            raise NeonError(f"project {project_id} has no branch")
        q: dict[str, str] = {"branch_id": primary["id"], "pooled": "false"}
        if not database or not role:
            dbs = self.call("GET", f"/projects/{project_id}/branches/{primary['id']}/databases").get("databases", [])
            if not dbs:
                raise NeonError(f"project {project_id} has no database")
            database, role = database or dbs[0]["name"], role or dbs[0]["owner_name"]
        q.update({"database_name": database, "role_name": role})
        uri = self.call("GET", f"/projects/{project_id}/connection_uri?" + urllib.parse.urlencode(q))["uri"]
        if "-pooler" in uri:
            raise NeonError("Neon returned a pooled URL; a DIRECT URL is required (session advisory locks)")
        return uri if "sslmode=" in uri else uri + ("&" if "?" in uri else "?") + "sslmode=require"

    # ---- branches (snapshots)
    def primary_branch(self, project_id: str) -> dict:
        branches = self.call("GET", f"/projects/{project_id}/branches").get("branches", [])
        for b in branches:
            if b.get("primary") or b.get("default"):
                return b
        raise NeonError(f"project {project_id} has no primary branch")

    def snapshot(self, project_id: str, name: str) -> tuple[dict, bool]:
        """Create a copy-on-write branch named ``name`` from the primary branch head, with NO compute endpoint.
        Idempotent: an existing branch of that name is returned untouched. Never restores, resets or deletes."""
        branches = self.call("GET", f"/projects/{project_id}/branches").get("branches", [])
        for b in branches:
            if b.get("name") == name:
                return b, False
        parent = self.primary_branch(project_id)
        res = self.call("POST", f"/projects/{project_id}/branches", {"branch": {"name": name, "parent_id": parent["id"]}})
        return res["branch"], True

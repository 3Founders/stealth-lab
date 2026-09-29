"""GitHub reads for provenance: which commit holds this exact file, and what license the repository had there.

Identity is Git's own: a file's blob id is sha1("blob <n>\\0" + bytes). A dataset row's text is matched to a
commit by comparing that id with the blob GitHub reports for the path at a commit, first at the default branch's
head, then back through the commits that touched the path. A match is exact by construction; there is no
"close enough".

The license is GitHub's own detection (`GET /repos/{o}/{r}/license?ref=<commit>`) at that same commit.

Errors are split the way the ledger needs them:
  RepoGone           -- the repository or the path's history does not exist any more    -> rejected
  GitHubUnavailable  -- network, 5xx, or rate limits that outlast the wait allowance    -> failed (retried)
Rate limits are waited out (primary: until the reset time; secondary: Retry-After), never skipped.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

GRAPHQL = "https://api.github.com/graphql"
REST = "https://api.github.com"
PATHS_PER_QUERY = 40              # aliases per GraphQL query (cost stays 1 point)
HISTORY_DEPTH = 100               # commits touching a path searched for an exact blob
MAX_RATE_WAIT_S = 3600.0          # longest single wait for a primary rate-limit reset
TRANSIENT_RETRIES = 4


class GitHubUnavailable(RuntimeError):
    pass


class RepoGone(RuntimeError):
    pass


def git_blob_id(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@dataclass
class GitHubStats:
    graphql_calls: int = 0
    rest_calls: int = 0
    rate_waits: int = 0
    rate_wait_s: float = 0.0
    transient_retries: int = 0

    def as_dict(self) -> dict:
        return {"graphql_calls": self.graphql_calls, "rest_calls": self.rest_calls, "rate_waits": self.rate_waits,
                "rate_wait_s": round(self.rate_wait_s, 1), "transient_retries": self.transient_retries}


@dataclass
class GitHub:
    token: str
    client: Optional[httpx.AsyncClient] = None
    stats: GitHubStats = field(default_factory=GitHubStats)

    async def __aenter__(self) -> "GitHub":
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "kel-ingest"})
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.client is not None:
            await self.client.aclose()

    # -- transport ---------------------------------------------------------------------------------------------

    async def _wait(self, seconds: float, why: str) -> None:
        seconds = max(1.0, seconds)
        if seconds > MAX_RATE_WAIT_S:
            raise GitHubUnavailable(f"{why}: reset is {seconds:.0f}s away (over the {MAX_RATE_WAIT_S:.0f}s allowance)")
        log.warning("github: %s; waiting %.0fs", why, seconds)
        self.stats.rate_waits += 1
        self.stats.rate_wait_s += seconds
        await asyncio.sleep(seconds)

    async def _send(self, method: str, url: str, **kw: Any) -> httpx.Response:
        assert self.client is not None, "use `async with GitHub(token) as gh`"
        for attempt in range(TRANSIENT_RETRIES + 1):
            try:
                resp = await self.client.request(method, url, **kw)
            except httpx.HTTPError as exc:
                if attempt == TRANSIENT_RETRIES:
                    raise GitHubUnavailable(f"{method} {url}: {exc!r}") from exc
                self.stats.transient_retries += 1
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code in (403, 429):
                retry_after = resp.headers.get("retry-after")
                remaining = resp.headers.get("x-ratelimit-remaining")
                if retry_after:
                    await self._wait(float(retry_after), "secondary rate limit")
                    continue
                if remaining == "0":
                    reset = float(resp.headers.get("x-ratelimit-reset", time.time() + 60))
                    await self._wait(reset - time.time() + 2, "primary rate limit")
                    continue
            if resp.status_code >= 500:
                if attempt == TRANSIENT_RETRIES:
                    raise GitHubUnavailable(f"{method} {url}: HTTP {resp.status_code}")
                self.stats.transient_retries += 1
                await asyncio.sleep(2 ** attempt)
                continue
            return resp
        raise GitHubUnavailable(f"{method} {url}: retries exhausted")

    async def graphql(self, query: str, variables: dict) -> dict:
        self.stats.graphql_calls += 1
        resp = await self._send("POST", GRAPHQL, json={"query": query, "variables": variables})
        if resp.status_code != 200:
            raise GitHubUnavailable(f"graphql HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        rate = (body.get("data") or {}).get("rateLimit") or {}
        if rate.get("remaining") is not None and int(rate["remaining"]) < 20 and rate.get("resetAt"):
            reset = datetime.fromisoformat(rate["resetAt"].replace("Z", "+00:00"))
            await self._wait((reset - datetime.now(timezone.utc)).total_seconds() + 2, "graphql points nearly spent")
        errors = body.get("errors") or []
        if errors and not body.get("data"):
            raise GitHubUnavailable(f"graphql errors: {errors[:2]}")
        return body

    # -- provenance --------------------------------------------------------------------------------------------

    async def files_at(self, owner: str, name: str, ref: str, paths: list[str]) -> tuple[str, dict[str, Optional[str]]]:
        """(commit oid of `ref`, {path: blob oid or None}). `ref` is "HEAD" (the default branch) or a commit sha.

        Raises RepoGone when the repository is not there."""
        commit_oid: Optional[str] = None
        blobs: dict[str, Optional[str]] = {}
        for start in range(0, len(paths), PATHS_PER_QUERY):
            chunk = paths[start:start + PATHS_PER_QUERY]
            decls = ", ".join(f"$p{i}: String!" for i in range(len(chunk)))
            fields = " ".join(f"f{i}: object(expression: $p{i}) {{ ... on Blob {{ oid }} }}" for i in range(len(chunk)))
            query = (f"query($o: String!, $n: String!, $ref: String!, {decls}) {{ repository(owner: $o, name: $n) {{ "
                     f"c: object(expression: $ref) {{ ... on Commit {{ oid }} }} {fields} }} "
                     f"rateLimit {{ remaining resetAt }} }}")
            variables: dict[str, Any] = {"o": owner, "n": name, "ref": ref}
            for i, path in enumerate(chunk):
                variables[f"p{i}"] = f"{commit_oid or ref}:{path}"
            body = await self.graphql(query, variables)
            repo = (body.get("data") or {}).get("repository")
            if repo is None:
                raise RepoGone(f"{owner}/{name}: repository not found")
            if not (repo.get("c") or {}).get("oid"):
                raise RepoGone(f"{owner}/{name}: ref {ref} not found")
            if commit_oid is None:
                commit_oid = repo["c"]["oid"]
            elif repo["c"]["oid"] != commit_oid and ref == "HEAD":
                raise GitHubUnavailable(f"{owner}/{name}: default branch moved during the lookup; retry")
            for i, path in enumerate(chunk):
                blobs[path] = (repo.get(f"f{i}") or {}).get("oid")
        assert commit_oid is not None
        return commit_oid, blobs

    async def commit_with_blob(self, owner: str, name: str, path: str, blob_oid: str) -> Optional[str]:
        """The newest commit (of the last HISTORY_DEPTH touching `path` on the default branch) whose `path` is exactly
        `blob_oid`, or None."""
        query = ("query($o: String!, $n: String!, $p: String!, $k: Int!) { repository(owner: $o, name: $n) { "
                 "defaultBranchRef { target { ... on Commit { history(first: $k, path: $p) { nodes { oid "
                 "file(path: $p) { oid } } } } } } } rateLimit { remaining resetAt } }")
        body = await self.graphql(query, {"o": owner, "n": name, "p": path, "k": HISTORY_DEPTH})
        repo = (body.get("data") or {}).get("repository")
        if repo is None:
            raise RepoGone(f"{owner}/{name}: repository not found")
        target = ((repo.get("defaultBranchRef") or {}).get("target") or {})
        for node in ((target.get("history") or {}).get("nodes") or []):
            if ((node.get("file") or {}).get("oid")) == blob_oid:
                return node["oid"]
        return None

    async def license_at(self, owner: str, name: str, commit: str) -> Optional[str]:
        """GitHub's detected SPDX id of the repository license at `commit`; None when there is no license file.
        "NOASSERTION" is returned as is (a license file GitHub could not identify)."""
        self.stats.rest_calls += 1
        resp = await self._send("GET", f"{REST}/repos/{owner}/{name}/license", params={"ref": commit})
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitHubUnavailable(f"license {owner}/{name}@{commit[:10]}: HTTP {resp.status_code}")
        return ((resp.json().get("license") or {}).get("spdx_id")) or None

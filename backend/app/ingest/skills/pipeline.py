"""The SkillMD-138K pipeline.

Per item (one dataset row), in this order; the first gate that says no decides:

  1. ledger       -- already written or rejected: never redone; failed: retried
  2. integrity    -- sha256(content) must equal the row's content_hash                 -> rejected content_hash_mismatch
  3. identity     -- this exact text already written, from any source                  -> rejected duplicate_identity
  4. near-dup     -- a written skill with >= 0.9 estimated similarity                   -> rejected near_duplicate
  5. provenance   -- the commit that holds exactly this text: the default branch's head, else the newest of the
                     last 100 commits touching the path                                -> rejected source_gone |
                                                                                           content_not_at_any_commit
  6. license      -- GitHub's detected license at THAT commit, through the allowlist    -> rejected license_*
  7. compile      -- the core SKILL.md compiler (screening, admission, one extraction call, candidates only),
                     with the credit on the ingestion context                          -> written | rejected compile_*

Items are processed repository by repository, so one GraphQL call resolves the head and every file of a
repository, and one REST call reads its license.
"""
from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from app.ingest.common import neardup
from app.ingest.common.github import GitHub, GitHubUnavailable, RepoGone, git_blob_id
from app.ingest.common.hf import PinnedFile
from app.ingest.common.ledger import FAILED, REJECTED, WRITTEN, ItemRef, Ledger
from app.ingest.common.licenses import decide

log = logging.getLogger(__name__)

PIPELINE = "skills"
EXTRACTOR = f"ingest:{PIPELINE}"
EXTRACTOR_VERSION = "2026-09-29.1"
SOURCE_TYPE = "skill_md"
DATASET = PinnedFile("FayeZC/SkillMD-138K", "0d73048abf2fb6ee91f6f9f5ac598d5be8d6bdd7", "train.parquet")
DATASET_CREDIT = "SkillMD-138K by FayeZC (CC-BY-4.0, https://creativecommons.org/licenses/by/4.0/)"


@dataclass(frozen=True)
class Row:
    index: int
    content_hash: str
    repo: str
    path: str
    html_url: str
    stars: int
    source: str

    @property
    def item_key(self) -> str:
        return self.content_hash

    @property
    def dedup_key(self) -> str:
        return f"skill-text:sha256:{self.content_hash}"

    @property
    def owner_name(self) -> tuple[str, str]:
        owner, name = self.repo.split("/", 1)
        return owner, name

    def ref(self) -> Optional[str]:
        """The ref in html_url: a full commit sha, or a branch name."""
        parts = urlparse(self.html_url).path.split("/")
        return parts[4] if len(parts) > 5 and parts[3] == "blob" else None


@dataclass
class RepoResolution:
    head: Optional[str] = None
    blobs: dict[str, Optional[str]] = field(default_factory=dict)
    gone: Optional[str] = None
    licenses: dict[str, Optional[str]] = field(default_factory=dict)   # commit -> spdx (None = no license file)


def load_rows(path: Path) -> tuple[Any, list[Row]]:
    """The whole table (content stays in Arrow until an item needs it) and the light rows, ordered by repository
    then path. Blocking."""
    import pyarrow.parquet as pq

    table = pq.read_table(str(path), columns=["content_hash", "repo", "path", "stars", "source", "html_url",
                                              "content"])
    light = table.drop_columns(["content"]).to_pylist()
    rows = [Row(i, str(r["content_hash"]), str(r["repo"]), str(r["path"]), str(r["html_url"]),
                int(r["stars"] or 0), str(r["source"] or "")) for i, r in enumerate(light)]
    rows.sort(key=lambda r: (r.repo.lower(), r.path, r.content_hash))
    return table, rows


def attribution(row: Row, commit: str, spdx: str, title: Optional[str]) -> dict:
    uri = f"https://github.com/{row.repo}/blob/{commit}/{row.path}"
    return {
        "license": spdx, "creator": row.repo, "title": title, "source_uri": uri,
        "commit": commit, "path": row.path, "dataset": DATASET_CREDIT,
        "changes": "converted into structured procedures and claims",
        "notice": (f"{row.path} from {row.repo}@{commit[:12]} ({uri}), licensed under {spdx}; via {DATASET_CREDIT}; "
                   "modified: converted into structured procedures and claims."),
    }


def _ref(row: Row) -> ItemRef:
    return ItemRef(item_key=row.item_key, source=DATASET.source_id, revision=DATASET.revision,
                   row_ref=f"{row.repo}/{row.path}", dedup_key=row.dedup_key)


async def resolve_repo(gh: GitHub, repo: str, rows: list[Row]) -> RepoResolution:
    owner, name = repo.split("/", 1)
    res = RepoResolution()
    try:
        res.head, res.blobs = await gh.files_at(owner, name, "HEAD", sorted({r.path for r in rows}))
    except RepoGone as exc:
        res.gone = str(exc)
    return res


async def provenance(gh: GitHub, res: RepoResolution, row: Row, blob: str) -> tuple[Optional[str], str]:
    """(commit holding exactly this text, how it was found) or (None, rejection reason)."""
    if res.gone:
        return None, "source_gone"
    owner, name = row.owner_name
    if res.blobs.get(row.path) == blob:
        return res.head, "default_branch_head"
    ref = row.ref()
    if ref and len(ref) == 40 and all(c in "0123456789abcdef" for c in ref):
        try:
            commit, blobs = await gh.files_at(owner, name, ref, [row.path])
        except RepoGone:
            commit, blobs = None, {}
        if commit and blobs.get(row.path) == blob:
            return commit, "url_commit"
    try:
        found = await gh.commit_with_blob(owner, name, row.path, blob)
    except RepoGone:
        return None, "source_gone"
    return (found, "path_history") if found else (None, "content_not_at_any_commit")


async def license_at(gh: GitHub, res: RepoResolution, row: Row, commit: str) -> Optional[str]:
    if commit not in res.licenses:
        owner, name = row.owner_name
        res.licenses[commit] = await gh.license_at(owner, name, commit)
    return res.licenses[commit]


async def compile_item(pool: Any, row: Row, content: str, commit: str, spdx: str, how: str,
                       client: Any, run_id: str) -> tuple[str, str, dict, dict]:
    from app.config import settings
    from app.services.embeddings import Embedder
    from app.services.ingestion_sources.base import SourceArtifact
    from app.services.skill_ingestion import compile_skill_artifact

    title = next((ln.split(":", 1)[1].strip() for ln in content.splitlines()[:15]
                  if ln.lower().startswith("name:")), None)
    credit = attribution(row, commit, spdx, title)
    artifact = SourceArtifact(
        source_type=SOURCE_TYPE,
        uri=credit["source_uri"],
        content=content,
        content_hash=row.content_hash,
        repository=row.repo,
        path=row.path,
        commit=commit,
        source_id=f"{DATASET.source_id}@{DATASET.revision}",
        license_metadata={
            "spdx_id": spdx, "attribution": credit, "license_source": f"github license detection at {commit}",
            "provenance_match": how, "dataset": DATASET.repo, "dataset_revision": DATASET.revision,
            "dataset_row": row.index, "stars": row.stars, "collection_source": row.source,
        },
    )
    from app.ingest.common.llm import ingest_model

    model = ingest_model()
    outcome = await compile_skill_artifact(
        pool, artifact, embedder=Embedder(rate_limit_pool=pool), client=client, run_id=run_id,
        created_by=EXTRACTOR, admission_llm_model=model, extraction_llm_model=model,
        fallback_extraction_llm_model=None, claim_extraction_llm_model=model)
    objects = {k: getattr(outcome, k, None) for k in (
        "procedure_id", "version_row_id", "artifact_id", "source_id", "ingestion_context_id")}
    objects.update({k: list(getattr(outcome, k, None) or []) for k in (
        "script_procedure_ids", "reference_procedure_ids", "independent_step_procedure_ids", "task_node_ids")})
    detail = {"commit": commit, "provenance_match": how, "license": spdx, "status": outcome.status,
              "extraction_model": outcome.extraction_model, "admission": outcome.admission_decision,
              "quarantined": outcome.quarantined, "injection_screened": outcome.injection_screened,
              "compile_reason": outcome.reason, "stars": row.stars}
    return classify_outcome(outcome) + (detail, objects)


def classify_outcome(outcome: Any) -> tuple[str, str]:
    """The core compiler's outcome -> (ledger status, reason). The compiler never raises for a model failure; it
    returns `rejected` with a reason beginning "extraction failed:", which is infrastructure and must be retried,
    not recorded as a policy decision."""
    status, reason = outcome.status, (outcome.reason or "")
    if status in ("captured", "new_version"):
        return WRITTEN, "quarantined_for_review" if outcome.quarantined else "written"
    if status == "unchanged":
        # a completed earlier attempt already wrote this exact text (a run killed before its ledger write)
        return WRITTEN, "recovered_from_earlier_attempt"
    if reason.startswith("extraction failed:"):
        return FAILED, "extraction_failed"
    if outcome.injection_screened:
        return REJECTED, "screened_prompt_injection"
    if outcome.admission_decision == "reject":
        return REJECTED, "admission_rejected"
    if outcome.quarantined:
        return REJECTED, "quarantined_by_admission"
    if reason.startswith("extraction abstained") or reason == "extraction produced zero procedures":
        return REJECTED, "nothing_extractable"
    return REJECTED, "compile_rejected"


async def run(pool: Any, *, ledger: Ledger, limit: Optional[int]) -> dict:
    from app.config import settings
    from app.ingest.common.llm import extraction_client
    from app.services.ingest_budget import BudgetExceeded
    from app.utils.aio import run_blocking

    token = settings.github_token or settings.personal_github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN (or PERSONAL_GITHUB_TOKEN) is required: provenance and licenses are read "
                           "from GitHub at the exact commit")
    path = await run_blocking(DATASET.local_path)
    table, rows = await run_blocking(load_rows, path)
    client = extraction_client()
    stats: dict[str, int] = defaultdict(int)

    todo: list[Row] = []
    for row in rows:
        go, _why = await ledger.should_process(row.item_key)
        if go:
            todo.append(row)
        else:
            stats["skipped_already_decided"] += 1
        if limit is not None and len(todo) >= limit:
            break
    by_repo: dict[str, list[Row]] = defaultdict(list)
    for row in todo:
        by_repo[row.repo].append(row)

    stopped: Optional[str] = None
    async with GitHub(token) as gh:
        for repo in sorted(by_repo, key=str.lower):
            group = by_repo[repo]
            res: Optional[RepoResolution] = None
            for row in group:
                ref = _ref(row)
                content = str(table.column("content")[row.index].as_py() or "")
                if hashlib.sha256(content.encode("utf-8")).hexdigest() != row.content_hash:
                    await ledger.record(ref, REJECTED, "content_hash_mismatch")
                    stats["rejected:content_hash_mismatch"] += 1
                    continue
                owner = await ledger.identity_owner(row.dedup_key)
                if owner is not None:
                    await ledger.record(ref, REJECTED, "duplicate_identity", detail={"identity_owner": owner})
                    stats["rejected:duplicate_identity"] += 1
                    continue
                sig = await run_blocking(neardup.signature, content)
                near = await neardup.nearest_written(pool, PIPELINE, sig)
                if near is not None:
                    await ledger.record(ref, REJECTED, "near_duplicate",
                                        detail={"of": near[0], "similarity": round(near[1], 3)})
                    stats["rejected:near_duplicate"] += 1
                    continue
                try:
                    if res is None:
                        res = await resolve_repo(gh, repo, group)
                    blob = git_blob_id(content)
                    commit, how = await provenance(gh, res, row, blob)
                    if commit is None:
                        await ledger.record(ref, REJECTED, how, detail={"repo": repo, "path": row.path,
                                                                         "blob": blob, "gone": res.gone})
                        stats[f"rejected:{how}"] += 1
                        continue
                    spdx = await license_at(gh, res, row, commit)
                except GitHubUnavailable as exc:
                    await ledger.record(ref, FAILED, "github_unavailable", detail={"error": str(exc)[:1000]})
                    stats["failed:github_unavailable"] += 1
                    continue
                verdict = decide(spdx, records_attribution=True)
                if not verdict.allowed:
                    reason = "license_missing" if spdx is None else f"license_{verdict.decision.lower()}"
                    await ledger.record(ref, REJECTED, reason, detail={"commit": commit, "license": spdx,
                                                                        "why": verdict.reason})
                    stats[f"rejected:{reason}"] += 1
                    continue
                try:
                    status, why, detail, objects = await compile_item(
                        pool, row, content, commit, verdict.used_under or spdx, how, client, ledger.run_id)
                except BudgetExceeded as exc:
                    stopped = f"budget: {exc}"
                    break
                except Exception as exc:  # noqa: BLE001 -- infrastructure: retried by the next run
                    log.exception("skills item %s failed", row.item_key)
                    await ledger.record(ref, FAILED, type(exc).__name__, detail={"commit": commit,
                                                                                  "error": str(exc)[:2000]})
                    stats[f"failed:{type(exc).__name__}"] += 1
                    continue
                stored = await ledger.record(ref, status, why, detail={**detail, "minhash": sig}, objects=objects)
                if stored == WRITTEN:
                    await neardup.remember(pool, PIPELINE, row.item_key, sig)
                stats[f"{stored}:{why if stored == status else 'duplicate_identity'}"] += 1
            if stopped:
                break
    return {"dataset_rows": len(rows), "attempted": len(todo), "stopped": stopped, "github": gh.stats.as_dict(),
            "stats": dict(stats)}

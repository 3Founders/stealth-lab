"""The whole code cascade for one repository: fetch -> license -> S1-S3 structural -> S4 model judge -> S5 store.

    python -m app.ingestion.admin cascade-repo owner/name [--ref R] [--top 20] [--no-judge] [--apply] [--max-usd 1]

Without `--apply` nothing is written: the run returns the candidate exemplars (capability, path, lines, why it is non-trivial) so
a human can read what would be stored. With it, each accepted span becomes a ONE-STEP PROCEDURE whose Goal is the generalizable
capability sentence and whose step points at the exact lines of the exact commit (a `span` source_locator), with the span text
preserved as a `reference_code` artifact (migration 128) so a small model that cannot open a URL can still be shown it.

LICENSE COMES FIRST AND FAILS CLOSED
    The repository license is read pinned to the commit; the NEAREST in-repo LICENSE governs each file (a vendored subfolder with
    its own license does not inherit the root's); an unidentified license, or a copyleft one, keeps the file out of scoring
    entirely -- it is not scored and then dropped. A file whose own header contradicts a permissive repository license (a GPL
    notice inside an MIT repo) is dropped after ranking. One IngestionContext per run records the license, so the whole run can
    be removed as a class with `admin license-takedown`.

WHAT IT DOES NOT CLAIM
    "Non-trivial" is decided by structure and confirmed by a model reading the span; neither proves the code is CORRECT. An
    exemplar is a technique worth studying from a well-regarded project, shown with its source and license.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from typing import Any, Iterable, Optional

from app.services.code_cascade import fetch, judge
from app.services.code_cascade.cascade import KeptSpan, run_structural_cascade
from app.utils.aio import run_blocking

log = logging.getLogger(__name__)

PIPELINE_VERSION = "code_cascade@1"
DEFAULT_TOP = 20
DEFAULT_OVERSAMPLE = 2.0
_HEADER_CHARS = 3_000
# Only unambiguous markers. "All rights reserved" is deliberately NOT one: it routinely precedes the permission text of an MIT or
# BSD header ("Copyright (c) 2020 X. All rights reserved. Licensed under the MIT License"), so it would flag ordinary files.
_COPYLEFT_HEADER = re.compile(
    r"GNU (Affero |Lesser |Library )?General Public License|\bA?GPL-?[123]\b|\bSSPL\b|Server Side Public License|"
    r"Confidential and Proprietary", re.IGNORECASE)
_PERMISSIVE = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Unlicense", "CC0-1.0", "Zlib"})


def license_header_conflict(text: str, governing_spdx: Optional[str]) -> Optional[str]:
    """A file that carries its OWN copyleft / proprietary notice inside a repository licensed permissively is not covered by
    the repository license: the header is the closer statement. Returns the reason, or None."""
    if governing_spdx not in _PERMISSIVE:
        return None
    match = _COPYLEFT_HEADER.search(text[:_HEADER_CHARS])
    return f"file header carries its own notice: {match.group(0)[:60].strip()!r}" if match else None


def partition_files_by_license(snapshot: fetch.RepoSnapshot, *, allow: Optional[Iterable[str]] = None
                               ) -> tuple[dict[str, str], Counter, dict[str, str]]:
    """(allowed files, blocked-by-decision counts, path -> governing SPDX for the allowed ones)."""
    from app.services.repo_license_policy import decide_repo_license

    allowed: dict[str, str] = {}
    governing: dict[str, str] = {}
    blocked: Counter = Counter()
    for path, text in snapshot.files.items():
        if path == "go.mod":
            continue
        verdict = decide_repo_license(path=path, tree=snapshot.tree, repo_spdx=snapshot.license_spdx,
                                      license_spdx_by_path=snapshot.license_index, allow=allow)
        if verdict.decision == "ALLOW":
            allowed[path] = text
            governing[path] = verdict.spdx_id or ""
        else:
            blocked[f"{verdict.decision.lower()}:{verdict.spdx_id or 'unidentified'}"] += 1
    return allowed, blocked, governing


def _exemplar(snapshot: fetch.RepoSnapshot, span: KeptSpan, verdict: judge.Judgement, spdx: str, judged_by: str,
              file_ctx: dict[str, Any]):
    from app.services.code_exemplars import Exemplar

    f = span.ranked.features
    return Exemplar(
        capability=verdict.capability, code=span.text, language=span.language, repository=snapshot.repository,
        path=span.path, commit=snapshot.commit, line_start=span.line_start, line_end=span.line_end, license_spdx=spdx,
        why_nontrivial=verdict.why_nontrivial, prerequisites=verdict.prerequisites, pitfalls=verdict.pitfalls,
        difficulty=verdict.difficulty, tags=verdict.tags,
        extra={"pipeline": PIPELINE_VERSION, "judge": judged_by, "score": round(span.ranked.score, 3),
               "kind": f.kind, "name": f.name, "code_lines": f.code_lines, "cyclomatic": f.cyclomatic,
               "max_nesting": f.max_nesting, "distinct_calls": f.distinct_calls,
               "file_indegree": span.file_indegree, "role": span.ranked.role, **file_ctx})


async def store_exemplar(pool: Any, ex: Any, *, context_id: Optional[str], embedder: Any, goal_cache: Any,
                         created_by: str) -> dict:
    """One accepted span -> a one-step Procedure (Goal = the capability) + its preserved `reference_code` artifact.
    Returns {"stored": {...}} or {"skipped": reason}."""
    from app.services.code_exemplars import preserve_span
    from app.services.goals import GoalQualityRejected
    from app.services.identity_resolution import identity_idempotency_key
    from app.services.procedure_display import (
        DISPLAY_METADATA_FALLBACK_VERSION, DISPLAY_METADATA_VERSION, build_display_metadata)
    from app.services.procedures import capture_procedure
    from app.services.retrieval_document import (
        RETRIEVAL_DOCUMENT_VERSION, build_procedure_retrieval_document, retrieval_document_sha256)
    from app.services.skill_ingestion import _content_name
    from app.services.v0_gate import V0Violation
    import hashlib

    sha = hashlib.sha256(ex.code.encode("utf-8")).hexdigest()
    locator = {"source_id": ex.repository, "uri": ex.blob_url, "path": ex.path, "commit": ex.commit, "content_hash": sha,
               "line_start": ex.line_start, "line_end": ex.line_end, "granularity": "span"}
    description = (ex.why_nontrivial or "Study this reference implementation and adapt the technique to the task.")
    step = {"order": 0, "description": description if len(description) >= 10 else description.ljust(10, "."),
            "goal": ex.capability, "source_locator": locator}
    name = _content_name(ex.capability, ex.path)
    # The procedure is embedded HERE, from its canonical retrieval document, exactly as the SKILL.md path does. Without a
    # vector an exemplar is stored but never found (a first live run stored six with no embedding): retrievability is the point.
    retrieval_doc = build_procedure_retrieval_document({
        "name": name, "goal": ex.capability, "steps": [step], "preconditions": list(ex.prerequisites), "invariants": [],
        "postconditions": [], "failure_conditions": list(ex.pitfalls), "domain": None, "domain_payload": {}})
    vector, embedding_meta = await embedder.embed_one_with_metadata(retrieval_doc, input_type="document")
    disp_name, disp_desc, disp_quality = build_display_metadata(
        {"name": name, "goal": ex.capability, "capability_statement": ex.capability})
    try:
        result = await capture_procedure(
            pool, name=name, goal=ex.capability, steps=[step], provenance="prior_library",
            scope_type="global", created_by=created_by, display_description=step["description"], goal_cache=goal_cache,
            domain_payload={"source": "code_exemplar", "language": ex.language, "tags": list(ex.tags),
                            "embedding": embedding_meta.__dict__},
            embedding=vector, embedding_model_id=embedding_meta.model_id, embedding_provider=embedding_meta.provider,
            embedding_input_type=embedding_meta.input_type, embedding_text_hash=embedding_meta.text_sha256,
            retrieval_document=retrieval_doc, retrieval_document_version=RETRIEVAL_DOCUMENT_VERSION,
            retrieval_document_sha256=retrieval_document_sha256(retrieval_doc),
            display_name=disp_name, display_metadata_version=(
                DISPLAY_METADATA_VERSION if disp_quality is None else DISPLAY_METADATA_FALLBACK_VERSION),
            goal_embedder=embedder, judge_mode="model", procedure_dedup=True,
            source_key=f"code-exemplar:{ex.repository}:{ex.path}:{ex.line_start}-{ex.line_end}:{sha[:16]}",
            identity_job_id=None,
            identity_idempotency_key=identity_idempotency_key(
                job_id=None, source_hash=sha, object_type="goal", semantic_role=f"code_exemplar_goal:{ex.path}",
                scope_type="global", scope_entity_id=None, text=ex.capability),
            source_locator=locator, require_source_locators=True, ingestion_context_id=context_id)
    except (V0Violation, GoalQualityRejected) as exc:
        return {"skipped": f"goal_rejected: {str(exc)[:120]}"}
    ref = await preserve_span(pool, procedure_row_id=str(result["id"]), ex=ex, ingestion_context_id=context_id)
    if ref is None:
        return {"skipped": "no_bytes_kept"}
    return {"stored": {"procedure_id": str(result["procedure_id"]), "path": ex.path,
                       "lines": f"{ex.line_start}-{ex.line_end}", "capability": ex.capability,
                       "artifact_id": ref["artifact_id"]}}


async def run_repo_cascade(
    pool: Any, repository: str, *, ref: Optional[str] = None, top: int = DEFAULT_TOP, oversample: float = DEFAULT_OVERSAMPLE,
    client: Any = None, model: Optional[str] = None, embedder: Any = None, apply: bool = False,
    created_by: str = "code_cascade", snapshot: Optional[fetch.RepoSnapshot] = None, http_json: Any = None,
    download: Any = None, judge_batch: int = judge.DEFAULT_BATCH, license_allow: Optional[Iterable[str]] = None,
    languages: Optional[set[str]] = None,
) -> dict:
    started = time.perf_counter()
    if apply and (client is None or not model):
        raise ValueError("apply needs a model client: nothing is stored on structure alone (S4 confirms and describes)")
    snapshot = snapshot or await run_blocking(lambda: fetch.fetch_snapshot(repository, ref, http_json=http_json, download=download))
    report: dict[str, Any] = {
        "repository": repository, "commit": snapshot.commit, "license": snapshot.license_spdx, "stars": snapshot.stars,
        "archive_skipped": dict(snapshot.skipped), "applied": bool(apply), "pipeline": PIPELINE_VERSION}

    allowed, blocked, governing = partition_files_by_license(snapshot, allow=license_allow)
    report["license_blocked"] = dict(blocked)
    if not allowed:
        report.update(status="rejected", reason="no file of this repository has a permitted license",
                      seconds=round(time.perf_counter() - started, 2))
        return report

    go_mod = snapshot.files.get("go.mod")
    structural = await run_blocking(lambda: run_structural_cascade(
        allowed, max_spans=max(top, int(top * oversample)), languages=languages, go_mod_text=go_mod))
    report["funnel"] = structural.funnel.as_dict()

    candidates: list[KeptSpan] = []
    header_conflicts = 0
    for span in structural.kept:
        conflict = license_header_conflict(allowed[span.path], governing.get(span.path))
        if conflict:
            header_conflicts += 1
            continue
        candidates.append(span)
    report["funnel"]["spans_license_header_conflict"] = header_conflicts

    judgements: list[judge.Judgement]
    if client is not None and model:
        judgements = await judge.judge_spans(client, model, repository, snapshot.commit, candidates, batch_size=judge_batch)
    else:
        judgements = [judge.Judgement(True, capability="(not judged: --no-judge)") for _ in candidates]
    rejected = Counter(j.reject_reason.split(":")[0] for j in judgements if not j.keep)
    accepted = [(s, j) for s, j in zip(candidates, judgements) if j.keep][:top]
    report["judge"] = {"model": model if client is not None else None, "sent": len(candidates), "kept": len(accepted),
                       "rejected": dict(rejected), "prompt": judge.PROMPT_VERSION}

    exemplars = [(_exemplar(snapshot, s, j, governing.get(s.path) or (snapshot.license_spdx or ""),
                            f"{model}:{judge.PROMPT_VERSION}", {"stars": snapshot.stars})) for s, j in accepted]
    report["candidates"] = [{"capability": e.capability, "path": e.path, "lines": f"{e.line_start}-{e.line_end}",
                             "kind": e.extra.get("kind"), "name": e.extra.get("name"), "score": e.extra.get("score"),
                             "difficulty": e.difficulty, "why": e.why_nontrivial, "license": e.license_spdx}
                            for e in exemplars]
    if not apply:
        report.update(status="dry_run", seconds=round(time.perf_counter() - started, 2))
        return report

    from app.services.embeddings import Embedder
    from app.services.goals import GoalResolutionCache
    from app.services.ingestion_context import complete_ingestion_context, open_ingestion_context
    from app.services.sources import register_source

    embedder = embedder or Embedder(rate_limit_pool=pool)
    goal_cache = GoalResolutionCache(max_concurrency=2)
    spdx = snapshot.license_spdx or ""
    # The repository at its pinned commit is the Source every derived row traces back to (the context's source_ref is that
    # Source's id, exactly as the SKILL.md path does it).
    source = await register_source(
        pool, source_type="repository", locator=f"https://github.com/{repository}/tree/{snapshot.commit}",
        publisher=repository, title=repository, license=spdx or None, discovered_via="code_cascade",
        provenance="prior_library", created_by=created_by)
    context_id = await open_ingestion_context(
        pool, source_type="code_exemplar", extractor_id="code_cascade", extractor_version=PIPELINE_VERSION,
        actor_id=created_by, scope_type="global", source_ref=source["id"],
        source_uri=f"https://github.com/{repository}/tree/{snapshot.commit}",
        **({"license_spdx": spdx, "attribution": {"license": spdx, "repository": repository,
                                                  "notice": f"Reference code from {repository}, licensed {spdx}."}} if spdx else {}))
    stored: list[dict] = []
    skipped: Counter = Counter()
    try:
        for ex in exemplars:
            outcome = await store_exemplar(pool, ex, context_id=context_id, embedder=embedder, goal_cache=goal_cache,
                                           created_by=created_by)
            if "stored" in outcome:
                stored.append(outcome["stored"])
            else:
                skipped[outcome["skipped"].split(":")[0]] += 1
    except BaseException:
        await complete_ingestion_context(pool, context_id, status="failed")
        raise
    await complete_ingestion_context(pool, context_id, status="completed")
    report.update(status="stored", ingestion_context_id=context_id, stored=stored, skipped=dict(skipped),
                  seconds=round(time.perf_counter() - started, 2))
    return report

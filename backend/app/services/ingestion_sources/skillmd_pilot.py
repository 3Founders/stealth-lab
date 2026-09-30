"""Step 3 pilot: 2,000 gated skills from `FayeZC/SkillMD-138K` into a local shard.

This is the measurement step. Everything upstream of it is a per-row decision;
this module exists to turn those decisions into the numbers a full-scale run
can be costed from, and to be honest when a number is not yet knowable.

WHAT IS MEASURED AND WHAT IS PROJECTED
    Measured here: rows seen, rows fetched, rows dead upstream, every
    rejection reason, exact and near-duplicate counts, the mirror split, the
    license outcome distribution, wall time, and bytes per admitted item.
    Projected: everything about the full 138,133-row corpus. The projection
    assumes the admitted fraction measured on this sample holds across the
    corpus, which is an assumption and is labelled as one in the summary. It
    is *not* a measurement, and a sample that happens to be mirror-heavy or
    dead-heavy will over- or under-state it.

WHY THE SAMPLE IS TAKEN FROM THE FRONT OF THE ROW ORDER
    The parquet's row order is whatever the crawler emitted -- registry rows
    first (90.0% measured). A prefix is therefore NOT a uniform random sample
    of the corpus, and the summary says so. It is the sample that is actually
    reachable in a bounded run, and the honest framing is "the first N rows as
    the crawler ordered them", not "N random rows".

WHY NOTHING IS WRITTEN TO PRODUCTION
    `run_skill_ingestion` is given this process's control pool. The caller is
    responsible for pointing that at a local shard's control database; this
    module asserts the resolved DSN is not one of the experiment databases the
    plan forbids (`kel_swebench*`, `kel_*` on 127.0.0.1:55432) and refuses to
    run otherwise. A pilot that quietly wrote 2,000 procedures into a shared
    instance would contaminate every later measurement in the project.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from app.services.ingestion_sources.skillmd_dataset import (
    DATASET_REPO,
    DATASET_REVISION,
    GATE_VERSION,
    GitHubLicenseResolver,
    SkillMD138KReader,
    SkillMD138KSource,
)
from app.utils.aio import run_blocking

log = logging.getLogger(__name__)

# The plan's isolation rule, enforced rather than trusted.
FORBIDDEN_DSN_MARKERS: tuple[str, ...] = ("kel_swebench", ":55432")


def build_extraction_client() -> tuple[Any, str]:
    """A SYNC OpenAI-compatible client + model id for document extraction.

    WHY SYNC, NOT ASYNC
        `skill_extraction.ungrounded.extract_document` and its grounded twin
        both call `asyncio.to_thread(client.chat.completions.create, ...)` --
        they wrap a sync client themselves to keep the call off the event loop
        (the same rule as `app.utils.aio.run_blocking`). Handing them an
        AsyncOpenAI would put an awaitable where a sync call is expected, so
        this builds a plain `OpenAI`.

    WHY THE PROVIDER PRECEDENCE IS COPIED, NOT INVENTED
        The order -- local, General Compute, OpenRouter, Anthropic -- is the
        same one `debate.panel.default_chat_agent` uses, for the same stated
        reason ("no need for a fourth distinct provider just for this"). A
        second, different precedence here would mean a pilot billed to a
        different account than the rest of the system, which is exactly the
        kind of surprise a spend-capped step cannot afford.

    Returns (client, model). (None, model) when nothing is configured, which
        makes every artifact a clean, labelled `rejected` outcome with the
        reason "no LLM client configured" rather than a silent zero.
    """
    from openai import OpenAI

    from app.config import settings

    if settings.use_local_models:
        return (
            OpenAI(api_key="local", base_url=settings.local_base_url),
            settings.general_compute_fallback_model or "gemma-4-31B-it",
        )
    if settings.use_general_compute and settings.general_compute_api_key:
        return (
            OpenAI(
                api_key=settings.general_compute_api_key,
                base_url=settings.general_compute_base_url,
            ),
            settings.general_compute_fallback_model or "gemma-4-31B-it",
        )
    if settings.use_openrouter and settings.openrouter_api_key:
        return (
            OpenAI(
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
            ),
            settings.openrouter_judge_model,
        )
    if settings.anthropic_api_key:
        return None, "claude-haiku-4-5"
    return None, "gemma-4-31B-it"


def assert_not_experiment_database(dsn: str) -> None:
    lowered = (dsn or "").lower()
    for marker in FORBIDDEN_DSN_MARKERS:
        if marker in lowered:
            raise RuntimeError(
                f"refusing to ingest into an experiment database: dsn matches {marker!r}. "
                "The plan forbids writing to kel_swebench* / kel_* on 127.0.0.1:55432."
            )


def _content_bytes(pairs: list[tuple[str, str]]) -> int:
    return sum(len(text.encode("utf-8")) for _, text in pairs)


async def run_skillmd_pilot(
    pool: Any,
    *,
    limit: int = 2000,
    shard_dsn: Optional[str] = None,
    enforce_license: bool = True,
    star_prior: Optional[int] = None,
    reader: Any = None,
    license_resolver: Any = None,
    raw_fetcher: Any = None,
    fetch_workers: int = 6,
    embed: bool = False,
    created_by: str = "skillmd_138k_pilot",
    dry_run: bool = False,
    max_usd: Optional[float] = None,
) -> dict[str, Any]:
    """Gate `limit` skills, report the disposition table, and (unless
    `dry_run`) compile the survivors through the existing skill path.

    `dry_run` is the default-shaped mode: it runs every gate and the license
    lookups, and stops before `run_skill_ingestion`. That is what makes the
    first run safe to do at full width -- the license and dedup numbers do not
    depend on any write.

    `raw_fetcher` is the same seam `SkillMD138KSource` already takes, exposed
    here so this orchestration is testable without a network. Without it the
    only way to exercise `run_skillmd_pilot` was to let it fetch
    raw.githubusercontent.com for real, which is why the async wrapper around
    the blocking discover pass had no proving test.
    """
    if shard_dsn:
        assert_not_experiment_database(shard_dsn)

    if license_resolver is None and enforce_license:
        license_resolver = GitHubLicenseResolver()     # kept in a name so its stats reach the summary below
    source = SkillMD138KSource(
        reader=reader or SkillMD138KReader(),
        limit=limit,
        license_resolver=license_resolver,
        enforce_license=enforce_license,
        star_prior=star_prior,
        raw_fetcher=raw_fetcher,
        fetch_workers=fetch_workers,
    )

    started = time.monotonic()
    refs = await run_blocking(lambda: list(source.discover()))   # network fetches, thread pool, sleeps: off-loop
    gate_seconds = time.monotonic() - started

    admitted_pairs = await run_blocking(lambda: [
        (ref.uri, source.fetch(ref).content) for ref in refs
    ])
    gate_stats = source.stats.as_dict()
    if isinstance(license_resolver, GitHubLicenseResolver) or hasattr(
        license_resolver, "stats"
    ):
        gate_stats["license_api"] = license_resolver.stats()  # type: ignore[union-attr]

    summary: dict[str, Any] = {
        "gate": gate_stats,
        "gate_seconds": round(gate_seconds, 2),
        "content_bytes": _content_bytes(admitted_pairs),
        "bytes_per_admitted": (
            round(_content_bytes(admitted_pairs) / len(admitted_pairs), 1)
            if admitted_pairs else 0
        ),
        "dry_run": dry_run,
        "dataset": {"repo": DATASET_REPO, "revision": DATASET_REVISION, "gate": GATE_VERSION},
    }

    lic = gate_stats.get("license_api") or {}
    if int(lic.get("rate_limited", 0) or 0):
        # Rate-limited lookups are quarantined as "license unknown", which reads as a low admission rate. It is not one:
        # it is an unfinished gate (an unauthenticated run gets 60 lookups/hour). Say so, and do not write on it.
        # `api_errors` is NOT a trigger: a deleted or moved repo answers 404, which is an ordinary per-row outcome.
        summary["warning"] = (
            f"license lookups were rate-limited (rate_limited={lic.get('rate_limited')}); the admitted count is a floor, "
            "not a measurement. Check that a GitHub token is configured."
        )
        if not dry_run:
            summary["ingestion"] = None
            summary["note"] = "refused to compile: " + summary["warning"]
            return summary

    if dry_run:
        summary["ingestion"] = None
        summary["note"] = "dry-run: every gate executed, nothing written"
        return summary

    from app.services.skill_ingestion import run_skill_ingestion

    if not refs:
        summary["ingestion"] = {"metrics": {"artifacts_seen": 0}}
        summary["note"] = "no rows survived the gates; nothing to compile"
        return summary

    ingest_started = time.monotonic()
    client, model = build_extraction_client()
    summary["llm"] = {"client_configured": client is not None, "model": model}
    # The same spend cap the ingestion workers enforce (rolling 24h on the llm_spend ledger; a paid call that
    # starts over the cap raises BudgetExceeded). Before this the pilot ran uncapped (docs/ingestion_review.md).
    # max_usd None = the configured DAILY_LLM_BUDGET_USD; an already-installed budget (inside a worker) is kept.
    from app.services import ingest_budget

    budget = ingest_budget.install(pool, cap_usd=max_usd) if ingest_budget.active() is None else None
    try:
        result = await run_skill_ingestion(
            pool, source, created_by=created_by,
            client=client, extraction_llm_model=model,
        )
    finally:
        if budget is not None:
            try:
                summary["budget"] = (await budget.status(fresh=True)).as_dict()
            finally:
                ingest_budget.uninstall()
    ingest_seconds = time.monotonic() - ingest_started

    metrics = result.get("metrics", {})
    outcomes = result.get("outcomes") or []
    outcome_reasons: dict[str, int] = {}
    outcome_statuses: dict[str, int] = {}
    for outcome in outcomes:
        status = str(getattr(outcome, "status", "unknown"))
        outcome_statuses[status] = outcome_statuses.get(status, 0) + 1
        reason = getattr(outcome, "reason", None) or f"status_{status}"
        outcome_reasons[reason] = outcome_reasons.get(reason, 0) + 1
    accepted = int(metrics.get("accepted", 0) or 0)
    summary["ingestion"] = {
        "run_id": result.get("run_id"),
        "metrics": metrics,
        "outcome_statuses": dict(sorted(outcome_statuses.items(), key=lambda kv: (-kv[1], kv[0]))),
        "outcome_reasons": dict(sorted(outcome_reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "seconds": round(ingest_seconds, 2),
        "seconds_per_item": (
            round(ingest_seconds / accepted, 3) if accepted else None
        ),
    }
    summary["cost_model"] = {
        "judge_calls": int(metrics.get("admission_escalated", 0) or 0)
        + int(metrics.get("document_claims", 0) or 0),
        "zero_claim_documents": int(metrics.get("zero_claim_documents", 0) or 0),
        "screened": int(metrics.get("screened", 0) or 0),
        "screening_quarantine": int(metrics.get("screening_quarantine", 0) or 0),
        "screening_reject": int(metrics.get("screening_reject", 0) or 0),
    }
    summary["wall_seconds_total"] = round(gate_seconds + ingest_seconds, 2)
    return summary


def project_to_full_corpus(summary: dict[str, Any], total_rows: int = 138_133) -> dict[str, Any]:
    """Scale the measured sample to the whole corpus, labelled as a projection.

    Every number here is `measured_sample * (total_rows / rows_seen)`. That is
    only a projection if the sample is representative, and §"WHY THE SAMPLE IS
    TAKEN FROM THE FRONT OF THE ROW ORDER" in this module's docstring is the
    reason it is not guaranteed to be. The caller is expected to say so when
    quoting it.
    """
    gate = summary.get("gate", {})
    rows_seen = int(gate.get("rows_seen", 0) or 0)
    admitted = int(gate.get("admitted", 0) or 0)
    if rows_seen == 0:
        return {"rows_seen": 0, "note": "no rows sampled; nothing to project"}
    scale = total_rows / rows_seen
    return {
        "basis_rows_seen": rows_seen,
        "basis_admitted": admitted,
        "assumed_total_rows": total_rows,
        "scale_factor": round(scale, 1),
        "projected_admitted": int(round(admitted * scale)),
        "projected_admitted_fraction": round(admitted / rows_seen, 4),
        "projected_content_bytes": int(summary.get("content_bytes", 0) * scale),
        "caveat": (
            "PROJECTION, not a measurement. The sample is a prefix of the "
            "crawler's row order (90.0% registry rows first), not a uniform "
            "random sample, so the admitted fraction may not transfer."
        ),
    }


def render_markdown(summary: dict[str, Any], projection: dict[str, Any]) -> str:
    gate = summary.get("gate", {})
    lines = [
        "| measure | value |",
        "|---|---|",
        f"| dataset | `{gate.get('dataset')}` @ `{gate.get('dataset_revision')}` |",
        f"| gate version | `{gate.get('gate_version')}` |",
        f"| rows seen | {gate.get('rows_seen')} |",
        f"| rows fetched from raw | {gate.get('fetched')} |",
        f"| rows admitted | {gate.get('admitted')} |",
        f"| mirror rows | {gate.get('mirror_rows')} |",
        f"| mirror origin recovered | {gate.get('mirror_origin_recovered')} |",
        f"| exact duplicates | {gate.get('exact_duplicates')} |",
        f"| near duplicates | {gate.get('near_duplicates')} |",
        "",
        "### Disposition reasons",
        "| reason | count |",
        "|---|---|",
    ]
    for reason, count in (gate.get("reasons") or {}).items():
        lines.append(f"| `{reason}` | {count} |")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(projection, indent=2))
    lines.append("```")
    return "\n".join(lines)

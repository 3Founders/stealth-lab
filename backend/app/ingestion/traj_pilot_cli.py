"""
Step-0 pilot: stream chat-message SWE-rebench OpenHands trajectories into a
LOCAL shard and measure what a full run would cost.

Run it as its own entry point (`python -m app.ingestion.traj_pilot_cli`),
matching `codemod_cli.py`, rather than through `app.ingestion.admin`: the
admin dispatcher is the production operator surface, and this is a pilot that
refuses to run against anything but a loopback DSN.

Refusals happen before any work, with the reason printed:

- **Non-loopback DSN.** `--shard-dsn-env` takes the *name* of an env var, never
  a DSN, and the resolved host must be loopback. `backend/.env` points
  `DATABASE_URL` at production; a pilot that could reach it would be a
  production write by accident.
- **Held-out ids.** Excluded at instance *and* repository level, from
  `experiments/swebench*/runs/design.json` (`test` + `calibration`).
  Repository-level matters as much as instance-level: ingesting other
  instances from a scored repo still teaches the codebase the held-out
  instances live in.
- **License, per item.** The corpus has NO license column (verified at the
  pinned revision), so `license_name` is joined per instance from the parent
  `nebius/SWE-rebench` and mapped to SPDX. The corpus-level CC-BY-4.0 is
  recorded as provenance, never used as the gate.

What this pilot does and does not spend money on
------------------------------------------------
The raw path (normalize -> `write_normalized_trajectory` -> episodes) is
LLM-free by construction, like `codemod_cli`. The money is one layer down, in
per-episode semantic extraction, which `--semantics N` runs for real on the
first N episodes. `--budget-cap-usd` installs a hard ceiling that
`ingest_budget.guard` checks BEFORE each call, so the cap is a ceiling rather
than a tally. Spend is measured by reading `llm_spend` across the run window
rather than trusting any single call site to have recorded itself -- and the
report says so when those two disagree.

Yield is counted including REJECTIONS. A pilot that reports only what it
accepted cannot tell a good filter from a broken one -- the case-based
reasoning literature's recurring failure is a yield metric with no negative
instances, which measures nothing. The same discipline applies to the paid
layer: an extraction FAILURE, an extraction ABSTENTION (a completed pass that
legitimately found nothing) and a real YIELD are three different numbers, and
a run whose paid layer attempted calls and completed none exits non-zero. The
first step-0 run reported "done" with 13 of 13 calls dead; that is the bug this
accounting exists to make impossible to miss.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

COMMANDS = ("ingest-trajectories",)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})

#: Where the per-instance license join is cached. One JSON file; rebuilt from
#: the pinned parent revision whenever the pin changes.
LICENSE_CACHE_DIR = Path("data/license_maps")


class PilotRefused(RuntimeError):
    """The run was refused before any work, with the reason attached."""


def _dsn_is_loopback(dsn: str) -> bool:
    host = (urlparse(dsn).hostname or "").lower()
    return host in _LOOPBACK_HOSTS


@dataclass
class PilotCounters:
    """Per-item accounting. Every count here is an exit criterion."""

    rows_streamed: int = 0
    rows_resolved: int = 0
    accepted: int = 0
    duplicates: int = 0
    events_written: int = 0
    events_duplicate: int = 0
    episodes_written: int = 0
    malformed: int = 0
    malformed_events: int = 0
    turn_capped: int = 0
    model_patch_bytes: int = 0
    patch_bytes_accepted: int = 0
    patch_bytes_duplicate: int = 0
    semantics_attempted: int = 0
    semantics_succeeded: int = 0
    semantics_abstained: int = 0
    semantics_yielded: int = 0
    semantics_failed: int = 0
    semantics_knowledge_items: int = 0
    llm_calls: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    license_decisions: dict[str, int] = field(default_factory=dict)
    seen_instance_ids: dict[str, int] = field(default_factory=dict)
    semantics_failure_reasons: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def semantics_failed_with(self, reason: str) -> None:
        self.semantics_failed += 1
        self.semantics_failure_reasons[reason] = (
            self.semantics_failure_reasons.get(reason, 0) + 1
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_streamed": self.rows_streamed,
            "rows_resolved": self.rows_resolved,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "events_written": self.events_written,
            "events_duplicate": self.events_duplicate,
            "episodes_written": self.episodes_written,
            "malformed": self.malformed,
            "malformed_events": self.malformed_events,
            "turn_capped": self.turn_capped,
            "model_patch_bytes": self.model_patch_bytes,
            "patch_bytes_accepted": self.patch_bytes_accepted,
            "patch_bytes_duplicate": self.patch_bytes_duplicate,
            "semantics_attempted": self.semantics_attempted,
            "semantics_succeeded": self.semantics_succeeded,
            "semantics_abstained": self.semantics_abstained,
            "semantics_yielded": self.semantics_yielded,
            "semantics_failed": self.semantics_failed,
            "semantics_failure_reasons": dict(self.semantics_failure_reasons),
            "semantics_knowledge_items": self.semantics_knowledge_items,
            "llm_calls": self.llm_calls,
            "rejected": dict(self.rejected),
            "license_decisions": dict(self.license_decisions),
        }


# ---------------------------------------------------------------------------
# Per-instance license join
# ---------------------------------------------------------------------------

def _cache_path(parent_repo: str, revision: str) -> Path:
    slug = parent_repo.replace("/", "__")
    return LICENSE_CACHE_DIR / f"{slug}@{revision[:12]}.json"


def load_instance_licenses(parent_repo: str, revision: str) -> dict[str, str]:
    """`instance_id -> license_name` for the parent benchmark.

    Cached on disk keyed by the pinned revision: the parent dataset is large and
    the pilot joins once per instance, so rebuilding this per run would dominate
    the pilot's own wall time. A pin change invalidates the cache by name.
    """
    cache = _cache_path(parent_repo, revision)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass  # rebuild rather than fail a pilot over a corrupt cache

    from datasets import load_dataset

    mapping: dict[str, str] = {}
    stream = load_dataset(
        parent_repo, split="test", revision=revision, streaming=True,
    )
    for row in stream:
        instance_id = row.get("instance_id")
        if instance_id:
            mapping[str(instance_id)] = str(row.get("license_name") or "")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


# ---------------------------------------------------------------------------
# Pilot
# ---------------------------------------------------------------------------

async def _knowledge_counts(pool) -> dict[str, int]:
    """Live knowledge-item counts.

    Deltas matter, not absolutes: a shard that already holds goals and
    procedures would otherwise make every yield number in this report a lie.
    """
    row = await pool.fetchrow(
        "SELECT "
        " (SELECT count(*) FROM goals WHERE t_invalid IS NULL) AS goals,"
        " (SELECT count(*) FROM knowledge_nodes WHERE node_type='claim' AND t_invalid IS NULL) AS claims,"
        " (SELECT count(*) FROM procedures WHERE t_invalid IS NULL) AS procedures,"
        " (SELECT count(*) FROM observations) AS observations,"
        " (SELECT count(*) FROM episodes) AS episodes,"
        " (SELECT count(*) FROM trace_events) AS trace_events,"
        " (SELECT count(*) FROM agent_traces) AS agent_traces,"
        " (SELECT count(*) FROM quarantined_records) AS quarantined,"
        " (SELECT count(*) FROM ingestion_jobs) AS jobs,"
        " (SELECT count(*) FROM ingestion_contexts) AS contexts"
    )
    return {k: int(v or 0) for k, v in dict(row).items()}


async def _spend_between(pool, start: "datetime", end: "datetime") -> tuple[int, float]:
    """(ledger rows, USD) recorded in `llm_spend` between two instants.

    A trailing `now() - interval` window is wrong for this report: it sweeps up
    spend from an earlier probe, which is how a run that made zero model calls
    came to report $0.20. The interval must be the run's own.

    The row count is returned alongside the sum because a $0.00 reading is only
    meaningful next to how many calls it is supposed to cover. Zero rows beside
    a non-zero attempt count means the *measurement* failed, not that the run
    was free -- and this report must be able to say which.
    """
    from app.services import search_group

    # llm_spend is a log on the search database (every member of a search group, migration 132)
    parts = await search_group.fetch_all(
        pool,
        "SELECT COALESCE(SUM(estimated_cost), 0) AS usd, count(*) AS n FROM llm_spend "
        "WHERE occurred_at >= $1 AND occurred_at <= $2",
        start, end,
    )
    total, rows = sum((p["usd"] for p in parts), 0), sum(int(p["n"]) for p in parts)
    return int(rows or 0), float(total or 0.0)


def _llm_client():
    """The ingestion worker's own shared chat client.

    Reuses `ingestion_jobs._general_compute_client()` rather than building a
    second provider path: that factory already does Vertex-then-General-Compute
    key rotation and connection reuse. It returns a SYNC `OpenAI`, which is the
    shape every extraction module in this repo takes -- and
    `extract_trajectory_semantics` now routes that blocking `.create(...)`
    through `app.utils.aio.run_blocking`, so it is neither frozen-inline on the
    event loop nor a coroutine-shape trap for an `AsyncOpenAI`. Spend is NOT
    wrapped here: `extract_trajectory_semantics` records its own completion
    into the same `llm_spend` ledger (the pilot installs the budget), and a
    second recorder would double-count every call. Returns None when nothing is
    configured.
    """
    from app.services.ingestion_jobs import _general_compute_client

    return _general_compute_client()


def _default_semantics_model() -> str:
    from app.config import settings

    if settings.require("general_compute_judge_model"):
        return settings.require("general_compute_judge_model").strip()
    panel = (settings.require("general_compute_panel_models") or "").split(",")
    for candidate in panel:
        if candidate.strip():
            return candidate.strip()
    return (settings.require("general_compute_fallback_model") or "").strip()


async def _table_bytes(pool) -> int:
    total = await pool.fetchval(
        "SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)::bigint "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    )
    return int(total or 0)


def _semantics_ok(counters: PilotCounters) -> bool:
    """Did the paid layer actually work?

    False whenever a call was attempted and none completed -- the exact shape
    of the first step-0 run (13 of 13 dead, reported as "done"). An attempted-
    and-all-abstained run is True: honest emptiness is not breakage, and the
    report's `abstained` count is there to say so.
    """
    return counters.semantics_attempted == 0 or counters.semantics_succeeded > 0


async def _one_semantic_pass(
    pool, counters: PilotCounters, episode_id: Any, args: argparse.Namespace,
    *, context_id: Optional[str] = None,
) -> None:
    """One paid extraction pass, and an honest verdict on it.

    Three outcomes, never collapsed into one:

    - **yield** -- the pass succeeded and produced at least one
      Goal/Claim/Procedure.
    - **abstention** -- the pass succeeded and produced none. That is a real
      answer about this trajectory, not a defect, and counting it as a failure
      would make an honestly-thorough model look broken.
    - **failure** -- the pass raised. Counted by exception type, printed, and
      it can fail the whole run (see `semantic_extraction.ok`).

    The distinction exists because the first step-0 run reported "done" with
    13 of 13 calls dead: a caller that cannot tell a failure from an abstention
    has no way to notice. `args.semantics` is set to 0 on a budget stop so the
    remaining sub-sample is not spent against a cap that is already gone.
    """
    from app.services.governance import BudgetExceeded
    from app.services.trajectory_semantics import extract_trajectory_semantics

    counters.semantics_attempted += 1
    client = _llm_client()
    if client is None:
        print("  semantics skipped: no general-compute client configured", flush=True)
        counters.semantics_failed_with("no_llm_client")
        return
    try:
        result = await extract_trajectory_semantics(
            pool, str(episode_id), client=client, model=args.semantics_model,
            ingestion_context_id=context_id, scope_type="global",
        )
    except BudgetExceeded as exc:
        # A pre-spend refusal spent nothing. It is neither a failed call nor an
        # abstention, and continuing would only produce more of them.
        counters.semantics_failed_with("budget_exceeded")
        print(f"  semantics budget stop at {episode_id}: {exc}", flush=True)
        args.semantics = 0
        return
    except Exception as exc:  # noqa: BLE001
        counters.semantics_failed_with(type(exc).__name__)
        print(f"  semantics failed on {episode_id}: "
              f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return

    produced = (
        int(result.get("goals") or 0)
        + int(result.get("claims") or 0)
        + int(result.get("procedures") or 0)
    )
    counters.semantics_succeeded += 1
    counters.semantics_knowledge_items += produced
    if produced:
        counters.semantics_yielded += 1
        print(f"  semantics ok on {episode_id}: "
              f"{result.get('goals')} goals / {result.get('claims')} claims / "
              f"{result.get('procedures')} procedures", flush=True)
    else:
        counters.semantics_abstained += 1
        print(f"  semantics abstained on {episode_id} (0 knowledge items)", flush=True)


async def run(pool, args: argparse.Namespace) -> int:
    from app.services.ingestion_context import (
        complete_ingestion_context,
        open_ingestion_context,
    )
    from app.services.ingestion_sources.chat_messages import (
        DATASET_REPO,
        DATASET_REVISION,
        PARENT_REPO,
        PARENT_REVISION,
        ChatMessageMalformedTrajectory,
        NebiusOpenHandsTrajectorySource,
        normalize_chat_message_trajectory,
    )
    from app.services.ingestion_sources.held_out import (
        HeldOutUnavailable,
        load_held_out,
    )
    from app.services.repo_license_policy import classify_spdx
    from app.services.ingestion_sources.verified_solutions_hf import normalize_spdx
    from app.services.trace_worker import (
        write_normalized_trajectory,
        write_trajectory_episodes,
    )
    from app.utils.aio import run_blocking

    # --- refusals, before any work -------------------------------------
    dsn = os.environ.get(args.shard_dsn_env)
    if not dsn:
        print(
            f"ERROR: env var {args.shard_dsn_env} is not set in this shell; "
            "the pilot resolves a DSN by NAME, never by value",
            file=sys.stderr,
        )
        return 2
    if not _dsn_is_loopback(dsn):
        print(
            f"ERROR: refusing to run: {args.shard_dsn_env} points at "
            f"{(urlparse(dsn).hostname or '?')}, which is not loopback. "
            "This pilot writes rows; it may only write a local shard.",
            file=sys.stderr,
        )
        return 2

    try:
        held = load_held_out(args.root, allow_missing=args.allow_missing_designs)
    except HeldOutUnavailable as exc:
        print(f"ERROR: held-out designs unavailable: {exc}", file=sys.stderr)
        return 2

    print(f"held-out: {len(held)} ids across {len(held.snapshots)} designs, "
          f"{len(held.scored_repos)} scored repos", flush=True)

    source = NebiusOpenHandsTrajectorySource(
        limit=args.limit, resolved_only=not args.include_unresolved,
    )
    if not args.semantics_model:
        args.semantics_model = _default_semantics_model()
    counters = PilotCounters()
    licenses = await run_blocking(load_instance_licenses, PARENT_REPO, PARENT_REVISION)
    print(f"license join: {len(licenses)} parent instances", flush=True)

    # Spend is read from the ledger across the run window rather than summed at
    # call sites, so a path that spends without recording still shows up.
    from app.services import ingest_budget

    budget_kw: dict[str, Any] = {}
    if getattr(args, "budget_cap_usd", 0.0):
        budget_kw["cap_usd"] = float(args.budget_cap_usd)
    budget = ingest_budget.install(pool, **budget_kw)
    budget_status = await budget.status(fresh=True)
    print(f"model-spend cap for this run: ${budget_status.cap_usd:.2f}/24h "
          f"(enforced by ingest_budget.guard before every paid call)", flush=True)
    bytes_before = await _table_bytes(pool)
    knowledge_before = await _knowledge_counts(pool)

    started = time.monotonic()
    run_started_at = datetime.now(timezone.utc)
    refs = await run_blocking(source.discover)    # opens the HF stream: network, off the event loop
    seen_episode_for_semantics = 0

    while True:
        ref = await run_blocking(next, refs, None)
        if ref is None:
            break
        counters.rows_streamed += 1

        artifact = await run_blocking(source.fetch, ref)
        payload = json.loads(artifact.content)
        instance_id = str(payload.get("instance_id") or "")
        resolved = int(payload.get("resolved") or 0)
        if resolved == 1:
            counters.rows_resolved += 1

        # Gate order is deliberate: the held-out check runs FIRST so a held-out
        # id is counted as held-out rather than as whatever else is wrong with it.
        if held.is_held_out(instance_id):
            counters.reject("held_out_instance")
            continue
        repo = str(payload.get("repo") or "")
        if repo and repo.lower() in {r.lower() for r in held.scored_repos}:
            counters.reject("held_out_repo")
            continue

        # One trajectory per (task, outcome) across sources, per the plan.
        prior = counters.seen_instance_ids.get(instance_id)
        if prior is not None:
            counters.seen_instance_ids[instance_id] = prior + 1
            counters.reject("duplicate_instance_in_run")
            continue
        counters.seen_instance_ids[instance_id] = 1

        license_name = licenses.get(instance_id)
        if not license_name:
            counters.license_decisions["unmappable"] = (
                counters.license_decisions.get("unmappable", 0) + 1
            )
            counters.reject("license_unmappable")
            continue
        # `license_name` is a GitHub DISPLAY NAME ("MIT License"), not an SPDX
        # slug, so the license-TEXT header matcher returns None for it. The
        # corpus-name mapper that already exists for the verified-solution
        # sources is reused; using the wrong one rejects 100% of the corpus.
        spdx = normalize_spdx(license_name)
        if spdx is None:
            counters.license_decisions["unmappable"] = (
                counters.license_decisions.get("unmappable", 0) + 1
            )
            counters.reject("license_unmappable")
            continue
        verdict = classify_spdx(spdx, source_path=f"{PARENT_REPO}:{instance_id}")
        decision = verdict.decision
        counters.license_decisions[decision] = counters.license_decisions.get(decision, 0) + 1
        if decision != "ALLOW":
            counters.reject(f"license_{decision.lower()}")
            continue

        from dataclasses import replace as dc_replace

        artifact = dc_replace(artifact, license_metadata={
            "spdx_id": spdx,
            "license_name": license_name,
            "decision": decision,
            "allowlist_version": verdict.allowlist_version,
            "corpus_license": "CC-BY-4.0",
            "corpus_license_source": f"{DATASET_REPO}@{DATASET_REVISION[:12]}",
        })

        if payload.get("exit_status") and "Agent reached maximum iteration" in str(payload["exit_status"]):
            counters.turn_capped += 1

        context_id = await open_ingestion_context(
            pool,
            source_type=source.source_type,
            extractor_id="chat_message_adapter",
            extractor_version="1",
            actor_id="step0_pilot",
            scope_type="global",
            scope_entity_id=None,
            source_uri=artifact.uri,
            source_hash=artifact.content_hash,
            visibility="public",
            owner_id=None,
        )

        try:
            trajectory = normalize_chat_message_trajectory(artifact)
        except ChatMessageMalformedTrajectory as exc:
            await pool.execute(
                "INSERT INTO quarantined_records "
                "(source_type, source_uri, raw_content, reason, owner_id, visibility, scope_type) "
                "VALUES ($1,$2,$3,$4,NULL,$5::visibility_level,$6)",
                source.source_type, artifact.uri,
                artifact.content[:200_000], str(exc), "public", "global",
            )
            await complete_ingestion_context(pool, context_id, status="rejected")
            counters.malformed += 1
            counters.reject("malformed")
            continue

        write_result = await write_normalized_trajectory(pool, trajectory)
        episode_result = await write_trajectory_episodes(
            pool, session_id=trajectory.session_id, trajectory=trajectory,
        )

        patch_bytes = int(trajectory.metadata.get("model_patch_bytes") or 0)
        counters.model_patch_bytes += patch_bytes
        counters.malformed_events += trajectory.skipped_malformed_events
        episodes_now = (
            int(episode_result.get("parents_inserted") or 0)
            + int(episode_result.get("children_inserted") or 0)
        )
        if write_result["inserted"] == 0:
            counters.duplicates += 1
            counters.events_duplicate += write_result["records_seen"]
            counters.patch_bytes_duplicate += patch_bytes
        else:
            counters.accepted += 1
            counters.events_written += write_result["inserted"]
            counters.episodes_written += episodes_now
            counters.patch_bytes_accepted += patch_bytes

        # --- the paid layer, on a bounded sub-sample ---------------------
        # `write_session_episodes` returns counts, not row ids, so the episode
        # this pass would act on is looked up by its own session.
        if args.semantics and seen_episode_for_semantics < args.semantics:
            episode_id = await pool.fetchval(
                "SELECT id FROM episodes WHERE session_id = $1 ORDER BY start_ts LIMIT 1",
                trajectory.session_id,
            )
            if episode_id:
                seen_episode_for_semantics += 1
                await _one_semantic_pass(pool, counters, episode_id, args, context_id=context_id)

        await complete_ingestion_context(pool, context_id, status="completed")

        if counters.accepted % 100 == 0:
            print(f"  ...{counters.accepted} accepted "
                  f"({counters.events_written} events)", flush=True)

    elapsed = time.monotonic() - started
    run_ended_at = datetime.now(timezone.utc)
    # The run's own interval, not a trailing window.
    ledger_rows, spend_in_run = await _spend_between(pool, run_started_at, run_ended_at)
    counters.llm_calls = ledger_rows
    bytes_after = await _table_bytes(pool)
    knowledge_after = await _knowledge_counts(pool)

    extractions = await pool.fetchrow(
        "SELECT count(*) AS total,"
        " count(*) FILTER (WHERE status='completed') AS completed,"
        " count(*) FILTER (WHERE status='failed') AS failed,"
        # A row still 'pending' after the run is an orphan, not an abstention.
        # Reported so `completed + failed` can be checked against
        # `semantics_succeeded + semantics_failed` rather than assumed equal.
        " count(*) FILTER (WHERE status='pending') AS pending"
        " FROM trajectory_extractions"
    )

    accepted = counters.accepted
    # "did the paid layer work" is a first-class exit criterion, not a footnote:
    # the first step-0 run reported "done" with 13 of 13 extraction calls dead
    # and nothing but raw rows to show for it. `ok` is False whenever a call was
    # attempted and none of them completed -- the exact shape of that failure --
    # and it is what makes the process exit non-zero.
    semantics_ok = _semantics_ok(counters)
    report = {
        "pilot": "step0_nebius_openhands_trajectories",
        "status": "ok" if semantics_ok else "degraded",
        "dataset": {"repo": DATASET_REPO, "revision": DATASET_REVISION,
                    "parent_repo": PARENT_REPO, "parent_revision": PARENT_REVISION},
        "shard_dsn_env": args.shard_dsn_env,
        "held_out": held.as_dict(),
        "requested_limit": args.limit,
        "resolved_only": not args.include_unresolved,
        "semantics_limit": args.semantics,
        "semantics_model": args.semantics_model or None,
        "budget_cap_usd": budget_status.cap_usd,
        "semantic_extraction": {
            "ok": semantics_ok,
            "attempted": counters.semantics_attempted,
            "succeeded": counters.semantics_succeeded,
            "yielded": counters.semantics_yielded,
            "abstained": counters.semantics_abstained,
            "failed": counters.semantics_failed,
            "failure_reasons": dict(counters.semantics_failure_reasons),
            "knowledge_items": counters.semantics_knowledge_items,
            "usd_per_knowledge_item": (
                round(spend_in_run / counters.semantics_knowledge_items, 6)
                if counters.semantics_knowledge_items else None
            ),
        },
        "spend": {
            "usd_in_run": round(spend_in_run, 6),
            "llm_spend_rows_in_run": ledger_rows,
            "spend_is_accounted_for": ledger_rows >= counters.semantics_succeeded,
            "note": (
                "usd comes from llm_spend over the run's own interval, not from "
                "a call site. spend_is_accounted_for=False means calls completed "
                "but the ledger has fewer rows than that, i.e. the money figure "
                "understates reality and the run is not a valid cost measurement."
            ),
        },
        "counters": counters.as_dict(),
        "wall_time_s": round(elapsed, 2),
        "items_per_hour": round(accepted / elapsed * 3600, 1) if elapsed > 0 else None,
        "db_bytes_delta": bytes_after - bytes_before,
        "bytes_per_accepted_item": (
            round((bytes_after - bytes_before) / accepted, 1) if accepted else None
        ),
        "model_usd_in_run": round(spend_in_run, 6),
        "llm_calls": counters.llm_calls,
        "per_accepted_item": {
            "events": round(counters.events_written / accepted, 2) if accepted else None,
            "episodes": round(counters.episodes_written / accepted, 3) if accepted else None,
            "model_patch_bytes": (
                round(counters.patch_bytes_accepted / accepted, 1) if accepted else None
            ),
            "db_bytes": (
                round((bytes_after - bytes_before) / accepted, 1) if accepted else None
            ),
            "usd": (
                round(spend_in_run / accepted, 6) if accepted else None
            ),
        },
        "knowledge_delta": {
            k: knowledge_after.get(k, 0) - knowledge_before.get(k, 0)
            for k in knowledge_after
        },
        "knowledge_totals_after": knowledge_after,
        "trajectory_extractions": {k: int(v or 0) for k, v in dict(extractions).items()},
    }
    if accepted:
        report["projection_full_corpus"] = {
            "resolved_trajectories_in_corpus": 32161,
            "items": 32161,
            "wall_time_hours": round(32161 / (accepted / elapsed) / 3600, 2) if elapsed > 0 else None,
            "db_bytes_tb": round((bytes_after - bytes_before) / accepted * 32161 / 1e12, 3),
            "usd_at_pilot_rate": round(
                spend_in_run / accepted * 32161, 2
            ),
            "note": (
                "usd covers only what this run actually called; the raw path is "
                "LLM-free, so a real figure needs the extraction layer enabled "
                "for every episode, not a sub-sample."
            ),
        }
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    if not semantics_ok:
        print(
            f"DEGRADED: {counters.semantics_attempted} extraction call(s) attempted, "
            f"0 succeeded ({dict(counters.semantics_failure_reasons)}). The raw "
            f"path wrote {accepted} items; the paid path produced "
            f"{counters.semantics_knowledge_items} knowledge items.",
            file=sys.stderr,
        )
        return 1
    return 0


def add_parsers(sub: Any) -> None:
    p = sub.add_parser("ingest-trajectories")
    p.add_argument("--shard-dsn-env", required=True,
                   help="NAME of the env var holding the LOCAL shard DSN (never the DSN)")
    p.add_argument("--limit", type=int, default=1000,
                   help="maximum resolved trajectories to process")
    p.add_argument("--root", default=".",
                   help="repo root, for the held-out design files")
    p.add_argument("--semantics", type=int, default=0,
                   help="run paid per-episode semantic extraction on this many episodes")
    p.add_argument("--semantics-model", default="",
                   help="model for the semantics pass (default: first general-compute panel model)")
    p.add_argument("--include-unresolved", action="store_true",
                   help="include resolved=0 rows (default: resolved only)")
    p.add_argument("--allow-missing-designs", action="store_true",
                   help="dry-run only: proceed when a design file is unreadable")
    p.add_argument("--budget-cap-usd", type=float, default=0.0,
                   help="hard ceiling for this run's model spend, enforced by "
                        "ingest_budget.guard BEFORE each paid call (0 = use the "
                        "deployment's DAILY_LLM_BUDGET_USD). Set it explicitly when "
                        "DAILY_LLM_BUDGET_USD is a placeholder like 100000.")
    p.add_argument("--out", default=None, help="write the report JSON here")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.ingestion.traj_pilot_cli")
    add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    if args.command not in COMMANDS:
        print(f"ERROR: unknown command {args.command}", file=sys.stderr)
        return 2

    from app.db.session import create_pool

    async def _go() -> int:
        dsn = os.environ.get(args.shard_dsn_env)
        if not dsn:
            print(f"ERROR: {args.shard_dsn_env} is not set", file=sys.stderr)
            return 2
        pool = await create_pool(dsn, max_size=4)
        try:
            return await run(pool, args)
        except PilotRefused as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        finally:
            await pool.close()

    return asyncio.run(_go())


if __name__ == "__main__":
    raise SystemExit(main())

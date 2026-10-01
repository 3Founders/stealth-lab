"""The ingestion ledger (migration 127): every item a pipeline touches, with a closed outcome.

    written   -- knowledge was written; `objects` lists the rows. Terminal.
    rejected  -- a policy said no. Terminal: a re-run never re-decides it.
    failed    -- infrastructure broke. The next run retries it, up to `max_attempts`.

`dedup_key` is identity across sources and pipelines. `claim_identity` answers "is this identity already written
anywhere?" from the database, and the partial unique index settles a race between two runs: the loser's write is
recorded as `rejected/duplicate_identity`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

WRITTEN, REJECTED, FAILED = "written", "rejected", "failed"
# Failures of the infrastructure, not of the item: a model refusing for capacity, a network drop, a timeout. They never
# count toward max_attempts -- an item is given up only when IT keeps failing (2026-10-01: a model outage burned up to
# three attempts of thousands of good items through rapid restarts).
INFRASTRUCTURE_FAILURES = (
    "RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError", "ServiceUnavailableError",
    "ConnectionDoesNotExistError", "InterfaceError", "ConnectionError", "TimeoutError", "OSError",
    "ShardUnavailable", "github_unavailable", "PostgresConnectionError", "TooManyConnectionsError",
    "SemanticJudgmentUnavailable",    # every judge model refused (capacity): identity fails closed, the item waits
)


def _jsonable(value: Optional[dict]) -> dict:
    """A plain dict for the pool's jsonb codec (it encodes; pre-encoding would store a JSON string)."""
    return json.loads(json.dumps(value or {}, default=str))


@dataclass(frozen=True)
class ItemRef:
    """Where an item came from: enough to find the exact row again."""

    item_key: str        # unique within the pipeline
    source: str          # e.g. "hf:nebius/SWE-rebench-openhands-trajectories"
    revision: str        # the full commit sha of the source
    row_ref: str         # the row's own id in the source
    dedup_key: Optional[str] = None


class Ledger:
    def __init__(self, pool: Any, *, pipeline: str, run_id: str, target: str, max_attempts: int = 3):
        self.pool = pool
        self.pipeline = pipeline
        self.run_id = run_id
        self.target = target
        self.max_attempts = max_attempts
        import time as _time

        self.last_activity = _time.monotonic()      # the CLI's stall watchdog reads this (app/ingest/cli.py)

    async def prior(self, item_key: str) -> Optional[dict]:
        row = await self.pool.fetchrow(
            "SELECT status, reason, attempts FROM ingest_ledger WHERE pipeline = $1 AND item_key = $2",
            self.pipeline, item_key)
        return dict(row) if row else None

    async def settled_keys(self) -> set[str]:
        """Every item this pipeline must NOT process again (written, rejected, or failed max_attempts times), in ONE
        query. Checking item by item cost one database round trip per dataset row (21,000 for SWE-rebench alone),
        which dominated a run from a machine far from the database."""
        rows = await self.pool.fetch(
            "SELECT item_key FROM ingest_ledger WHERE pipeline = $1 AND (status IN ($2, $3) "
            "OR (attempts >= $4 AND NOT (reason = ANY($5::text[]))))",
            self.pipeline, WRITTEN, REJECTED, self.max_attempts, list(INFRASTRUCTURE_FAILURES))
        return {r["item_key"] for r in rows}

    async def should_process(self, item_key: str) -> tuple[bool, Optional[str]]:
        """(process?, why not). Terminal rows are never redone; failed rows are retried until max_attempts."""
        prior = await self.prior(item_key)
        if prior is None:
            return True, None
        if prior["status"] in (WRITTEN, REJECTED):
            return False, f"already_{prior['status']}"
        if prior["attempts"] >= self.max_attempts and prior["reason"] not in INFRASTRUCTURE_FAILURES:
            return False, "failed_max_attempts"
        return True, None

    async def identity_owner(self, dedup_key: Optional[str]) -> Optional[dict]:
        """The written item that already holds this identity, in any pipeline, or None."""
        if not dedup_key:
            return None
        row = await self.pool.fetchrow(
            "SELECT pipeline, item_key FROM ingest_ledger WHERE dedup_key = $1 AND status = 'written'", dedup_key)
        return dict(row) if row else None

    async def record(self, ref: ItemRef, status: str, reason: str, *, detail: Optional[dict] = None,
                     objects: Optional[dict] = None) -> str:
        """Upsert the item's outcome. Returns the status actually stored (a lost identity race becomes rejected)."""
        import time as _time

        self.last_activity = _time.monotonic()
        if status not in (WRITTEN, REJECTED, FAILED):
            raise ValueError(f"unknown ledger status {status!r}")
        args = (self.pipeline, ref.item_key, ref.source, ref.revision, ref.row_ref, ref.dedup_key, status, reason,
                _jsonable(detail), _jsonable(objects), self.run_id,
                self.target)
        sql = """
            INSERT INTO ingest_ledger (pipeline, item_key, source, revision, row_ref, dedup_key, status, reason,
                                       detail, objects, run_id, target)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11::uuid, $12)
            ON CONFLICT (pipeline, item_key) DO UPDATE SET
                status = EXCLUDED.status, reason = EXCLUDED.reason, detail = EXCLUDED.detail,
                objects = EXCLUDED.objects, run_id = EXCLUDED.run_id, target = EXCLUDED.target,
                dedup_key = EXCLUDED.dedup_key, attempts = ingest_ledger.attempts + 1, updated_at = now()
        """
        try:
            await self.pool.execute(sql, *args)
            return status
        except Exception as exc:  # asyncpg.UniqueViolationError: another run wrote this identity first
            if status == WRITTEN and type(exc).__name__ == "UniqueViolationError":
                owner = await self.identity_owner(ref.dedup_key)
                lost = (self.pipeline, ref.item_key, ref.source, ref.revision, ref.row_ref, ref.dedup_key, REJECTED,
                        "duplicate_identity", _jsonable({**(detail or {}), "identity_owner": owner,
                                                         "orphaned_objects": objects or {}}),
                        {}, self.run_id, self.target)
                await self.pool.execute(sql, *lost)
                return REJECTED
            raise

    async def counts(self) -> dict:
        rows = await self.pool.fetch(
            "SELECT status, reason, count(*) AS n FROM ingest_ledger WHERE pipeline = $1 AND run_id = $2::uuid "
            "GROUP BY status, reason ORDER BY status, n DESC", self.pipeline, self.run_id)
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["status"], {})[r["reason"]] = int(r["n"])
        return out

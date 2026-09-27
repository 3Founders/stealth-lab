"""Verified examples: the verified solution a Procedure was extracted from (migration 124).

docs/knowledge_side_improvements.md, changes 1-3. Kel's Procedures (steps + APIs + pitfalls) did not
measurably help models; the verified code, shown as a worked example, did. This module:

  * builds the stored example from extraction evidence -- size-capped, and passed through the same
    known-token redaction as execution traces (app.services.trace_redaction) before it is stored;
  * renders it for find_ways (`public_example`), never exposing more than was stored.

The example lives on the Procedure row (procedures.verified_example), so it shares the Procedure's
home shard, visibility, owner and tenant -- no separate object, no separate access rule.
Gated by settings.knowledge_verified_examples (off by default).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

MAX_CODE_CHARS = 20_000
MAX_TASK_CHARS = 4_000


def _redacted(text: str) -> tuple[str, list[str]]:
    from app.services.trace_redaction import _redact_string

    return _redact_string(text)


def build_example(*, task: str, code: str, language: str = "python",
                  verified_by: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The stored shape, or None when there is nothing usable (empty code or task)."""
    task, code = (task or "").strip(), (code or "").strip("\n")
    if not task or not code.strip():
        return None
    truncated = len(code) > MAX_CODE_CHARS or len(task) > MAX_TASK_CHARS
    code, code_hits = _redacted(code[:MAX_CODE_CHARS])
    task, task_hits = _redacted(task[:MAX_TASK_CHARS])
    return {
        "task": task, "code": code, "language": (language or "python")[:40],
        "verified_by": (verified_by or "")[:300] or None,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "redacted": sorted(set(code_hits + task_hits)), "truncated": truncated,
    }


def example_from_evidence(evidence: Any) -> Optional[dict[str, Any]]:
    """The example for a Procedure extracted from a VERIFIED code solution (the observation the
    code-solution extractor reads); the task is the run's task statement, else its goal text."""
    from app.services.procedure_extraction.strategies import verified_code_solution

    solution = verified_code_solution(evidence)
    if solution is None:
        return None
    props = solution.get("properties") or {}
    task = next((str((o.get("properties") or {}).get("text") or "")
                 for o in (getattr(evidence, "observations", None) or [])
                 if isinstance(o, dict) and o.get("observation_type") == "task_statement"), "")
    return build_example(task=task or str(getattr(evidence, "goal_text", "") or ""), code=str(props.get("code") or ""),
                         language=str(props.get("language") or "python"), verified_by=props.get("verified_by"))


async def attach(pool: Any, procedure_row_id: str, example: Mapping[str, Any]) -> None:
    """Store `example` on the Procedure version row, on that row's home shard. The pools' jsonb
    codec (app.db.session) encodes the dict -- passing a JSON string would store a JSON string."""
    from app.services.shards import home_pool

    await (await home_pool(pool, "procedure", str(procedure_row_id), by_row_id=True)).execute(
        "UPDATE procedures SET verified_example = $2::jsonb WHERE id = $1::uuid",
        str(procedure_row_id), dict(example))


def public_example(value: Any) -> Optional[dict[str, Any]]:
    """What find_ways returns for a stored example (None when absent or malformed)."""
    import json

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict) or not value.get("code") or not value.get("task"):
        return None
    return {"task": value["task"], "code": value["code"], "language": value.get("language") or "python",
            "verified_by": value.get("verified_by")}

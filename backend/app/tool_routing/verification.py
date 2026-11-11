"""Signed verification results, verifier reputation, hidden anchors and random audits (db/194, goal_router.md P3-05).

    await register(pool, "ver-1", "nightly checker", "automated", public_key_b64)
    ok, why, rid = await submit(pool, "ver-1", payload, signature_b64)     # a forged signature: (False, "signature:invalid")
    rep = await reputation(pool, "ver-1")                                   # accuracy and the weight it earns
    print(asyncio.run(report("web")))                                       # results with signature status and weight

A result is a claim about one card on one goal and task, signed by the verifier with Ed25519 over canonical(payload).
It is checked against the verifier's registered public key before it is stored; a nonce already used by that verifier
is a replay and is refused.

A verifier's weight is earned, never bought: the Beta(1 + agreed, 1 + disagreed) lower bound (app/tool_routing/trust.py)
of how often its results matched the truth, where the truth is
  * a hidden anchor's expected outcome (tool_verification_anchors), or
  * an audit's re-run outcome, or
  * the downstream verified outcome for that card and goal (tool_trust, db/192), when there are at least
    DOWNSTREAM_MIN judged attempts: pass if at least half passed.
A verifier funded by the provider of the card it checks counts half (CONFLICT_FACTOR); funding never adds weight.
Ranking reads tool_trust, not these results, so no amount of funded verification changes a rank by itself.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

DOWNSTREAM_MIN = 5
CONFLICT_FACTOR = 0.5
AUDIT_RATE = 0.1
PAYLOAD_FIELDS = ("card_id", "goal", "card_version", "task_ref", "outcome", "score", "evidence_ref", "at", "nonce")


def canonical(payload: dict) -> bytes:
    body = {k: payload.get(k) for k in PAYLOAD_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(private_key: Any, payload: dict) -> str:
    """For tool_verifiers (and the checks): an Ed25519 private key signs the canonical payload."""
    return base64.b64encode(private_key.sign(canonical(payload))).decode("ascii")


def public_key_b64(private_key: Any) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def signature_valid(public_key: str, payload: dict, signature: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(base64.b64decode(signature), canonical(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


async def register(pool: Any, verifier_id: str, name: str, kind: str, public_key: str,
                   funded_by: Optional[str] = None) -> None:
    base64.b64decode(public_key, validate=True)
    await pool.execute(
        """INSERT INTO tool_verifiers (id, name, kind, public_key, funded_by) VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind,
               public_key = EXCLUDED.public_key, funded_by = EXCLUDED.funded_by, active = true""",
        verifier_id, name, kind, public_key, funded_by)


async def submit(pool: Any, verifier_id: str, payload: dict, signature: str) -> tuple[bool, str, Optional[int]]:
    """Store one signed result. -> (ok, reason, result id). Nothing is stored unless the signature verifies."""
    v = await pool.fetchrow("SELECT public_key, funded_by, active FROM tool_verifiers WHERE id = $1", verifier_id)
    if v is None or not v["active"]:
        return False, "verifier:unknown", None
    if payload.get("outcome") not in ("pass", "fail") or not payload.get("card_id") or not payload.get("nonce"):
        return False, "payload:invalid", None
    if not signature_valid(v["public_key"], payload, signature):
        return False, "signature:invalid", None
    body = {k: payload.get(k) for k in PAYLOAD_FIELDS}
    rid = await pool.fetchval(
        """
        INSERT INTO tool_verification_results (verifier_id, card_id, goal, card_version, task_ref, outcome, score,
                                          evidence_ref, funded_by, nonce, payload, signature)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12)
        ON CONFLICT (verifier_id, nonce) DO NOTHING
        RETURNING id
        """,
        verifier_id, body["card_id"], str(body["goal"] or ""), str(body["card_version"] or ""),
        str(body["task_ref"] or ""), body["outcome"], body["score"], body["evidence_ref"], v["funded_by"],
        str(body["nonce"]), json.dumps(body), signature)
    if rid is None:
        return False, "replay:nonce_used", None
    return True, "ok", int(rid)


@dataclass
class Reputation:
    verifier_id: str
    agreed: int
    disagreed: int
    on_anchors: int
    on_audits: int
    on_downstream: int
    accuracy_lb: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


async def _truths(pool: Any, rows: list[Any]) -> list[tuple[Any, str, str]]:
    """(result, truth, source) for every result that has a truth: anchor, then audit, then downstream."""
    from app.tool_routing import trust

    out = []
    anchors = {(r["task_ref"], r["card_id"]): r["expected"] for r in await pool.fetch(
        "SELECT task_ref, card_id, expected FROM tool_verification_anchors")}
    downstream: dict[tuple[str, str], Optional[str]] = {}
    for r in rows:
        key = (r["task_ref"], r["card_id"])
        if key in anchors:
            out.append((r, anchors[key], "anchor"))
        elif r["audit_outcome"]:
            out.append((r, r["audit_outcome"], "audit"))
        else:
            dk = (r["card_id"], r["goal"])
            if dk not in downstream:
                ev = await trust.rows(pool, goal=r["goal"], card_ids=[r["card_id"]], limit=1)
                downstream[dk] = (("pass" if ev[0]["wins"] * 2 >= ev[0]["judged"] else "fail")
                                  if ev and ev[0]["judged"] >= DOWNSTREAM_MIN else None)
            if downstream[dk]:
                out.append((r, downstream[dk], "downstream"))
    return out


async def reputation(pool: Any, verifier_id: str) -> Reputation:
    from app.tool_routing import trust

    rows = await pool.fetch("SELECT id, task_ref, card_id, goal, outcome, audit_outcome FROM tool_verification_results "
                            "WHERE verifier_id = $1", verifier_id)
    judged = await _truths(pool, list(rows))
    agreed = sum(1 for r, truth, _src in judged if r["outcome"] == truth)
    return Reputation(verifier_id, agreed, len(judged) - agreed,
                      sum(1 for _r, _t, s in judged if s == "anchor"), sum(1 for _r, _t, s in judged if s == "audit"),
                      sum(1 for _r, _t, s in judged if s == "downstream"),
                      round(trust.lower_bound(agreed, len(judged)), 4))


async def _card_providers(pool: Any, card_id: str) -> set[str]:
    """Who profits from this card: its trials' and listings' providers (a table that does not exist yet is none)."""
    out: set[str] = set()
    for sql in ("SELECT provider FROM tool_trials WHERE card_id = $1", "SELECT provider FROM tool_listings WHERE card_id = $1"):
        try:
            out |= {str(r["provider"]) for r in await pool.fetch(sql, card_id)}
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ not in ("UndefinedTableError", "UndefinedColumnError"):
                raise
    return out


async def weighted_results(pool: Any, goal: str, *, limit: int = 200) -> list[dict]:
    from app.tool_routing import trust

    rows = await pool.fetch(
        """SELECT r.id, r.verifier_id, v.kind, r.card_id, r.goal, r.card_version, r.task_ref, r.outcome, r.score,
                  r.funded_by, r.payload, r.signature, v.public_key, r.at, r.audited_at, r.audit_outcome
           FROM tool_verification_results r JOIN tool_verifiers v ON v.id = r.verifier_id
           WHERE r.goal = ANY($1::text[]) ORDER BY r.at DESC LIMIT $2""", trust._goal_keys(goal), limit)
    reps: dict[str, Reputation] = {}
    out = []
    for r in rows:
        if r["verifier_id"] not in reps:
            reps[r["verifier_id"]] = await reputation(pool, r["verifier_id"])
        payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        sig_ok = signature_valid(r["public_key"], payload, r["signature"])   # re-checked: a row edited later fails
        conflict = bool(r["funded_by"]) and r["funded_by"] in await _card_providers(pool, r["card_id"])
        weight = 0.0 if not sig_ok else reps[r["verifier_id"]].accuracy_lb * (CONFLICT_FACTOR if conflict else 1.0)
        out.append({"id": r["id"], "verifier": r["verifier_id"], "kind": r["kind"], "card_id": r["card_id"],
                    "card_version": r["card_version"], "task_ref": r["task_ref"], "outcome": r["outcome"],
                    "signature": "valid" if sig_ok else "INVALID", "funded_by": r["funded_by"], "conflict": conflict,
                    "verifier_accuracy_lb": reps[r["verifier_id"]].accuracy_lb, "weight": round(weight, 4),
                    "audited": r["audit_outcome"] or "-"})
    return out


def audit_sample(result_ids: list[int], *, rate: float = AUDIT_RATE, seed: str = "") -> list[int]:
    """A random share of results to re-run. Seeded per audit round so no verifier can know ahead which are picked."""
    seed = seed or os.environ.get("VERIFICATION_AUDIT_SEED", "")
    pick = []
    for rid in result_ids:
        h = hashlib.sha256(f"{seed}:{rid}".encode()).digest()
        if int.from_bytes(h[:8], "big") / 2 ** 64 < rate:
            pick.append(rid)
    return pick


async def record_audit(pool: Any, result_id: int, outcome: str) -> None:
    if outcome not in ("pass", "fail"):
        raise ValueError("outcome is pass or fail")
    await pool.execute("UPDATE tool_verification_results SET audited_at = now(), audit_outcome = $2 WHERE id = $1",
                       result_id, outcome)


async def reverify_needed(pool: Any, goal: str) -> list[dict]:
    """Cards to verify again: a version newer than the last verified one, or a recent drop in real-use success."""
    from app.tool_routing import lifecycle, trust

    out = []
    rows = await pool.fetch(
        """SELECT DISTINCT ON (r.card_id) r.card_id, r.card_version, c.extra->>'version' AS current
           FROM tool_verification_results r JOIN tool_cards c ON c.id = r.card_id
           WHERE r.goal = ANY($1::text[]) ORDER BY r.card_id, r.at DESC""", trust._goal_keys(goal))
    for r in rows:
        if r["current"] and r["card_version"] and r["current"] != r["card_version"]:
            out.append({"card_id": r["card_id"], "why": f"new version {r['current']} (last verified {r['card_version']})"})
    for s in await lifecycle.evaluate(pool, goal):
        if s.recent_failure_rate is not None and s.recent_judged >= lifecycle.RECENT_MIN \
                and s.recent_failure_rate > lifecycle.MAX_RECENT_FAILURE:
            out.append({"card_id": s.card_id, "why": f"recent failure rate {s.recent_failure_rate:.0%} over "
                                                     f"{s.recent_judged} attempts"})
    return out


async def report(goal: str, pool: Any = None) -> list[dict]:
    """Verifier results for a goal with signature status and weight (opens its own connection when not given one)."""
    if pool is not None:
        return await weighted_results(pool, goal)
    import asyncpg
    from dotenv import dotenv_values

    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    conn = await asyncpg.connect(os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or "", timeout=30)
    try:
        return await weighted_results(conn, goal)
    finally:
        await conn.close()

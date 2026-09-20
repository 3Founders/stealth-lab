"""Threshold alerts over ``ops_metrics.collect`` -- de-duplicated, not per-event.

An alert is a pure function of one metrics snapshot (``evaluate``). ``run`` adds the noise control:
  * a warning must be true for ``for_checks`` consecutive evaluations before it notifies (critical: 1),
  * once notified it re-notifies only after ``renotify_minutes``,
  * when it clears after having notified it sends ONE "resolved".
State lives in ``ops_alert_state`` (migration 100). Notification is a webhook (``OPS_ALERT_WEBHOOK_URL``;
the body carries both ``text`` (Slack/Mattermost) and ``content`` (Discord)); with no webhook the alerts are
printed and reflected in the exit code only. Thresholds are env-configurable (``OPS_*``), never hardcoded
provider limits.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

CRITICAL = "critical"
WARNING = "warning"


def _f(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


@dataclass(frozen=True)
class Thresholds:
    retryable_failed_repeat: int = 5        # OPS_RETRYABLE_REPEAT_MAX  jobs stuck retrying with >=3 attempts
    retryable_failed_total: int = 50        # OPS_RETRYABLE_TOTAL_MAX
    expired_leases: int = 5                 # OPS_EXPIRED_LEASES_MAX
    projection_lag_s: float = 600.0         # OPS_PROJECTION_LAG_SECONDS
    projection_pending: int = 5000          # OPS_PROJECTION_PENDING_MAX
    shard_warn_bytes: float = 0.0           # OPS_SHARD_WARN_BYTES (0 = capacity alerts off until configured)
    shard_rollover_bytes: float = 0.0       # OPS_SHARD_ROLLOVER_BYTES
    connection_use: float = 0.8             # OPS_CONNECTION_USE_MAX
    provider_failures_15m: int = 5          # OPS_PROVIDER_FAILURES_15M
    fallback_rate: float = 0.3              # OPS_FALLBACK_RATE_MAX
    fallback_min_samples: int = 20          # OPS_FALLBACK_MIN_SAMPLES
    retrieval_degraded_rate: float = 0.1    # OPS_RETRIEVAL_DEGRADED_RATE_MAX
    retrieval_min_samples: int = 10         # OPS_RETRIEVAL_MIN_SAMPLES
    for_checks: int = 2                     # OPS_ALERT_FOR_CHECKS
    renotify_minutes: float = 60.0          # OPS_ALERT_RENOTIFY_MINUTES

    @classmethod
    def from_env(cls) -> "Thresholds":
        d = cls()
        return cls(
            retryable_failed_repeat=int(_f("OPS_RETRYABLE_REPEAT_MAX", d.retryable_failed_repeat)),
            retryable_failed_total=int(_f("OPS_RETRYABLE_TOTAL_MAX", d.retryable_failed_total)),
            expired_leases=int(_f("OPS_EXPIRED_LEASES_MAX", d.expired_leases)),
            projection_lag_s=_f("OPS_PROJECTION_LAG_SECONDS", d.projection_lag_s),
            projection_pending=int(_f("OPS_PROJECTION_PENDING_MAX", d.projection_pending)),
            shard_warn_bytes=_f("OPS_SHARD_WARN_BYTES", d.shard_warn_bytes),
            shard_rollover_bytes=_f("OPS_SHARD_ROLLOVER_BYTES", d.shard_rollover_bytes),
            connection_use=_f("OPS_CONNECTION_USE_MAX", d.connection_use),
            provider_failures_15m=int(_f("OPS_PROVIDER_FAILURES_15M", d.provider_failures_15m)),
            fallback_rate=_f("OPS_FALLBACK_RATE_MAX", d.fallback_rate),
            fallback_min_samples=int(_f("OPS_FALLBACK_MIN_SAMPLES", d.fallback_min_samples)),
            retrieval_degraded_rate=_f("OPS_RETRIEVAL_DEGRADED_RATE_MAX", d.retrieval_degraded_rate),
            retrieval_min_samples=int(_f("OPS_RETRIEVAL_MIN_SAMPLES", d.retrieval_min_samples)),
            for_checks=int(_f("OPS_ALERT_FOR_CHECKS", d.for_checks)),
            renotify_minutes=_f("OPS_ALERT_RENOTIFY_MINUTES", d.renotify_minutes),
        )


@dataclass
class Alert:
    key: str
    severity: str
    message: str
    detail: dict = field(default_factory=dict)


def evaluate(m: dict[str, Any], t: Thresholds, verify: Optional[dict[str, bool]] = None) -> list[Alert]:
    """Pure: metrics snapshot (+ last verify results: name -> ok) -> the alerts that are true right now."""
    out: list[Alert] = []
    j, p, r, pr, cost = m["jobs"], m["providers"], m["retrieval"], m["projection"], m["cost"]
    if j["permanent_failed_60m"] > 0:
        out.append(Alert("ingestion.permanent_failure", CRITICAL,
                         f"{j['permanent_failed_60m']} job(s) failed permanently in the last hour (admin failures)",
                         {"count": j["permanent_failed_60m"]}))
    if j["retryable_failed_repeat"] >= t.retryable_failed_repeat or j["retryable_failed"] >= t.retryable_failed_total:
        out.append(Alert("ingestion.repeated_retryable_failures", WARNING,
                         f"{j['retryable_failed']} retryable-failed jobs ({j['retryable_failed_repeat']} with >=3 attempts)"))
    if j["expired_leases"] >= t.expired_leases:
        out.append(Alert("ingestion.expired_leases_spike", WARNING,
                         f"{j['expired_leases']} expired leases (workers crashing or timing out)"))
    if pr["oldest_pending_s"] > t.projection_lag_s or pr["pending"] > t.projection_pending:
        out.append(Alert("projection.lag", WARNING,
                         f"projection lag: {pr['pending']} pending, oldest {pr['oldest_pending_s']:.0f}s"))
    if pr["failed"] > 0:
        out.append(Alert("projection.outbox_failure", CRITICAL,
                         f"{pr['failed']} projection outbox entries failed (admin drain-projections / reindex)"))
    for s in m["shards"]:
        sid = s["shard_id"]
        if not s.get("reachable", False):
            if s["status"] in ("active", "full", "readonly", "unhealthy"):
                out.append(Alert(f"shard.unavailable.{sid}", CRITICAL, f"shard {sid} unreachable: {s.get('error', '')}"))
            continue
        gb = s["size_bytes"] / 1e9
        if t.shard_rollover_bytes and s["size_bytes"] >= t.shard_rollover_bytes:
            out.append(Alert(f"shard.capacity_rollover.{sid}", CRITICAL,
                             f"shard {sid} at rollover size ({gb:.2f} GB): run 'stealth-ops capacity --apply'"))
        elif t.shard_warn_bytes and s["size_bytes"] >= t.shard_warn_bytes:
            out.append(Alert(f"shard.capacity_warn.{sid}", WARNING, f"shard {sid} nearing capacity ({gb:.2f} GB)"))
        if s["connection_use"] >= t.connection_use:
            out.append(Alert(f"db.connection_saturation.{sid}", WARNING,
                             f"shard {sid} connections {s['connections']}/{s['max_connections']}"))
    if p["embedding_failures_15m"] >= t.provider_failures_15m:
        out.append(Alert("provider.embedding_outage", CRITICAL, f"{p['embedding_failures_15m']} embedding failures in 15m"))
    if p["judge_failures_15m"] >= t.provider_failures_15m or p["judge_unavailable_60m"] >= t.provider_failures_15m:
        out.append(Alert("provider.judge_outage", CRITICAL,
                         f"judge chain unavailable ({p['judge_failures_15m']} failures/15m, "
                         f"{p['judge_unavailable_60m']} judge_unavailable decisions/60m)"))
    if p["judge_decisions_60m"] >= t.fallback_min_samples and p["fallback_rate_60m"] > t.fallback_rate:
        out.append(Alert("provider.semantic_fallback_spike", WARNING,
                         f"{p['fallback_rate_60m']:.0%} of judgments served by a fallback (primary {p['primary_judge']})"))
    if r["requests_60m"] >= t.retrieval_min_samples and r["degraded_rate_60m"] > t.retrieval_degraded_rate:
        out.append(Alert("retrieval.degraded", WARNING, f"{r['degraded_rate_60m']:.0%} of retrievals degraded in the last hour"))
    if cost["state"] == "exceeded":
        out.append(Alert("cost.budget_exceeded", CRITICAL,
                         f"daily model budget exceeded: ${cost['spent_24h_usd']} of ${cost['daily_cap_usd']} (workers stopped leasing)"))
    elif cost["state"] == "warn":
        out.append(Alert("cost.budget_near_limit", WARNING,
                         f"daily model budget {cost['fraction']:.0%} used: ${cost['spent_24h_usd']} of ${cost['daily_cap_usd']}"))
    elif cost["state"] == "unavailable":
        out.append(Alert("cost.ledger_unavailable", CRITICAL, "cost ledger unreadable: workers will not lease"))
    for name, ok in (verify or {}).items():
        if not ok:
            out.append(Alert(f"verify.{name}", CRITICAL, f"last {name} run FAILED"))
    return out


async def last_verify(pool: Any) -> dict[str, bool]:
    rows = await pool.fetch("SELECT alert_key, firing FROM ops_alert_state WHERE alert_key LIKE 'verify:%'")
    return {r["alert_key"].split(":", 1)[1]: not r["firing"] for r in rows}


async def record_verify(pool: Any, name: str, ok: bool) -> None:
    """firing = the check FAILED. Kept until the same check passes."""
    await pool.execute(
        "INSERT INTO ops_alert_state (alert_key, firing, severity, first_seen) VALUES ($1, $2, 'critical', now()) "
        "ON CONFLICT (alert_key) DO UPDATE SET firing = EXCLUDED.firing, updated_at = now()", f"verify:{name}", not ok)


def _post(url: str, text: str) -> None:
    body = json.dumps({"text": text, "content": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def _default_send(text: str) -> None:
    url = os.environ.get("OPS_ALERT_WEBHOOK_URL")
    if url:
        _post(url, text)


async def run(pool: Any, metrics: dict[str, Any], t: Optional[Thresholds] = None, *, notify: bool = True,
              send: Any = None) -> dict[str, Any]:
    """Evaluate, de-duplicate, notify. Never raises because of the notification channel."""
    t = t or Thresholds.from_env()
    send = send or _default_send
    alerts = {a.key: a for a in evaluate(metrics, t, await last_verify(pool))}
    state = {r["alert_key"]: r for r in await pool.fetch("SELECT * FROM ops_alert_state")}
    notified: list[str] = []
    resolved: list[str] = []

    def _send(text: str) -> None:
        if notify:
            try:
                send(text)
            except Exception:  # noqa: BLE001 -- a broken webhook must not break ingestion tooling
                pass

    for key, a in alerts.items():
        prev = state.get(key)
        consecutive = (prev["consecutive"] if prev and prev["firing"] else 0) + 1
        need = 1 if a.severity == CRITICAL else t.for_checks
        last_notified = prev["last_notified"] if prev else None
        due = last_notified is None or bool(await pool.fetchval(
            "SELECT now() - $1::timestamptz > make_interval(mins => $2::int)", last_notified, int(t.renotify_minutes)))
        do_notify = consecutive >= need and due
        await pool.execute(
            "INSERT INTO ops_alert_state (alert_key, firing, consecutive, severity, first_seen, last_notified, detail) "
            "VALUES ($1, true, $2, $3, now(), CASE WHEN $4 THEN now() END, $5::jsonb) "
            "ON CONFLICT (alert_key) DO UPDATE SET firing = true, consecutive = $2, severity = $3, "
            "first_seen = CASE WHEN ops_alert_state.firing THEN ops_alert_state.first_seen ELSE now() END, "
            "last_notified = CASE WHEN $4 THEN now() ELSE ops_alert_state.last_notified END, detail = $5::jsonb, updated_at = now()",
            key, consecutive, a.severity, do_notify, json.dumps(a.detail))
        if do_notify:
            notified.append(key)
            _send(f"[{a.severity.upper()}] stealth ingestion: {a.message}")
    for key, prev in state.items():
        if key.startswith("verify:") or key in alerts or not prev["firing"]:
            continue
        await pool.execute(
            "UPDATE ops_alert_state SET firing = false, consecutive = 0, updated_at = now() WHERE alert_key = $1", key)
        if prev["last_notified"] is not None:
            resolved.append(key)
            _send(f"[RESOLVED] stealth ingestion: {key}")
    return {"firing": sorted(alerts), "critical": sorted(k for k, a in alerts.items() if a.severity == CRITICAL),
            "notified": notified, "resolved": resolved, "messages": {k: a.message for k, a in alerts.items()}}

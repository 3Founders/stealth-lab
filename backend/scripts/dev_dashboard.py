"""Read-only dev dashboard: one glanceable page of what the substrate is
actually doing, for watching a TESTING_GUIDE run in progress.

THIS IS NOT PRODUCT. It is a local dev tool, deliberately excluded from the
Docker image (see .dockerignore) and bound to 127.0.0.1 only -- never
0.0.0.0 -- because it has no auth. It reads aggregate counts, never raw
trace content, so nothing sensitive is rendered; that is a property of the
queries below and it should stay that way if anyone extends this.

Every query is wrapped: a table this DB has not migrated to yet renders as
"unavailable" with the error, rather than 500-ing the whole page. That
matters here specifically -- the dev database has historically sat behind
main's migration head, and a dashboard that dies on one missing table is
worse than one that says which table is missing.

No writes anywhere. Safe to leave running during a sweep.

    python backend/scripts/dev_dashboard.py      # http://127.0.0.1:8090/
"""
from __future__ import annotations

import asyncio
import html
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

HOST = "127.0.0.1"
PORT = int(os.environ.get("DEV_DASHBOARD_PORT", "8090"))
REFRESH_SECONDS = 5

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        import asyncpg

        _pool = await asyncpg.create_pool(
            os.environ["DATABASE_URL"], min_size=1, max_size=2
        )
    return _pool


# --------------------------------------------------------------- querying
#
# Each section returns a list of (label, value) rows. A failure becomes one
# row rather than an exception, so a partially-migrated DB still renders.


async def _val(pool, sql, default="-"):
    try:
        v = await pool.fetchval(sql)
        return "0" if v is None else f"{v:,}" if isinstance(v, int) else str(v)
    except Exception as exc:  # noqa: BLE001 -- surfacing it IS the behaviour
        return f"unavailable ({str(exc).splitlines()[0][:70]})"


async def _rows(pool, sql):
    try:
        return await pool.fetch(sql)
    except Exception as exc:  # noqa: BLE001
        return exc


def _grouped(rows, key, count, empty="(none)"):
    if isinstance(rows, Exception):
        return [("query failed", str(rows).splitlines()[0][:70])]
    if not rows:
        return [(empty, "0")]
    return [(str(r[key]), f"{r[count]:,}") for r in rows]


async def section_ingestion(pool):
    out = [
        ("trace_events", await _val(pool, "SELECT count(*) FROM trace_events")),
        ("observations", await _val(pool, "SELECT count(*) FROM observations")),
    ]
    jobs = await _rows(
        pool,
        "SELECT status, count(*) AS c FROM ingestion_jobs GROUP BY 1 ORDER BY 1",
    )
    out += [(f"ingestion_jobs / {k}", v) for k, v in _grouped(jobs, "status", "c")]
    return out


async def section_episodes(pool):
    # Coverage MUST be the INTERSECTION, not a ratio of two independent
    # counts. Ingestion reads .claude/traces/ (hook events -> trace_events)
    # while episode assembly reads ~/.claude/projects/ (transcripts ->
    # episodes); the two populate the same session_id namespace but from
    # different sources, so they can drift apart completely. The first cut
    # of this panel divided one total by the other and rendered "100%" over
    # sets with ZERO sessions in common, then "300%" once assembly ran
    # ahead -- a number that actively hid the exact break the founding-loop
    # audit was looking for. Overlap is the only honest denominator: an
    # observation can only resolve a justification episode when its own
    # session appears on BOTH sides.
    ingested = await _val(pool, "SELECT count(DISTINCT session_id) FROM trace_events")
    assembled = await _val(
        pool,
        "SELECT count(DISTINCT session_id) FROM episodes WHERE session_id IS NOT NULL",
    )
    overlap = await _val(
        pool,
        "SELECT count(*) FROM ("
        " SELECT session_id FROM episodes WHERE session_id IS NOT NULL"
        " INTERSECT SELECT session_id FROM trace_events) x",
    )
    pct = "-"
    try:
        i, o = int(ingested.replace(",", "")), int(overlap.replace(",", ""))
        pct = f"{(100.0 * o / i):.0f}%" if i else "n/a (no sessions ingested)"
    except ValueError:
        pass
    return [
        ("episodes", await _val(pool, "SELECT count(*) FROM episodes")),
        ("sessions in trace_events", ingested),
        ("sessions with an episode", assembled),
        ("sessions on BOTH sides", overlap),
        ("coverage (overlap / ingested)", pct),
    ]


async def section_founding_loop(pool):
    out = [
        (
            "claims (knowledge_nodes node_type='claim')",
            await _val(
                pool,
                "SELECT count(*) FROM knowledge_nodes WHERE node_type = 'claim'",
            ),
        ),
        (
            "  of those, live (t_invalid IS NULL)",
            await _val(
                pool,
                "SELECT count(*) FROM knowledge_nodes"
                " WHERE node_type = 'claim' AND t_invalid IS NULL",
            ),
        ),
        ("episode_links", await _val(pool, "SELECT count(*) FROM episode_links")),
        ("claim_sources", await _val(pool, "SELECT count(*) FROM claim_sources")),
    ]
    procs = await _rows(
        pool,
        "SELECT verification_state AS s, count(*) AS c FROM procedures"
        " WHERE t_invalid IS NULL GROUP BY 1 ORDER BY 1",
    )
    out += [
        (f"procedures / {k}", v) for k, v in _grouped(procs, "s", "c", "(no procedures)")
    ]
    return out


async def section_governance(pool):
    # Cheap recent-activity counts only -- deliberately not a spend report.
    return [
        (
            "rate_limit_events (last 1h)",
            await _val(
                pool,
                "SELECT count(*) FROM rate_limit_events"
                " WHERE occurred_at > now() - interval '1 hour'",
            ),
        ),
        (
            "llm_spend calls (last 24h)",
            await _val(
                pool,
                "SELECT count(*) FROM llm_spend"
                " WHERE occurred_at > now() - interval '24 hours'",
            ),
        ),
        (
            "llm_spend estimated $ (last 24h)",
            await _val(
                pool,
                "SELECT COALESCE(round(sum(estimated_cost), 4), 0) FROM llm_spend"
                " WHERE occurred_at > now() - interval '24 hours'",
            ),
        ),
    ]


def section_observability():
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    # The DSN value itself is never rendered -- on/off is the whole signal.
    return [("SENTRY_DSN", "ON (events would be reported)" if dsn else "off (not set)")]


# ----------------------------------------------------------------- render

CSS = """
:root{color-scheme:light dark}
body{font:14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
     margin:0;padding:24px;background:#0f1115;color:#e6e6e6}
h1{font-size:16px;margin:0 0 4px}
.meta{color:#8b93a1;font-size:12px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
section{border:1px solid #262a33;border-radius:6px;padding:12px 14px;background:#151821}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;
   color:#7aa2f7;margin:0 0 10px}
table{width:100%;border-collapse:collapse}
td{padding:3px 0;vertical-align:top}
td.v{text-align:right;font-weight:600;white-space:nowrap;padding-left:12px}
td.k{color:#a9b1c1}
.bad{color:#f7768e}
"""


def render(sections) -> str:
    parts = []
    for title, rows in sections:
        body = "".join(
            '<tr><td class="k">{}</td><td class="v{}">{}</td></tr>'.format(
                html.escape(k),
                " bad" if "unavailable" in str(v) or "failed" in str(v) else "",
                html.escape(str(v)),
            )
            for k, v in rows
        )
        parts.append(
            f"<section><h2>{html.escape(title)}</h2><table>{body}</table></section>"
        )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<meta http-equiv='refresh' content='{REFRESH_SECONDS}'>"
        "<title>StealthLab dev dashboard</title>"
        f"<style>{CSS}</style>"
        "<h1>StealthLab &mdash; dev dashboard (read-only)</h1>"
        f"<div class='meta'>{now} &middot; refreshes every {REFRESH_SECONDS}s "
        f"&middot; {html.escape(_dsn_hint())}</div>"
        f"<div class='grid'>{''.join(parts)}</div>"
    )


def _dsn_hint() -> str:
    url = os.environ.get("DATABASE_URL", "")
    # host/dbname only -- credentials must never reach the page.
    tail = url.rsplit("@", 1)[-1] if "@" in url else "(DATABASE_URL unset)"
    return f"db: {tail}"


async def collect():
    pool = await get_pool()
    return [
        ("Ingestion", await section_ingestion(pool)),
        ("Episode assembly", await section_episodes(pool)),
        ("The founding loop", await section_founding_loop(pool)),
        ("Governance", await section_governance(pool)),
        ("Observability", section_observability()),
    ]


async def build_page() -> str:
    return render(await collect())


async def print_once() -> None:
    """Same numbers, as text -- so a run can be pasted into a commit or a
    board note without a screenshot."""
    print(_dsn_hint())
    for title, rows in await collect():
        print(f"\n=== {title} ===")
        for k, v in rows:
            print(f"  {k:<44} {v}")


# ------------------------------------------------------------------- app

def make_app():
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="StealthLab dev dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index():  # noqa: D401
        return await build_page()

    return app


app = make_app()


if __name__ == "__main__":
    if "--once" in sys.argv:
        # Smoke path: prove the queries run, without starting a server.
        asyncio.run(print_once())
        raise SystemExit(0)
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

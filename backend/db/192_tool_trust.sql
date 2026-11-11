-- 192_tool_trust.sql
--
-- Next free number: 191 (task runs) was highest before this file.
--
-- What this adds (docs/goal_router.md P3-01): tool_trust, a materialized view of verified reuse per (tool card, goal):
-- wins, judged attempts, the last outcome and when, and a Wilson 95% lower bound for SQL ordering. Ranking uses the
-- exact Beta-posterior lower bound computed in app/tool_routing/trust.py from wins/judged, so two lucky passes never
-- outrank two hundred verified ones. Stars and "verified" badges are not inputs.
--
-- Evidence, verified only:
--   * task_runs (191), tool arm: pass is a win; fail and a tool error are losses. A gate refusal, a missing tool or a
--     budget stop judged nothing and is left out.
--   * tool_observations (162) from real use, checked by tests or a judge (never self_report), attributed to a card by
--     its connector host, only when exactly one card has that host (a shared gateway host cannot say which card).
-- trial_calls / paid_calls stay 0 until trials (193) and listings (195) exist; the columns are here so readers do not
-- change when they do.
--
-- Refresh: SELECT refresh_tool_trust();  (CONCURRENTLY, so readers are not blocked)
-- Access: application only, like 160/162/163.
-- Idempotent.

CREATE MATERIALIZED VIEW IF NOT EXISTS tool_trust AS
WITH card_hosts AS (
    SELECT host, min(id) AS card_id
    FROM (
        SELECT id, substring(lower(extra->>'connector_url') FROM '^[a-z][a-z0-9+.-]*://([^/?#:]+)') AS host
        FROM tool_cards
        WHERE extra ? 'connector_url'
    ) h
    WHERE host IS NOT NULL
    GROUP BY host
    HAVING count(*) = 1
),
evidence AS (
    SELECT card_id, goal_id AS goal, (status = 'pass') AS win, started_at AS at, 'compare' AS source
    FROM task_runs
    WHERE arm = 'tool' AND card_id IS NOT NULL AND status IN ('pass', 'fail', 'error')
      AND COALESCE(error_class, '') NOT LIKE 'gate:%'
      AND COALESCE(error_class, '') NOT IN ('no_tool', 'budget')
    UNION ALL
    SELECT c.card_id, o.tool_goal AS goal, (o.outcome = 'pass') AS win, o.at, 'use' AS source
    FROM tool_observations o
    JOIN card_hosts c ON c.host = o.ref
    WHERE o.used AND o.outcome IN ('pass', 'fail') AND o.check_kind IN ('tests', 'judge')
)
SELECT
    card_id,
    COALESCE(goal, '') AS goal,
    count(*) FILTER (WHERE win)::int AS wins,
    count(*)::int AS judged,
    count(*) FILTER (WHERE source = 'compare')::int AS from_comparisons,
    count(*) FILTER (WHERE source = 'use')::int AS from_use,
    (array_agg(CASE WHEN win THEN 'pass' ELSE 'fail' END ORDER BY at DESC))[1] AS last_outcome,
    max(at) AS last_at,
    0::int AS trial_calls,
    0::int AS paid_calls,
    -- Wilson score lower bound (z = 1.645, one-sided 95%): an ordering key only; trust.py has the exact Beta bound
    ((count(*) FILTER (WHERE win))::float8 / count(*) + 1.3530 / (2 * count(*))
       - 1.645 * sqrt(((count(*) FILTER (WHERE win))::float8 / count(*))
                      * (1 - (count(*) FILTER (WHERE win))::float8 / count(*)) / count(*)
                      + 1.3530 / (4 * count(*)::float8 * count(*))))
      / (1 + 1.3530 / count(*)) AS wilson_lb
FROM evidence
GROUP BY card_id, COALESCE(goal, '');

CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_trust ON tool_trust (card_id, goal);
CREATE INDEX IF NOT EXISTS idx_tool_trust_goal ON tool_trust (goal, wilson_lb DESC);

CREATE OR REPLACE FUNCTION refresh_tool_trust() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY tool_trust;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_trust FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_trust FROM authenticated;
    END IF;
END $$;

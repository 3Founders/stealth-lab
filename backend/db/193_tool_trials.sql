-- 193_tool_trials.sql
--
-- Next free number: 192 (tool trust) was highest before this file.
--
-- What this adds (docs/goal_router.md P3-02): provider-funded trials. A provider puts credit behind a new tool card for
-- one goal; the router gives that card a small, capped share of the goal's calls ("New - free trial"), the person
-- opts in, and each call is paid from the provider's credit, not the person's.
--
--   * tool_trials: provider, card, goal, credit and spend, call cap and use, a per-person cap, the share of the goal's
--     calls (at most 20%), the window, the tool's risk, and the status.
--   * tool_trial_uses: one row per trial call (who, when, cost), for the per-person cap and the market report (P3-07).
--
-- Rules the database itself holds: spend never exceeds credit and credit is never negative; uses never exceed the cap;
-- a share is between 0 and 0.2; a trial is R0/R1, or R2/R3 only when it is a read-only surface (a defensive scan);
-- R4 never. app/tool_routing/trials.py consume() debits in one statement guarded by all of these and the window.
--
-- Access: application only, like 160/162/163.
-- Idempotent.

CREATE TABLE IF NOT EXISTS tool_trials (
    id              bigserial PRIMARY KEY,
    provider        text NOT NULL,                          -- who funds it (an account or org id)
    card_id         text NOT NULL REFERENCES tool_cards (id) ON DELETE CASCADE,
    goal            text NOT NULL,                          -- a tool-goal key ("web") or a Goal uuid
    credit_usd      numeric(12, 6) NOT NULL CHECK (credit_usd >= 0),
    spent_usd       numeric(12, 6) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
    max_calls       integer NOT NULL CHECK (max_calls > 0),
    used_calls      integer NOT NULL DEFAULT 0 CHECK (used_calls >= 0),
    per_user_calls  integer NOT NULL DEFAULT 5 CHECK (per_user_calls > 0),
    share           real NOT NULL DEFAULT 0.1 CHECK (share > 0 AND share <= 0.2),
    risk            text NOT NULL DEFAULT 'R0' CHECK (risk IN ('R0', 'R1', 'R2', 'R3', 'R4')),
    read_only       boolean NOT NULL DEFAULT true,
    window_start    timestamptz NOT NULL DEFAULT now(),
    window_end      timestamptz NOT NULL,
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'exhausted', 'expired')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (spent_usd <= credit_usd),
    CHECK (used_calls <= max_calls),
    CHECK (window_end > window_start),
    CHECK (risk IN ('R0', 'R1') OR (risk IN ('R2', 'R3') AND read_only))
);

CREATE INDEX IF NOT EXISTS idx_tool_trials_goal ON tool_trials (goal, status, window_end);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_trials_active ON tool_trials (card_id, goal) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS tool_trial_uses (
    id          bigserial PRIMARY KEY,
    trial_id    bigint NOT NULL REFERENCES tool_trials (id) ON DELETE CASCADE,
    owner_id    text NOT NULL,
    at          timestamptz NOT NULL DEFAULT now(),
    cost_usd    numeric(12, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0)
);

CREATE INDEX IF NOT EXISTS idx_tool_trial_uses_trial ON tool_trial_uses (trial_id, owner_id);

ALTER TABLE tool_trials ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_trial_uses ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_trials FROM anon;
        REVOKE ALL ON tool_trial_uses FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_trials FROM authenticated;
        REVOKE ALL ON tool_trial_uses FROM authenticated;
    END IF;
END $$;

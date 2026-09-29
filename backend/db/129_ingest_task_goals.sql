-- Migration 129: one Goal per benchmark task, shared by every pipeline that ingests that task.
-- Next free migration number: 130.
--
-- WHY: the same SWE task arrives from several sources -- its gold patch (SWE-rebench, SWE-bench-extra, V2, SWE-Gym)
-- and agent runs on it (OpenHands trajectories). All of that knowledge must meet on ONE Goal: the verified solution,
-- the Benchmark, the agents' Procedures and their failure Claims. The first pipeline to reach a task names its Goal
-- (one model call from the issue) and records it here; every later pipeline attaches to it.
--
-- task_key is source-independent (`swe:<instance_id>`); the row keeps who named it and from which source.
-- Additive, idempotent.

CREATE TABLE IF NOT EXISTS ingest_task_goals (
    task_key       TEXT        PRIMARY KEY,
    goal_id        UUID        NOT NULL,
    canonical_name TEXT        NOT NULL,
    named_by       TEXT        NOT NULL,
    source         TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingest_task_goals_goal ON ingest_task_goals (goal_id);

-- A Way runs inside a Room, in steps a person can see, and can be restarted from the middle.
--
-- Why (docs/blinkit/presentation_rooms.md): after paying, a person is taken to Rooms, where the Way's steps appear
-- one at a time as a presentation. The left sidebar is minimised, the right rail offers similar Ways. They press
-- Start once the declared inputs are given. If a step is wrong, they edit it and restart from there — and that
-- restart is charged for the steps it actually re-runs, because the price is split per step.
--
-- What this adds:
--
--   rooms.display_name       the goal name as written ("Create a Research Report in neurips format"). `rooms.title`
--                            is a slug (CHECK ^#[a-z0-9]...), so the sidebar has nowhere to show the real name.
--   rooms.way_submission_id  which Way this room is running, so the room can render its run.
--   way_runs.room_id         the room hosting the run (a run made outside a room keeps NULL).
--   way_runs.step_inputs     what the person supplied, per step; the run reads these, never a live prompt.
--   goal_submission_steps.inputs        the author's declaration of a step's inputs (buyer-supplied values).
--   way_runs.restarts        how many times a run was restarted, so a charge cannot be dodged by restarting.
--
-- Additive and idempotent. 226 is free; 225 is the highest applied.

ALTER TABLE rooms ADD COLUMN IF NOT EXISTS display_name text;
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS way_submission_id uuid;

ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS room_id uuid;
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS step_inputs jsonb NOT NULL DEFAULT '{}';
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS restarts integer NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_way_runs_room ON way_runs (room_id) WHERE room_id IS NOT NULL;
-- One live room per (owner, Way): pressing "Run" twice must not make two rooms for the same Way.
CREATE UNIQUE INDEX IF NOT EXISTS rooms_owner_way_live ON rooms (owner_sub, way_submission_id)
    WHERE way_submission_id IS NOT NULL AND deleted_at IS NULL;

-- A step may declare inputs the buyer fills in before the run can start (db/227 explains the shape). Absent = the
-- step takes nothing, which is the normal case and what every existing step already is.
ALTER TABLE goal_submission_steps ADD COLUMN IF NOT EXISTS inputs jsonb;

COMMENT ON COLUMN rooms.display_name IS
    'The Way goal name as written, shown in the sidebar. `title` stays the slug the unique index needs.';
COMMENT ON COLUMN way_runs.step_inputs IS
    'Buyer-supplied inputs by step order and input key. Frozen at start, so a run cannot silently change its inputs.';
COMMENT ON COLUMN goal_submission_steps.inputs IS
    'Inputs this step asks for: [{key, label, required, help}]. NULL = the step declares none.';

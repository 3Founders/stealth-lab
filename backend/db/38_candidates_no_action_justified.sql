-- Final-V1 eval finding B: propose_synthesis crashed on real Postgres with
--   column "no_action_justified" of relation "candidates" does not exist
--
-- Real code<->schema drift, not a new feature: a debate Candidate's
-- `no_action_justified` flag is declared on the model
-- (app/models/debate.py), set by the debate engine
-- (app/debate/engine.py), read by Layer-1 eval (app/eval/layer1.py), and
-- written by BOTH persisters
-- (app/services/loop.py, app/services/human_participation.py) --
-- but the column was never added to `candidates` (defined in
-- db/02_loop.sql). Offline debate tests use a FakePool and never noticed;
-- only a real `propose_synthesis` run hits the missing column.
--
-- Additive, idempotent, no data rewrite: existing rows take the default.
ALTER TABLE candidates
    ADD COLUMN IF NOT EXISTS no_action_justified BOOLEAN NOT NULL DEFAULT false;

-- 176_delegate_cross_owner.sql
--
-- Next free number: 175 (room_docs_latex) was highest before this file.
--
-- What this adds: people in a shared room can call each other's connected agents (@alice/claude). A relay task has always belonged to the person
-- who owns the agent (owner_sub) and was sent by that same person. It now also records who asked (requester_sub) and which server room it was
-- asked in (room_id), so the asker can see the task's progress and the answer can be posted back into that room for everyone. Both are optional:
-- tasks made before this file, and tasks for a room that is only on one device, have neither.
--
-- Idempotent.

ALTER TABLE delegate_tasks ADD COLUMN IF NOT EXISTS requester_sub text CHECK (requester_sub IS NULL OR char_length(requester_sub) BETWEEN 1 AND 128);
ALTER TABLE delegate_tasks ADD COLUMN IF NOT EXISTS room_id uuid;

CREATE INDEX IF NOT EXISTS delegate_tasks_requester_idx ON delegate_tasks (requester_sub, room, created_at DESC) WHERE requester_sub IS NOT NULL;

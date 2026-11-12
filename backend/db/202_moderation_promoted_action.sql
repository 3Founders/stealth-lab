-- 202_moderation_promoted_action.sql
--
-- Next free number: 201 (procedure approval tiers) was highest before this file.
--
-- The moderation event log allowed hidden/restored/removed/withdrawn; a tier promotion is a distinct
-- action and scripts/review_procedures.py records it. Add 'promoted' to the closed vocabulary.
-- Idempotent.

ALTER TABLE way_moderation_events DROP CONSTRAINT IF EXISTS way_moderation_events_action_check;
ALTER TABLE way_moderation_events ADD CONSTRAINT way_moderation_events_action_check
    CHECK (action IN ('hidden', 'restored', 'removed', 'withdrawn', 'promoted'));

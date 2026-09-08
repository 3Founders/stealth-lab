-- Next free migration number: 49.
--
-- People layer: opt-in public contributor profile.  Additive + idempotent.
-- No backfill (fresh-start rule).  No existing row changes ownership or
-- visibility.
--
-- WHY A SEPARATE TABLE, NOT A COLUMN ON users:
--   `users` (migration 28) is server-derived identity — issuer, subject,
--   display_name, email, is_active.  A profile is a user-controlled
--   *publication decision*: it exists only after the person has seen the
--   disclosure and chosen to be listed.  Keeping it out of `users` means
--   the mere existence of a row already carries "this person opted in",
--   and deleting the row (data-rights) fully retracts the public listing
--   without touching the identity record.
--
-- INV-01 (launch-compliance): private by default.  `visibility` defaults
-- to 'private'; nothing about a person is world-readable until they set
-- it to 'public', and `disclosed_at` records when they last acknowledged
-- what that exposes (name + aggregate contribution counts).  The toggle
-- is audited through services/audit.py::record_audit_event
-- (action='profile_visibility_changed') — no second audit path.
--
-- Counts shown on a public profile / the /v1/contributors leaderboard are
-- computed live from existing provenance columns (procedures.owner_id,
-- knowledge_nodes.created_by, publication_records.actor_user_id); this
-- table stores no denormalised aggregate — there is no per-actor score.

CREATE TABLE IF NOT EXISTS contributor_profiles (
    user_id       UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    visibility    TEXT NOT NULL DEFAULT 'private'
                    CHECK (visibility IN ('private', 'public')),
    -- when the person last acknowledged the public-listing disclosure.
    -- Set whenever they submit the disclosure (whichever choice they make),
    -- so "was the person informed" is always answerable.
    disclosed_at  TIMESTAMPTZ,
    -- optional one-line self-description, rendered only while visibility='public'.
    tagline       TEXT,
    t_created     TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_updated     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Leaderboard / people-search only ever scan the opted-in set.
CREATE INDEX IF NOT EXISTS idx_contributor_profiles_public
    ON contributor_profiles (user_id)
    WHERE visibility = 'public';

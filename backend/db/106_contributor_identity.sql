-- Next free migration number: 107.
--
-- V1 contributor identity: public keळ username + avatar + onboarding state,
-- layered onto the EXISTING contributor_profiles table (migration 48) rather
-- than a parallel model. Additive + idempotent, no backfill.
--
-- WHY ON contributor_profiles, NOT users:
--   users (migration 28) is server-derived AUTHENTICATION identity --
--   issuer, external_subject, display_name (copied once from the IdP claim
--   at first login), email. None of that is user-controlled and none of it
--   is meant to be public. The keळ username/avatar are a PUBLICATION
--   decision the person makes about their own attribution -- exactly the
--   kind of user-controlled field contributor_profiles already exists to
--   hold (migration 48's own header comment). Putting username there keeps
--   "who is this, authentication-wise" and "what name do their
--   contributions show" from ever being the same column.
--
-- WHY contributor_profiles NOW GETS CREATED EARLIER THAN "opted into public
-- listing": a username must exist for every account from onboarding, not
-- only for the subset who later flip visibility='public'. The row's mere
-- existence therefore no longer implies public disclosure -- it never did
-- structurally (visibility already defaulted private), but this migration
-- makes explicit that `username`/`avatar_locator`/`onboarding_complete` are
-- populated regardless of visibility. The public-read surface
-- (app/services/contributors.py::public_profile/search_public/leaderboard)
-- is UNCHANGED here and keeps gating on visibility='public' -- INV-01 is
-- not touched by this migration.

ALTER TABLE contributor_profiles
    ADD COLUMN IF NOT EXISTS username            TEXT,
    ADD COLUMN IF NOT EXISTS avatar_locator       TEXT,
    ADD COLUMN IF NOT EXISTS onboarding_complete  BOOLEAN NOT NULL DEFAULT FALSE;

-- Case-insensitive uniqueness on the ACTIVE username only. A partial unique
-- index (not a CHECK/generated column) so accounts provisioned before this
-- migration ran (username IS NULL, back-filled lazily on next profile read,
-- per app/services/contributors.py) never collide with each other under the
-- index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contributor_profiles_username_ci
    ON contributor_profiles (lower(username))
    WHERE username IS NOT NULL;

-- Every username a user has ever held (including their current one, written
-- at rename time). Old names stay reserved in V1 -- rename() checks this
-- table too, so "CopperFox" renamed away can never be claimed by someone
-- else and silently become their profile at an old, still-shared URL.
CREATE TABLE IF NOT EXISTS username_history (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    old_username  TEXT NOT NULL,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_username_history_name_ci
    ON username_history (lower(old_username));
CREATE INDEX IF NOT EXISTS idx_username_history_user
    ON username_history (user_id);

-- Enforce "old usernames stay reserved" AT THE DATABASE, not just in
-- application code: Postgres has no native cross-table UNIQUE constraint,
-- so a trigger is the real guarantee that a name in username_history can
-- never become someone's active username again, race-safe under
-- concurrent renames (it runs inside the same row lock as the UPDATE/
-- INSERT it guards). Raises the same unique_violation SQLSTATE the plain
-- active-username index would, so callers already handling that error for
-- ordinary collisions handle this for free.
CREATE OR REPLACE FUNCTION reject_reused_username() RETURNS trigger AS $$
BEGIN
    IF NEW.username IS NOT NULL AND EXISTS (
        SELECT 1 FROM username_history WHERE lower(old_username) = lower(NEW.username)
    ) THEN
        RAISE EXCEPTION 'username % is reserved (previously used)', NEW.username
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_contributor_profiles_username_not_reused ON contributor_profiles;
CREATE TRIGGER trg_contributor_profiles_username_not_reused
    BEFORE INSERT OR UPDATE OF username ON contributor_profiles
    FOR EACH ROW WHEN (NEW.username IS NOT NULL)
    EXECUTE FUNCTION reject_reused_username();

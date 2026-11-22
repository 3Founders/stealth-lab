-- 219: reviews on a Way.
--
-- A review is one person's verdict on one published Way: a 1-5 rating and a sentence or two. It is
-- shown beside the Way, never mixed into the capability numbers — a rating is opinion, and the
-- evidence panel is measurement. Keeping them in different tables is what keeps that distinction
-- honest.
--
-- One review per person per Way: a second submission edits the first (the unique index), so a Way
-- cannot be inflated by repeating yourself.
--
-- `scope` and `source` are the storage rule (CLAUDE.md hard rule 2): every row says what visibility
-- class it belongs to and where it came from. Reviews on a published Way are public by definition.
--
-- Additive and idempotent.

CREATE TABLE IF NOT EXISTS way_reviews (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id   UUID NOT NULL,                  -- goal_submissions.id: the Way listing being reviewed
    procedure_row_id UUID,                          -- procedures.id of the version at review time, when known
    author_user_id  UUID NOT NULL,                  -- users.id: who wrote it
    author_name     TEXT,                           -- display name at write time; never an email or a subject id
    rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body            TEXT NOT NULL CHECK (length(body) BETWEEN 3 AND 4000),
    scope           TEXT NOT NULL DEFAULT 'public',
    source          TEXT NOT NULL DEFAULT 'website',
    hidden          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_way_review_author
    ON way_reviews (submission_id, author_user_id);

CREATE INDEX IF NOT EXISTS idx_way_reviews_submission
    ON way_reviews (submission_id, created_at DESC)
    WHERE hidden = FALSE;

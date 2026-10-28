-- 154_contact_requests.sql
--
-- Next free number: 153 was highest before this file.
--
-- What this adds: the website's "Contact" form (POST /v1/contact). One row per enquiry. The ROW IS THE SOURCE OF TRUTH:
-- the email to the team is a best-effort notification (notified_at stays NULL if it failed, so nothing is silently lost).
--
-- This is not knowledge-graph data and carries no tenant: it is a visitor writing to us. So there is no scope column, and the
-- table is closed to every role but the backend's own: RLS is ON with NO policies, and the Supabase API roles are revoked, so
-- the public anon key can neither read nor insert a row (the common mistake is an anon insert policy or a readable table).
--
-- Data kept is the minimum the form asks for. delete_after is the retention date (12 months, a decision for the founders, see
-- reports/Contact CTA implementation options.md); NO job enforces it yet, so a deletion job must be added before launch.
-- Idempotent.

CREATE TABLE IF NOT EXISTS contact_requests (
    id           uuid PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    email        text NOT NULL CHECK (char_length(email) BETWEEN 3 AND 254),
    company      text CHECK (company IS NULL OR char_length(company) <= 160),
    topic        text NOT NULL DEFAULT 'general' CHECK (topic IN ('general', 'pilot', 'security', 'other')),
    message      text NOT NULL CHECK (char_length(message) BETWEEN 1 AND 4000),
    source_path  text CHECK (source_path IS NULL OR char_length(source_path) <= 200),
    status       text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'replied', 'closed', 'spam')),
    notified_at  timestamptz,
    delete_after timestamptz NOT NULL DEFAULT (now() + interval '12 months')
);

CREATE INDEX IF NOT EXISTS contact_requests_new_idx ON contact_requests (created_at DESC) WHERE status = 'new';
CREATE INDEX IF NOT EXISTS contact_requests_unnotified_idx ON contact_requests (created_at) WHERE notified_at IS NULL;

ALTER TABLE contact_requests ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON contact_requests FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON contact_requests FROM authenticated';
    END IF;
END
$$;

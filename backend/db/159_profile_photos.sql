-- 159_profile_photos.sql
--
-- Next free number: 158 (org provider key declarations) was highest before this file.
--
-- What this adds: the profile photo of a signed-in person, so it follows them to every device (before this it lived only in one
-- browser's localStorage). One row per Supabase subject. The website shrinks the picture to a small square before upload; the
-- server keeps at most 64 KB and re-checks that the bytes really are a JPEG, PNG or WebP (routes in mcp_server/photo_routes.py).
--
-- Access: like 154, 155 and 157, RLS is ON with NO policies and the Supabase API roles are revoked, so the public anon key can
-- neither read nor write a row. Only the application, after proving who the caller is, reads or writes their own row.
--
-- Retention: until the person removes it. Idempotent.

CREATE TABLE IF NOT EXISTS profile_photos (
    sub          text PRIMARY KEY CHECK (char_length(sub) BETWEEN 1 AND 128),
    content_type text NOT NULL CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
    data         bytea NOT NULL CHECK (octet_length(data) BETWEEN 1 AND 65536),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE profile_photos ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON profile_photos FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON profile_photos FROM authenticated;
    END IF;
END
$$;

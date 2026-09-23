-- Migration 107: claimed_projects -- the missing bridge between an
-- anonymous local `.stealth` workspace and an authenticated keळ account.
--
-- IDENTITY: `project_id` here is a STABLE UUID persisted locally at
-- `.stealth/meta.json`'s `stable_project_id` field (see
-- app.stealth.claim.ensure_stable_project_id) -- deliberately NOT the
-- path-derived `project_id` `stealth_edit_ledger` (migration 92) and
-- `init_workspace` use (`sha256(realpath(repo_root))[:24]`), because that
-- one silently changes if the workspace folder is renamed or moved. A
-- claim is meant to survive exactly that, so it needs an identity the
-- path hash cannot give it. The two identities are related only by a
-- one-time correlation performed locally at claim/bootstrap time (the
-- MCP tool that already has filesystem access to repo_path computes
-- both); this table only ever stores the stable one.
--
-- OWNERSHIP: `owner_subject` is the verified OIDC/Supabase `sub` claim --
-- the SAME value space `stealth_edit_ledger.actor` and REST's
-- `AuthenticatedPrincipal.subject` already use (app.services.authn.Actor,
-- app.services.auth_context.AuthContext.subject), not `users.id`. Kept as
-- a raw subject rather than a `users(id)` FK because the MCP tool surface
-- (where a claim is made) has no existing seam that resolves a bare
-- verified subject into a `users.id` UUID -- introducing that plumbing
-- for this one feature would be more architecture than this migration
-- needs; the raw-subject convention already established by
-- stealth_edit_ledger.actor is reused instead, not replaced.
--
-- project_id is the PRIMARY KEY (not just unique): the ownership
-- invariant this table exists to enforce -- "a project must not be
-- claimable by a second user after it is already claimed" -- is a
-- straight `INSERT ... ON CONFLICT (project_id) DO NOTHING` plus an
-- application-level owner_subject comparison (app.stealth.claim.
-- claim_project). No transfer mechanism exists yet; a second, different
-- subject's claim attempt is refused, not merged or overwritten.
--
-- BOOTSTRAP SNAPSHOT: one-time, not continuous sync. The actual snapshot
-- bytes (the six allowed `.stealth/*.md` files + this project's
-- stealth_edit_ledger activity, bundled as one JSON document) live in the
-- EXISTING object-storage seam (app.services.object_storage, `raw_objects`
-- migration 96) -- this table keeps only the locator/hash/size, same
-- discipline every other large-payload table here already follows.
-- snapshot_* stay NULL until bootstrap succeeds (claim and bootstrap
-- happen in the same tool call in practice, but are two separable steps
-- so a bootstrap failure never has to roll back a successful claim).
--
-- Fresh-start rule: additive, NO in-migration backfill. Idempotent:
-- CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS claimed_projects (
    project_id           UUID PRIMARY KEY,
    owner_subject         TEXT NOT NULL,
    claimed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    snapshot_sha256         TEXT REFERENCES raw_objects(sha256),
    snapshot_locator         TEXT,
    snapshot_size_bytes      BIGINT,
    bootstrapped_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_claimed_projects_owner
    ON claimed_projects (owner_subject, claimed_at DESC);

-- 194_verification.sql
--
-- Next free number: 193 (tool trials) was highest before this file.
--
-- What this adds (docs/goal_router.md P3-05): a verification market whose results are signed, whose verifiers earn
-- weight only by being right, and which money cannot steer.
--
--   * tool_verifiers: who checks tools (automated, judge, human, provider-funded), their Ed25519 public key, and who funds
--     them. A verifier's weight is computed, never stored: app/tool_routing/verification.py reputation().
--   * tool_verification_results: one signed claim "card X, version V, on goal G, task T: pass/fail", with its provenance
--     (verifier, funder, evidence ref) and the signature over the canonical payload. A result whose signature does not
--     verify is refused before it is stored. The nonce makes a replayed result a duplicate.
--   * tool_verification_anchors: hidden tasks with a known answer per card. Verifiers are not told which tasks are
--     anchors; their answers on anchors measure their accuracy. Application only, never exposed.
--   * Audits: a random share of results is re-run (audited_at, audit_outcome); disagreement costs the verifier weight.
--
-- Ranking does not read this table: it reads tool_trust (192, verified reuse). Verification informs re-verification
-- and the market report (195/P3-07); a provider can fund verifiers but not buy rank.
--
-- Access: application only, like 160/162/163.
-- Idempotent.

CREATE TABLE IF NOT EXISTS tool_verifiers (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('automated', 'judge', 'human', 'provider')),
    public_key  text NOT NULL,                    -- Ed25519 raw public key, base64
    funded_by   text,                             -- a provider id, when someone pays for this verifier
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_verification_results (
    id             bigserial PRIMARY KEY,
    verifier_id    text NOT NULL REFERENCES tool_verifiers (id),
    card_id        text NOT NULL,
    goal           text NOT NULL,
    card_version   text NOT NULL DEFAULT '',
    task_ref       text NOT NULL,
    outcome        text NOT NULL CHECK (outcome IN ('pass', 'fail')),
    score          real,
    evidence_ref   text,
    funded_by      text,
    nonce          text NOT NULL,
    payload        jsonb NOT NULL,                -- exactly what was signed
    signature      text NOT NULL,
    at             timestamptz NOT NULL DEFAULT now(),
    audited_at     timestamptz,
    audit_outcome  text CHECK (audit_outcome IS NULL OR audit_outcome IN ('pass', 'fail')),
    UNIQUE (verifier_id, nonce)
);

CREATE INDEX IF NOT EXISTS idx_tool_verif_results_card ON tool_verification_results (card_id, goal, at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_verif_results_verifier ON tool_verification_results (verifier_id, at DESC);

CREATE TABLE IF NOT EXISTS tool_verification_anchors (
    task_ref    text NOT NULL,
    card_id     text NOT NULL,
    goal        text NOT NULL,
    expected    text NOT NULL CHECK (expected IN ('pass', 'fail')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_ref, card_id)
);

ALTER TABLE tool_verifiers ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_verification_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_verification_anchors ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_verifiers, tool_verification_results, tool_verification_anchors FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_verifiers, tool_verification_results, tool_verification_anchors FROM authenticated;
    END IF;
END $$;

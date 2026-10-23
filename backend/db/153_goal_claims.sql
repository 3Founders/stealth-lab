-- 153_goal_claims.sql
--
-- Next free number: 152 was highest before this file.
--
-- What this adds: a Goal's CONTRACT as linked claims, and a Goal's STATUS computed from the evidence on them
-- (docs/verification_changes_prop.md sections 4.1 and 4.2; docs/evidence_upload_design.md).
--
--   1. goal_claims      the link: (goal, claim, role). role = 'assumption' (must hold before) or 'guarantee' (must hold
--                       after). Bi-temporal like `goals` (migration 83): a link is ended with t_invalid and a changed
--                       contract is a NEW link, never an edit. A claim is a row of `knowledge_nodes` with node_type='claim'
--                       (confirmed against the live schema, 2026-10-23); a trigger refuses anything else.
--   2. goal_claim_state one row per live link: the claim's own state (closed or not) and its evidence counts.
--   3. goal_status      one row per Goal: verified | open | failed | contested | expired, COMPUTED. There is NO stored status
--                       column and `goals.status` (candidate | active | deprecated | merged) keeps its meaning: it is the
--                       record's lifecycle, this is whether the contract is proven.
--
-- HOW THE STATUS IS COMPUTED (the rules, in order). Only GUARANTEE claims gate it; an assumption claim can end the Goal's status
-- (expiry) but is never itself evidence.
--   evidence on a claim = rows of `evidence` (migration 24) with target_type='claim', live (t_invalid IS NULL).
--     a pass  = direction 'supports', outcome 'success', evidence_type execution_result or reproduction (the types the verified gate
--               of procedures counts); a fail = the same two types with direction 'contradicts' or outcome 'failure'.
--     Anything else (observation, document, external_source, artifact, experiment, benchmark, and human_review, which is the next
--     item) is recorded but NEVER makes a claim verified: a self-report cannot prove a contract.
--     Counted by independence group (DISTINCT COALESCE(independence_group, id)), as procedure_evidence_stats does.
--   claim state: pass and fail -> contested; fail only -> failed; pass only -> verified; neither -> open.
--   Goal status:
--     expired    a linked claim (either role) was closed (t_invalid set, or claim_status superseded | stale | retracted | invalid)
--                AT OR AFTER the Goal's first evidence: the contract the evidence was about no longer holds as written.
--     failed     any live guarantee claim failed
--     contested  any live guarantee claim is contested
--     verified   at least one live guarantee claim and every one of them verified
--     open       everything else, including a Goal with no guarantee claim and a link to a claim closed before any evidence
--   A link to a claim that was closed BEFORE any evidence existed is a dead link: it is ignored, not an expiry.
--
-- WHAT THIS DOES NOT DO. No composition across the Goal tree (a parent's status from its children), and human review as typed
-- evidence: both are next. The client's file-hash expiry stays client-side (stealthlab-mcp status); on the server a recorded
-- hash is superseded by a newer signature (view local_check_effective, migration 152), which lowers the client-check counts rather
-- than expiring the Goal.
--
-- ISOLATION. Row-level security with NO fallback, as the routing logs (migration 150): a link is visible to everyone when its
-- visibility is 'public', otherwise only to the owner and to the readers bound for the request (the caller's subject and
-- organisations), or under the explicit system scope. The views are security_invoker, so they read `evidence` (tenant RLS,
-- migration 29/150) as the CALLER.
--
-- GATE AND WRITER TOGETHER (hard rule 6): the trigger that refuses a non-claim and the tombstone-only rule land here with their
-- writer, app/services/goal_claims.py. Idempotent: safe to re-run. Fresh-start: additive DDL only, no backfill, no data writes.

CREATE TABLE IF NOT EXISTS goal_claims (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    goal_id          UUID NOT NULL REFERENCES goals(id),
    claim_id         UUID NOT NULL REFERENCES knowledge_nodes(id),
    role             TEXT NOT NULL,

    -- V0 gate fields: nullable at the DB layer on purpose, required by app/services/v0_gate.py at the write boundary
    provenance       TEXT,
    created_by       TEXT,
    visibility       visibility_level NOT NULL DEFAULT 'private',
    owner_id         TEXT,
    scope_type       TEXT,
    scope_entity_id  TEXT,

    -- bi-temporal, as goals (migration 83): a link is ended with t_invalid, never edited
    t_valid          TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid        TIMESTAMPTZ,
    t_created        TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_expired        TIMESTAMPTZ
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'goal_claims_role_chk') THEN
        ALTER TABLE goal_claims ADD CONSTRAINT goal_claims_role_chk CHECK (role IN ('assumption', 'guarantee'));
    END IF;
END $$;

-- one live link per (goal, claim, role) PER OWNER: two people may each state the same contract privately
CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_claims_live ON goal_claims (goal_id, claim_id, role, COALESCE(owner_id, '')) WHERE t_invalid IS NULL;
CREATE INDEX IF NOT EXISTS idx_goal_claims_claim ON goal_claims (claim_id) WHERE t_invalid IS NULL;

-- ============================================================
-- The gate: a link points at a CLAIM. knowledge_nodes holds every kind of node, so a foreign key alone cannot say it.
-- ============================================================
CREATE OR REPLACE FUNCTION sl_goal_claims_claim_only() RETURNS trigger AS $$
DECLARE kind TEXT;
BEGIN
    SELECT node_type INTO kind FROM knowledge_nodes WHERE id = NEW.claim_id;
    IF kind IS DISTINCT FROM 'claim' THEN
        RAISE EXCEPTION 'goal_claims.claim_id % is not a claim (node_type=%)', NEW.claim_id, COALESCE(kind, '<missing>');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_goal_claims_claim_only') THEN
        CREATE TRIGGER tg_goal_claims_claim_only BEFORE INSERT ON goal_claims
            FOR EACH ROW EXECUTE FUNCTION sl_goal_claims_claim_only();
    END IF;
END $$;

-- ============================================================
-- Invalidate-and-append: DELETE refused; the only UPDATE is ending the link (t_invalid), once.
-- ============================================================
CREATE OR REPLACE FUNCTION sl_goal_claims_only_end() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'goal_claims is invalidate-and-append: DELETE rejected -- end the link with t_invalid and add a new one';
    END IF;
    IF OLD.t_invalid IS NOT NULL THEN
        RAISE EXCEPTION 'goal_claims link % is already ended -- add a new link instead', OLD.id;
    END IF;
    IF ROW(NEW.id, NEW.goal_id, NEW.claim_id, NEW.role, NEW.provenance, NEW.created_by, NEW.visibility, NEW.owner_id,
           NEW.scope_type, NEW.scope_entity_id, NEW.t_valid, NEW.t_created, NEW.t_expired)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.goal_id, OLD.claim_id, OLD.role, OLD.provenance, OLD.created_by, OLD.visibility, OLD.owner_id,
           OLD.scope_type, OLD.scope_entity_id, OLD.t_valid, OLD.t_created, OLD.t_expired) THEN
        RAISE EXCEPTION 'goal_claims: only ending the link (t_invalid) may change -- add a new link instead';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'tg_goal_claims_only_end') THEN
        CREATE TRIGGER tg_goal_claims_only_end BEFORE UPDATE OR DELETE ON goal_claims
            FOR EACH ROW EXECUTE FUNCTION sl_goal_claims_only_end();
    END IF;
END $$;

-- ============================================================
-- Row-level security, no fallback (migration 150's routing rule).
-- ============================================================
ALTER TABLE goal_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE goal_claims FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'goal_claims' AND policyname = 'goal_claims_visibility') THEN
        CREATE POLICY goal_claims_visibility ON goal_claims FOR ALL
            USING (sl_routing_allows(visibility::text, owner_id))
            WITH CHECK (sl_routing_allows(visibility::text, owner_id));
    END IF;
END $$;

-- ============================================================
-- Views (security_invoker: they run with the CALLER's row-level security, not the owner's).
-- ============================================================

-- One row per live link: the claim's own state, and the evidence on it.
CREATE OR REPLACE VIEW goal_claim_state WITH (security_invoker = true) AS
SELECT gc.id AS link_id, gc.goal_id, gc.claim_id, gc.role,
       (kn.t_invalid IS NOT NULL OR kn.claim_status IN ('superseded', 'stale', 'retracted', 'invalid')) AS claim_closed,
       -- when the claim stopped holding as written; a status-only closure carries no time, so it counts as now
       CASE WHEN kn.t_invalid IS NOT NULL OR kn.claim_status IN ('superseded', 'stale', 'retracted', 'invalid')
            THEN COALESCE(kn.t_invalid, kn.t_expired, now()) END AS closed_at,
       COALESCE(ev.pass_groups, 0) AS pass_groups,
       COALESCE(ev.fail_groups, 0) AS fail_groups,
       ev.first_evidence_at,
       CASE WHEN COALESCE(ev.fail_groups, 0) > 0 AND COALESCE(ev.pass_groups, 0) > 0 THEN 'contested'
            WHEN COALESCE(ev.fail_groups, 0) > 0 THEN 'failed'
            WHEN COALESCE(ev.pass_groups, 0) > 0 THEN 'verified'
            ELSE 'open' END AS state
FROM goal_claims gc
JOIN knowledge_nodes kn ON kn.id = gc.claim_id
LEFT JOIN LATERAL (
    SELECT count(DISTINCT COALESCE(e.independence_group, e.id::text))
               FILTER (WHERE e.evidence_type IN ('execution_result', 'reproduction')
                         AND e.direction = 'supports' AND e.outcome_status = 'success')   AS pass_groups,
           count(DISTINCT COALESCE(e.independence_group, e.id::text))
               FILTER (WHERE e.evidence_type IN ('execution_result', 'reproduction')
                         AND (e.direction = 'contradicts' OR e.outcome_status = 'failure')) AS fail_groups,
           min(e.t_created) AS first_evidence_at
    FROM evidence e
    WHERE e.target_type = 'claim' AND e.target_id = gc.claim_id AND e.t_invalid IS NULL
) ev ON TRUE
WHERE gc.t_invalid IS NULL;

-- One row per Goal: the computed status, with the counts behind it. Never stored.
CREATE OR REPLACE VIEW goal_status WITH (security_invoker = true) AS
WITH first_ev AS (
    SELECT goal_id, min(first_evidence_at) FILTER (WHERE role = 'guarantee') AS first_evidence_at
    FROM goal_claim_state GROUP BY goal_id
), agg AS (
    SELECT s.goal_id,
           count(*) FILTER (WHERE s.role = 'guarantee' AND NOT s.claim_closed)                         AS guarantees,
           count(*) FILTER (WHERE s.role = 'guarantee' AND NOT s.claim_closed AND s.state = 'verified')  AS verified,
           count(*) FILTER (WHERE s.role = 'guarantee' AND NOT s.claim_closed AND s.state = 'failed')    AS failed,
           count(*) FILTER (WHERE s.role = 'guarantee' AND NOT s.claim_closed AND s.state = 'contested') AS contested,
           count(*) FILTER (WHERE s.role = 'guarantee' AND NOT s.claim_closed AND s.state = 'open')      AS open_claims,
           count(*) FILTER (WHERE s.role = 'assumption' AND NOT s.claim_closed)                        AS assumptions,
           count(*) FILTER (WHERE s.claim_closed)                                                      AS closed_links,
           bool_or(s.claim_closed AND f.first_evidence_at IS NOT NULL AND s.closed_at >= f.first_evidence_at) AS contract_changed
    FROM goal_claim_state s JOIN first_ev f USING (goal_id)
    GROUP BY s.goal_id
)
SELECT g.id AS goal_id,
       CASE WHEN COALESCE(a.contract_changed, false) THEN 'expired'
            WHEN COALESCE(a.failed, 0) > 0 THEN 'failed'
            WHEN COALESCE(a.contested, 0) > 0 THEN 'contested'
            WHEN COALESCE(a.guarantees, 0) > 0 AND a.verified = a.guarantees THEN 'verified'
            ELSE 'open' END AS status,
       COALESCE(a.guarantees, 0) AS guarantees,
       COALESCE(a.verified, 0) AS verified_claims,
       COALESCE(a.failed, 0) AS failed_claims,
       COALESCE(a.contested, 0) AS contested_claims,
       COALESCE(a.open_claims, 0) AS open_claims,
       COALESCE(a.assumptions, 0) AS assumptions,
       COALESCE(a.closed_links, 0) AS closed_links
FROM goals g
LEFT JOIN agg a ON a.goal_id = g.id;

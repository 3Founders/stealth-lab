-- 199_procedure_tool_cards.sql
--
-- Next free number: 198 (OSV policy) was highest before this file.
--
-- What this adds (docs/goal_router.md): the explicit link between a Way (procedure) and the tool
-- cards that can execute its steps. The model is Goal -> Way -> Tool, where a tool card is the
-- binding target of a step. Until now that link was implicit: `procedures.steps[i].binding` names a
-- tool and a server URL, and `tool_card_goals` maps cards to Goals, not to procedures. The old
-- procedure_implementation_bindings table was dropped in migration 59.
--
--   * procedure_id + step_order + card_id is one binding: "this card can run step N of this Way".
--     A step may have several candidate cards (a provider adapter and an MCP connector, or two
--     competing tools); a card may serve many Ways.
--   * score is the card's goal-fit for this Way's Goal (the tool_card_goals score), so a reader can
--     prefer the best card per step; source records where the binding came from.
--
-- The one-step tool procedures written by scripts/materialize_tool_procedures.py are backfilled 1:1
-- here; real multi-step procedures add one row per step. Access: application only, like 18/186.
-- Idempotent.

CREATE TABLE IF NOT EXISTS procedure_tool_cards (
    procedure_id  uuid    NOT NULL REFERENCES procedures (id) ON DELETE CASCADE,
    step_order    integer NOT NULL DEFAULT 0,
    card_id       text    NOT NULL REFERENCES tool_cards (id) ON DELETE CASCADE,
    score         real    NOT NULL DEFAULT 0,
    source        text    NOT NULL DEFAULT 'manual',      -- manual | materialized | lexical
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (procedure_id, step_order, card_id)
);

CREATE INDEX IF NOT EXISTS idx_procedure_tool_cards_card ON procedure_tool_cards (card_id);
CREATE INDEX IF NOT EXISTS idx_procedure_tool_cards_proc ON procedure_tool_cards (procedure_id, step_order);

ALTER TABLE procedure_tool_cards ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON procedure_tool_cards FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON procedure_tool_cards FROM authenticated;
    END IF;
END $$;

-- Backfill the one-step tool procedures already materialized (idempotent).
INSERT INTO procedure_tool_cards (procedure_id, step_order, card_id, score, source)
SELECT p.id,
       COALESCE((s->>'order')::int, 0),
       p.domain_payload->>'card_id',
       COALESCE((p.domain_payload->>'goal_score')::real, 0),
       'materialized'
FROM procedures p
CROSS JOIN LATERAL jsonb_array_elements(p.steps) AS s
WHERE p.provenance = 'tool_card'
  AND p.t_invalid IS NULL
  AND p.domain_payload ? 'card_id'
  AND EXISTS (SELECT 1 FROM tool_cards tc WHERE tc.id = p.domain_payload->>'card_id')
ON CONFLICT DO NOTHING;

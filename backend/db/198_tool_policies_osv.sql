-- 198_tool_policies_osv.sql
--
-- Next free number: 197 (append-only audit) was highest before this file.
--
-- What this adds: a policy row for OSV, so the security dependency/CVE subgoal has a working,
-- keyless tool while Hinoki (P1-09) is blocked. OSV is Google's public vulnerability database;
-- the card is registry:io.github.cyanheads/osv-advisory-mcp-server (live, no sign-in), and its
-- osv_query_package / osv_query_batch tools answer "which CVEs affect this package version".
--
-- R0: read-only public data. The card is pinned to the CVE subgoal by
-- scripts/map_tool_cards_to_goals.py --pin (source='manual'), which is data, not this migration.
--
-- Access: application only, like 188. Idempotent.

INSERT INTO tool_policies (server, tool, risk, effect, data_classes, max_calls, egress, note)
SELECT 'io.github.cyanheads/osv-advisory-mcp-server', NULL, 'R0', 'allow',
       ARRAY['PUBLIC_SOURCE'], 60,
       ARRAY['osv-advisory.caseyjhand.com', 'api.osv.dev'],
       'OSV public advisory lookup: dependency/CVE subgoal while Hinoki is blocked'
WHERE NOT EXISTS (
    SELECT 1 FROM tool_policies
    WHERE server = 'io.github.cyanheads/osv-advisory-mcp-server' AND tool IS NULL
);

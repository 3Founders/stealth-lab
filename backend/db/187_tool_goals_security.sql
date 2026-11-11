-- 187_tool_goals_security.sql
--
-- Next free number: 186 (tool card goals) was highest before this file.
--
-- What this adds: the security subgoals from docs/goal_router.md (P1-01). The broad `security`
-- goal stays; these are the narrower tasks a security prompt actually names:
--
--   security_dependency_cve   known CVEs in dependencies and lockfiles
--   security_secret_scan      hardcoded secrets, keys and credentials
--   security_sast             static analysis of source code
--   security_auth_review      authn/authz, privilege escalation, access control
--   security_pentest          exploitable vulnerabilities, findings
--
-- The ids are deterministic UUIDv5 values in the same namespace as the 25 tool goals
-- (scripts/map_tool_cards_to_goals.py, `kell:tool-goal:<id>`), so a card pin or a manual mapping
-- can refer to them without a lookup. They live on K000 like the other tool-goal taxonomy Goals.
--
-- Idempotent. No FK, no card mapping here: mapping is P1-03/P1-09.

INSERT INTO goals (id, canonical_name, description, status, provenance, created_from,
                   scope_type, visibility, home_shard_id, version)
VALUES
    ('c1aedb7a-fc53-50a3-ae21-3cfdeb756c52',
     'Find known CVEs in this repository''s dependencies',
     'find known vulnerabilities (CVEs) in this repository''s dependencies and lockfiles',
     'active', 'tool_goal_taxonomy', 'tool_goals', 'global', 'public', 'K000', 1),
    ('fb665d0d-8fdf-50e3-9a70-09a92ca5158d',
     'Find hardcoded secrets and credentials in a repository',
     'find hardcoded secrets, keys, tokens and credentials in a repository or diff',
     'active', 'tool_goal_taxonomy', 'tool_goals', 'global', 'public', 'K000', 1),
    ('f1303d86-9c03-5690-bc2d-db80de10025d',
     'Statically analyze code for security weaknesses',
     'statically analyze source code for security weaknesses without running it',
     'active', 'tool_goal_taxonomy', 'tool_goals', 'global', 'public', 'K000', 1),
    ('a117c7e2-3785-50d4-a972-d9a1873b299f',
     'Review authentication and authorization for privilege escalation',
     'review authentication and authorization for privilege escalation and broken access control',
     'active', 'tool_goal_taxonomy', 'tool_goals', 'global', 'public', 'K000', 1),
    ('ee43326e-a0d2-5a0d-90a4-27cc879ecc8b',
     'Test a repository or application for exploitable vulnerabilities',
     'test a repository or application for exploitable vulnerabilities and produce findings',
     'active', 'tool_goal_taxonomy', 'tool_goals', 'global', 'public', 'K000', 1)
ON CONFLICT (id) DO UPDATE
    SET canonical_name = EXCLUDED.canonical_name,
        description = EXCLUDED.description,
        status = 'active',
        t_invalid = NULL;

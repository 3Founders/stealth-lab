-- 207_hinoki_egress.sql
--
-- P1-09: Hinoki is reached at its inference API (inference.hinoki.security), not the api.hinoki.security that
-- db/188 guessed, and scan_repository reads the repository from GitHub first. The policy's egress list now names the
-- hosts the adapter (app/tool_providers/hinoki) actually calls. Only the seeded row is changed: an edited one is kept.
UPDATE tool_policies
   SET egress = ARRAY['inference.hinoki.security', 'api.github.com', 'raw.githubusercontent.com']
 WHERE server = 'hinoki' AND tool IS NULL AND egress = ARRAY['api.hinoki.security'];

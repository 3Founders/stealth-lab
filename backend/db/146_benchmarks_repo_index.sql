-- find_ways' same-repo lookup (app.services.library_context.same_repo_goal_ids): the Goals of public benchmarks
-- of one repository, by `lower(environment_specification ->> 'repo')` (= owner/name, e.g. django/django).
-- Without this index the lookup scans every benchmark row on each find_ways call that sends a public repo name.
-- Read-only use; additive; safe to re-run.
CREATE INDEX IF NOT EXISTS idx_benchmarks_repo_lower
    ON benchmarks (lower(environment_specification ->> 'repo'))
    WHERE environment_specification ? 'repo';

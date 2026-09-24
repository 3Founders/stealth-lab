-- Migration 112. Next free number: 113.
-- Repairs the frozen-benchmark immutability function after the Problem-to-Goal retarget.

CREATE OR REPLACE FUNCTION assert_benchmark_frozen_immutable()
RETURNS trigger AS $$
BEGIN
    IF OLD.frozen_at IS NOT NULL THEN
        IF NEW.version                IS DISTINCT FROM OLD.version
           OR NEW.name                 IS DISTINCT FROM OLD.name
           OR NEW.evaluation_protocol  IS DISTINCT FROM OLD.evaluation_protocol
           OR NEW.environment_specification IS DISTINCT FROM OLD.environment_specification
           OR NEW.success_criteria     IS DISTINCT FROM OLD.success_criteria
           OR NEW.comparison_policy     IS DISTINCT FROM OLD.comparison_policy
           OR NEW.goal_id               IS DISTINCT FROM OLD.goal_id
           OR NEW.frozen_at             IS DISTINCT FROM OLD.frozen_at THEN
            RAISE EXCEPTION 'benchmark % is frozen (frozen_at=%); its measured meaning is immutable -- create a new version row instead',
                OLD.id, OLD.frozen_at;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

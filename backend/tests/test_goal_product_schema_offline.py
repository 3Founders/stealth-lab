from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "db" / "112_v1_goal_product_repairs.sql"


def test_goal_product_repair_migration_replaces_the_frozen_benchmark_function_for_goals():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.startswith("-- Migration 112")
    assert "Next free number: 113" in sql
    assert "CREATE OR REPLACE FUNCTION assert_benchmark_frozen_immutable()" in sql
    assert "NEW.goal_id               IS DISTINCT FROM OLD.goal_id" in sql
    assert "problem_id" not in sql
    assert "idx_goals_resolved_at" not in sql


def test_goal_product_schema_has_resolution_and_user_payload_columns():
    goals_schema = (Path(__file__).parents[1] / "db" / "83_goals.sql").read_text(encoding="utf-8")
    problem_merge = (Path(__file__).parents[1] / "db" / "110_merge_problems_into_goals.sql").read_text(encoding="utf-8")
    assert "expected_outcome" in goals_schema
    assert "metadata" in problem_merge
    assert "resolved_at" in problem_merge

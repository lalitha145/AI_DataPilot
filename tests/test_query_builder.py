"""Tests for deterministic SQL generation and safety-related construction."""

from __future__ import annotations

from core.query_builder import build_sql
from core.schema import AnalysisPlan, FilterSpec, JoinSpec, MetricSpec, SortSpec, VisualizationSpec


def test_grouped_average_sql() -> None:
    plan = AnalysisPlan(
        intent="aggregation",
        tables=["performance"],
        group_by=["performance.department"],
        metric=MetricSpec(column="performance.performance_score", aggregation="avg"),
        sort=SortSpec(column="avg_performance_score", direction="desc"),
        visualization=VisualizationSpec(needed=True, type="bar"),
    )
    sql = build_sql(plan)
    assert "AVG(" in sql.upper()
    assert "GROUP BY" in sql.upper()
    assert sql.strip().upper().startswith("SELECT")


def test_cross_file_join_sql() -> None:
    plan = AnalysisPlan(
        intent="comparison",
        tables=["employees", "attendance"],
        joins=[
            JoinSpec(
                left_table="employees",
                left_column="employee_id",
                right_table="attendance",
                right_column="employee_id",
            )
        ],
        group_by=["employees.department"],
        metric=MetricSpec(
            column="attendance.present_days",
            aggregation="sum",
            ratio_over="attendance.working_days",
        ),
        filters=[FilterSpec(column="employees.location", operator="eq", value="Bangalore")],
    )
    sql = build_sql(plan)
    assert "INNER JOIN" in sql
    assert "present_days" in sql
    assert "Bangalore" in sql


def test_measures_are_rounded_to_two_decimals() -> None:
    plan = AnalysisPlan(
        intent="aggregation",
        tables=["performance"],
        group_by=["performance.department"],
        metric=MetricSpec(column="performance.performance_score", aggregation="avg"),
    )
    sql = build_sql(plan)
    assert "ROUND(" in sql.upper()
    assert ", 2)" in sql


def test_count_is_not_rounded() -> None:
    plan = AnalysisPlan(
        intent="ranking",
        tables=["employees"],
        group_by=["employees.location"],
        metric=MetricSpec(column="employees.employee_id", aggregation="count"),
    )
    sql = build_sql(plan)
    assert "COUNT(" in sql.upper()
    assert "ROUND(" not in sql.upper()


def test_limit_is_integer() -> None:
    plan = AnalysisPlan(
        intent="ranking",
        tables=["employees"],
        metric=MetricSpec(column="employees.employee_id", aggregation="count"),
        group_by=["employees.location"],
        limit=5,
    )
    sql = build_sql(plan)
    assert sql.strip().endswith("LIMIT 5")

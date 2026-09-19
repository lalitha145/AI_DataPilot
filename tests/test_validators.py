"""Tests for plan validation against catalog metadata."""

from __future__ import annotations

import pytest

from core.schema import AnalysisPlan, Catalog, ColumnMeta, JoinSpec, MetricSpec, TableMeta
from core.validators import (
    PlanValidationError,
    detect_ambiguity,
    detect_out_of_scope,
    validate_plan,
)


def _catalog() -> Catalog:
    return Catalog(
        tables=[
            TableMeta(
                file_name="employees.csv",
                table_name="employees",
                row_count=10,
                column_count=3,
                columns=[
                    ColumnMeta(name="employee_id", dtype="int64"),
                    ColumnMeta(name="department", dtype="object"),
                    ColumnMeta(name="location", dtype="object"),
                ],
            ),
            TableMeta(
                file_name="performance.csv",
                table_name="performance",
                row_count=10,
                column_count=2,
                columns=[
                    ColumnMeta(name="employee_id", dtype="int64"),
                    ColumnMeta(name="performance_score", dtype="float64"),
                ],
            ),
        ]
    )


def test_valid_plan_passes() -> None:
    plan = AnalysisPlan(
        intent="aggregation",
        tables=["performance", "employees"],
        joins=[
            JoinSpec(
                left_table="employees",
                left_column="employee_id",
                right_table="performance",
                right_column="employee_id",
            )
        ],
        group_by=["department"],
        metric=MetricSpec(column="performance_score", aggregation="avg"),
    )
    validated = validate_plan(plan, _catalog())
    assert "employees.department" in validated.group_by[0]


def test_nonexistent_table() -> None:
    plan = AnalysisPlan(
        intent="lookup",
        tables=["payroll"],
        metric=MetricSpec(column="salary", aggregation="avg"),
    )
    with pytest.raises(PlanValidationError):
        validate_plan(plan, _catalog())


def test_nonexistent_column() -> None:
    plan = AnalysisPlan(
        intent="aggregation",
        tables=["employees"],
        metric=MetricSpec(column="salary", aggregation="avg"),
    )
    with pytest.raises(PlanValidationError):
        validate_plan(plan, _catalog())


def test_invalid_join_column() -> None:
    plan = AnalysisPlan(
        intent="aggregation",
        tables=["employees", "performance"],
        joins=[
            JoinSpec(
                left_table="employees",
                left_column="department",
                right_table="performance",
                right_column="performance_score",
            )
        ],
        metric=MetricSpec(column="performance_score", aggregation="avg"),
    )
    with pytest.raises(PlanValidationError):
        validate_plan(plan, _catalog())


def test_semantic_column_match() -> None:
    catalog = Catalog(
        tables=[
            TableMeta(
                file_name="sales.csv",
                table_name="sales",
                row_count=5,
                column_count=1,
                columns=[ColumnMeta(name="sales_region", dtype="object")],
            )
        ]
    )
    plan = AnalysisPlan(
        intent="distribution",
        tables=["sales"],
        group_by=["region"],
        metric=MetricSpec(column="sales_region", aggregation="count"),
    )
    validated = validate_plan(plan, catalog)
    assert validated.group_by == ["sales.sales_region"]


def _sales_catalog() -> Catalog:
    return Catalog(
        tables=[
            TableMeta(
                file_name="sales.csv",
                table_name="sales",
                row_count=5,
                column_count=3,
                columns=[
                    ColumnMeta(name="sales_amount", dtype="float64"),
                    ColumnMeta(name="units_sold", dtype="int64"),
                    ColumnMeta(name="sales_target", dtype="float64"),
                ],
            )
        ]
    )


def test_ambiguity_for_generic_sales() -> None:
    ambiguous, question, candidates = detect_ambiguity(
        "What is the average sales?", _sales_catalog()
    )
    assert ambiguous
    assert "sales" in question.lower()
    assert len(candidates) >= 2


def test_no_ambiguity_when_column_is_named() -> None:
    ambiguous, _, _ = detect_ambiguity(
        "What is the average sales amount?", _sales_catalog()
    )
    assert not ambiguous


def test_no_ambiguity_for_unique_metric() -> None:
    ambiguous, _, _ = detect_ambiguity(
        "Which department has the highest performance score?", _catalog()
    )
    assert not ambiguous


def test_out_of_scope_question_detected() -> None:
    assert detect_out_of_scope("What is the weather in Paris today?", _catalog())
    assert detect_out_of_scope("Write me a poem about the ocean", _catalog())


def test_in_scope_questions_not_rejected() -> None:
    catalog = _catalog()
    assert not detect_out_of_scope("Average performance score by department", catalog)
    assert not detect_out_of_scope("How many employees are in each location?", catalog)
    assert not detect_out_of_scope("How many rows are there?", catalog)


def test_out_of_scope_matches_categorical_values() -> None:
    catalog = Catalog(
        tables=[
            TableMeta(
                file_name="employees.csv",
                table_name="employees",
                row_count=3,
                column_count=1,
                columns=[
                    ColumnMeta(
                        name="location",
                        dtype="object",
                        distinct_values=["Bangalore", "Hyderabad"],
                    )
                ],
            )
        ]
    )
    assert not detect_out_of_scope("Compare Bangalore and Hyderabad", catalog)

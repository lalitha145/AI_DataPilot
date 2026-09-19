"""End-to-end DuckDB analyses on sample HR files, without the LLM."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.executor import connect_and_register, execute_query
from core.query_builder import build_sql
from core.schema import (
    AnalysisPlan,
    Catalog,
    FilterSpec,
    JoinSpec,
    MetricSpec,
    SortSpec,
    VisualizationSpec,
)
from core.validators import detect_ambiguity, detect_out_of_scope, validate_plan
from core.visualization import build_chart
from utils.file_loader import extract_table_meta, read_tabular_file

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data"


def _load_sample():
    employees = read_tabular_file("employees.csv", (SAMPLE / "employees.csv").read_bytes())
    attendance = read_tabular_file("attendance.xlsx", (SAMPLE / "attendance.xlsx").read_bytes())
    performance = read_tabular_file("performance.csv", (SAMPLE / "performance.csv").read_bytes())
    tables = {
        "employees": employees,
        "attendance": attendance,
        "performance": performance,
    }
    catalog = Catalog(
        tables=[
            extract_table_meta("employees.csv", "employees", employees),
            extract_table_meta("attendance.xlsx", "attendance", attendance),
            extract_table_meta("performance.csv", "performance", performance),
        ]
    )
    return tables, catalog


def _run(plan: AnalysisPlan, tables, catalog) -> pd.DataFrame:
    validated = validate_plan(plan, catalog)
    sql = build_sql(validated)
    connection = connect_and_register(tables)
    try:
        return execute_query(connection, sql), validated
    finally:
        connection.close()


def test_highest_average_performance_by_department() -> None:
    tables, catalog = _load_sample()
    plan = AnalysisPlan(
        intent="ranking",
        tables=["employees", "performance"],
        joins=[
            JoinSpec(
                left_table="employees",
                left_column="employee_id",
                right_table="performance",
                right_column="employee_id",
            )
        ],
        group_by=["employees.department"],
        metric=MetricSpec(column="performance.performance_score", aggregation="avg"),
        sort=SortSpec(direction="desc"),
        visualization=VisualizationSpec(needed=True, type="bar", x="department"),
        limit=10,
    )
    frame, validated = _run(plan, tables, catalog)
    assert not frame.empty
    assert frame.iloc[0]["department"] == "Engineering"
    assert build_chart(frame, validated) is not None


def test_average_attendance_by_department() -> None:
    tables, catalog = _load_sample()
    plan = AnalysisPlan(
        intent="aggregation",
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
        visualization=VisualizationSpec(needed=True, type="bar"),
        limit=10,
    )
    frame, _ = _run(plan, tables, catalog)
    assert len(frame) >= 3


def test_location_headcount() -> None:
    tables, catalog = _load_sample()
    plan = AnalysisPlan(
        intent="ranking",
        tables=["employees"],
        group_by=["employees.location"],
        metric=MetricSpec(column="employees.employee_id", aggregation="count"),
        sort=SortSpec(direction="desc"),
        visualization=VisualizationSpec(needed=True, type="bar"),
        limit=10,
    )
    frame, _ = _run(plan, tables, catalog)
    assert frame.iloc[0]["location"] == "Bangalore"


def test_performance_trend() -> None:
    tables, catalog = _load_sample()
    plan = AnalysisPlan(
        intent="trend",
        tables=["performance"],
        group_by=["performance.review_period"],
        metric=MetricSpec(column="performance.performance_score", aggregation="avg"),
        sort=SortSpec(column="performance.review_period", direction="asc"),
        visualization=VisualizationSpec(needed=True, type="line", x="review_period"),
        limit=20,
    )
    frame, validated = _run(plan, tables, catalog)
    assert len(frame) == 4
    assert build_chart(frame, validated) is not None


def test_attendance_bangalore_vs_hyderabad() -> None:
    tables, catalog = _load_sample()
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
        filters=[
            FilterSpec(
                column="employees.location",
                operator="in",
                value=["Bangalore", "Hyderabad"],
            )
        ],
        group_by=["employees.location"],
        metric=MetricSpec(
            column="attendance.present_days",
            aggregation="sum",
            ratio_over="attendance.working_days",
        ),
        visualization=VisualizationSpec(needed=True, type="bar"),
        limit=10,
    )
    frame, _ = _run(plan, tables, catalog)
    assert set(frame["location"]) == {"Bangalore", "Hyderabad"}


def test_csv_and_excel_together() -> None:
    tables, catalog = _load_sample()
    assert set(tables) == {"employees", "attendance", "performance"}
    assert catalog.get_table("attendance").file_name.endswith(".xlsx")


def test_results_are_rounded_to_two_decimals() -> None:
    tables, catalog = _load_sample()
    plan = AnalysisPlan(
        intent="ranking",
        tables=["employees", "performance"],
        joins=[
            JoinSpec(
                left_table="employees",
                left_column="employee_id",
                right_table="performance",
                right_column="employee_id",
            )
        ],
        group_by=["employees.department"],
        metric=MetricSpec(column="performance.performance_score", aggregation="avg"),
        limit=10,
    )
    frame, _ = _run(plan, tables, catalog)
    values = frame.select_dtypes("number").iloc[:, 0]
    assert all(round(float(value), 2) == float(value) for value in values)


def test_out_of_scope_question_on_sample_data() -> None:
    _, catalog = _load_sample()
    assert detect_out_of_scope("Who won the football world cup?", catalog)
    assert not detect_out_of_scope("Compare attendance between Bangalore and Hyderabad", catalog)


def test_specific_performance_question_is_not_ambiguous() -> None:
    _, catalog = _load_sample()
    ambiguous, _, _ = detect_ambiguity(
        "Which department has the highest average performance score?", catalog
    )
    assert not ambiguous

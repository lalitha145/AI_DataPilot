"""Tests for DuckDB execution, SQL safety, empty results, and formatting."""

from __future__ import annotations

import pandas as pd
import pytest

from core.executor import SQLSafetyError, assert_readonly_select, connect_and_register, execute_query
from core.planner import _fallback_explanation
from core.query_builder import build_sql
from core.schema import AnalysisPlan, JoinSpec, MetricSpec, SortSpec
from core.validators import validate_plan
from utils.file_loader import extract_table_meta
from core.schema import Catalog


def test_dangerous_sql_rejected() -> None:
    with pytest.raises(SQLSafetyError):
        assert_readonly_select("DROP TABLE employees")
    with pytest.raises(SQLSafetyError):
        assert_readonly_select("SELECT 1; DELETE FROM employees")
    with pytest.raises(SQLSafetyError):
        assert_readonly_select("INSERT INTO employees VALUES (1)")
    with pytest.raises(SQLSafetyError):
        assert_readonly_select("COPY employees TO 'out.csv'")


def test_valid_cross_file_query() -> None:
    employees = pd.DataFrame(
        {
            "employee_id": [1, 2, 3],
            "department": ["Engineering", "Sales", "Engineering"],
            "location": ["Bangalore", "Hyderabad", "Bangalore"],
        }
    )
    performance = pd.DataFrame(
        {
            "employee_id": [1, 2, 3],
            "performance_score": [4.5, 3.2, 4.1],
        }
    )
    tables = {"employees": employees, "performance": performance}
    catalog = Catalog(
        tables=[
            extract_table_meta("employees.csv", "employees", employees),
            extract_table_meta("performance.csv", "performance", performance),
        ]
    )
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
        limit=10,
    )
    validated = validate_plan(plan, catalog)
    sql = build_sql(validated)
    connection = connect_and_register(tables)
    result = execute_query(connection, sql)
    connection.close()
    assert not result.empty
    assert result.iloc[0]["department"] == "Engineering"
    assert abs(float(result.loc[result["department"] == "Engineering"].iloc[0, 1]) - 4.3) < 1e-6


def test_empty_result() -> None:
    frame = pd.DataFrame({"employee_id": [1], "department": ["HR"]})
    connection = connect_and_register({"employees": frame})
    result = execute_query(
        connection,
        "SELECT * FROM employees WHERE department = 'Moon'",
    )
    connection.close()
    assert result.empty


def test_invalid_aggregation_rejected_by_schema() -> None:
    with pytest.raises(Exception):
        MetricSpec(column="score", aggregation="median")  # type: ignore[arg-type]


def test_result_formatting_fallback() -> None:
    frame = pd.DataFrame({"value": [42]})
    plan = AnalysisPlan(intent="aggregation", tables=["t"], metric=MetricSpec(aggregation="sum", column="t.value"))
    text = _fallback_explanation(frame, plan)
    assert "42" in text

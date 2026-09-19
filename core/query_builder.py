"""Deterministic read-only SQL generation from a validated AnalysisPlan."""

from __future__ import annotations

from typing import Any

from core.schema import AnalysisPlan, FilterSpec, MetricSpec
from utils.helpers import qualify, quote_ident

DECIMAL_PLACES = 2


class SQLBuildError(Exception):
    def __init__(self, message: str, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or "I couldn't build a query for that question."


def _split_ref(ref: str) -> tuple[str, str]:
    if "." not in ref:
        raise SQLBuildError(f"Unqualified column {ref}")
    table, column = ref.split(".", 1)
    return table, column


def _col_sql(ref: str) -> str:
    if ref == "*":
        return "*"
    table, column = _split_ref(ref)
    return qualify(table, column)


def _alias_for(metric: MetricSpec) -> str:
    if metric.alias:
        return metric.alias
    if metric.column == "*":
        return "row_count"
    _, column = _split_ref(metric.column) if "." in metric.column else ("", metric.column)
    if metric.ratio_over:
        return f"{metric.aggregation or 'ratio'}_{column}_rate"
    if metric.aggregation == "none":
        return column
    return f"{metric.aggregation}_{column}"


def _rounded(expression: str) -> str:
    """Round computed measures to two decimals inside DuckDB."""
    return f"ROUND(CAST({expression} AS DOUBLE), {DECIMAL_PLACES})"


def _metric_expr(metric: MetricSpec) -> str:
    if metric.ratio_over:
        numerator = _col_sql(metric.column)
        denominator = _col_sql(metric.ratio_over)
        ratio = f"({numerator} * 1.0) / NULLIF({denominator}, 0)"
        mapping = {
            "avg": _rounded(f"AVG({ratio})"),
            "sum": _rounded(f"SUM({numerator}) * 1.0 / NULLIF(SUM({denominator}), 0)"),
            "min": _rounded(f"MIN({ratio})"),
            "max": _rounded(f"MAX({ratio})"),
            "count": f"COUNT({numerator})",
            "none": _rounded(ratio),
        }
        return mapping[metric.aggregation]
    if metric.aggregation == "none":
        return _col_sql(metric.column) if metric.column != "*" else "*"
    if metric.column == "*" or metric.aggregation == "count":
        target = "*" if metric.column in {"", "*"} else _col_sql(metric.column)
        if metric.aggregation == "count":
            return f"COUNT({target})"
    expr = _col_sql(metric.column)
    return {
        "sum": _rounded(f"SUM({expr})"),
        "avg": _rounded(f"AVG({expr})"),
        "min": _rounded(f"MIN({expr})"),
        "max": _rounded(f"MAX({expr})"),
        "count": f"COUNT({expr})",
        "none": expr,
    }[metric.aggregation]


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _filter_sql(spec: FilterSpec) -> str:
    column = _col_sql(spec.column)
    op = spec.operator
    if op == "eq":
        return f"{column} = {_sql_literal(spec.value)}"
    if op == "ne":
        return f"{column} <> {_sql_literal(spec.value)}"
    if op == "gt":
        return f"{column} > {_sql_literal(spec.value)}"
    if op == "gte":
        return f"{column} >= {_sql_literal(spec.value)}"
    if op == "lt":
        return f"{column} < {_sql_literal(spec.value)}"
    if op == "lte":
        return f"{column} <= {_sql_literal(spec.value)}"
    if op == "like":
        return f"CAST({column} AS VARCHAR) ILIKE {_sql_literal(spec.value)}"
    if op == "in":
        values = spec.value if isinstance(spec.value, list) else [spec.value]
        rendered = ", ".join(_sql_literal(item) for item in values)
        return f"{column} IN ({rendered})"
    if op == "between":
        low, high = spec.value
        return f"{column} BETWEEN {_sql_literal(low)} AND {_sql_literal(high)}"
    raise SQLBuildError(f"Unsupported operator {op}")


def build_sql(plan: AnalysisPlan) -> str:
    """
    Build a read-only DuckDB query from a validated analysis plan.
    """
    if not plan.tables:
        raise SQLBuildError("Plan has no tables")

    base = quote_ident(plan.tables[0])
    from_sql = [f"FROM {base}"]
    seen = {plan.tables[0]}
    for join in plan.joins:
        right = join.right_table if join.right_table not in seen else join.left_table
        left = join.left_table if right == join.right_table else join.right_table
        if right in seen and left in seen:
            continue
        join_kw = "LEFT JOIN" if join.how == "left" else "INNER JOIN"
        from_sql.append(
            f"{join_kw} {quote_ident(right)} ON "
            f"{qualify(join.left_table, join.left_column)} = "
            f"{qualify(join.right_table, join.right_column)}"
        )
        seen.add(right)

    metrics = [plan.metric, *plan.extra_metrics]
    grouped = bool(plan.group_by) or any(m.aggregation != "none" for m in metrics)
    select_parts: list[str] = []
    for group in plan.group_by:
        table, column = _split_ref(group)
        select_parts.append(f"{qualify(table, column)} AS {quote_ident(column)}")

    for metric in metrics:
        if metric.aggregation == "none" and not metric.column:
            continue
        alias = quote_ident(_alias_for(metric))
        if grouped or metric.aggregation != "none":
            select_parts.append(f"{_metric_expr(metric)} AS {alias}")
        elif metric.column != "*":
            select_parts.append(f"{_col_sql(metric.column)} AS {alias}")

    if not select_parts:
        select_parts = ["*"]

    sql = f"SELECT {', '.join(select_parts)}\n" + "\n".join(from_sql)

    if plan.filters:
        sql += "\nWHERE " + " AND ".join(_filter_sql(item) for item in plan.filters)

    if plan.group_by and grouped:
        sql += "\nGROUP BY " + ", ".join(_col_sql(group) for group in plan.group_by)

    sort_column = plan.sort.column
    direction = "DESC" if plan.sort.direction == "desc" else "ASC"
    if sort_column:
        if "." in sort_column and sort_column not in {
            _alias_for(metric) for metric in metrics
        }:
            # Prefer metric alias when sorting by the measured field.
            metric_aliases = [_alias_for(metric) for metric in metrics]
            _, raw_name = _split_ref(sort_column)
            if raw_name in metric_aliases:
                order_expr = quote_ident(raw_name)
            elif any(raw_name in _alias_for(metric) or _alias_for(metric).endswith(raw_name) for metric in metrics):
                order_expr = quote_ident(metric_aliases[0])
            elif plan.group_by and any(_split_ref(g)[1] == raw_name for g in plan.group_by):
                order_expr = _col_sql(sort_column)
            else:
                order_expr = quote_ident(metric_aliases[0]) if metric_aliases and grouped else _col_sql(sort_column)
        else:
            order_expr = quote_ident(sort_column) if "." not in sort_column else _col_sql(sort_column)
        sql += f"\nORDER BY {order_expr} {direction} NULLS LAST"
    elif grouped and metrics:
        sql += f"\nORDER BY {quote_ident(_alias_for(metrics[0]))} {direction} NULLS LAST"

    sql += f"\nLIMIT {int(plan.limit)}"
    return sql

"""Question-driven Plotly charts from DuckDB results."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.graph_objs import Figure

from core.schema import AnalysisPlan

BLUE = "#0E7C6B"
PALETTE = ["#0E7C6B", "#2A9D8F", "#5EBBB0", "#0A5C50", "#7C9A95"]


def _clean_name(value: str) -> str:
    return value.replace("_", " ").strip()


def build_chart(frame: pd.DataFrame, plan: AnalysisPlan) -> Figure | None:
    """Return a Plotly figure only when a chart is useful."""
    if frame is None or frame.empty:
        return None
    viz = plan.visualization
    if not viz.needed or viz.type in {"none", "table"}:
        if plan.intent in {"lookup", "filtering"} and len(frame) <= 1:
            return None
        if plan.intent in {"aggregation"} and len(frame) == 1 and len(frame.columns) <= 2:
            return None
        if not viz.needed:
            return None

    chart_type = viz.type
    if chart_type in {"none", "table"}:
        return None

    x_col = _pick_x(frame, plan)
    y_col = _pick_y(frame, plan, x_col)
    if x_col is None or y_col is None:
        return None
    if x_col == y_col and chart_type != "pie":
        return None

    template = "plotly_white"
    if chart_type == "bar":
        fig = px.bar(
            frame,
            x=x_col,
            y=y_col,
            color_discrete_sequence=[BLUE],
            template=template,
        )
    elif chart_type == "line":
        fig = px.line(
            frame,
            x=x_col,
            y=y_col,
            markers=True,
            color_discrete_sequence=[BLUE],
            template=template,
        )
    elif chart_type == "pie":
        fig = px.pie(
            frame,
            names=x_col,
            values=y_col,
            color_discrete_sequence=PALETTE,
            hole=0.45,
        )
        fig.update_layout(template=template)
    elif chart_type == "scatter":
        extra = [c for c in frame.columns if c not in {x_col, y_col}]
        fig = px.scatter(
            frame,
            x=x_col,
            y=y_col,
            color=extra[0] if extra else None,
            color_discrete_sequence=PALETTE,
            template=template,
        )
    else:
        return None

    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=360,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis_title=_clean_name(str(x_col)),
        yaxis_title=_clean_name(str(y_col)),
        legend_title="",
        font=dict(family="DM Sans, sans-serif", color="#0F1C24"),
    )
    return fig


def kpi_figure(label: str, value: str) -> Figure:
    fig = go.Figure(
        go.Indicator(
            mode="number",
            value=None,
            title={"text": label},
        )
    )
    fig.update_layout(height=120)
    return fig


def _pick_x(frame: pd.DataFrame, plan: AnalysisPlan) -> str | None:
    if plan.visualization.x:
        short = plan.visualization.x.split(".")[-1]
        if short in frame.columns:
            return short
        if plan.visualization.x in frame.columns:
            return plan.visualization.x
    if plan.group_by:
        name = plan.group_by[0].split(".")[-1]
        if name in frame.columns:
            return name
    non_numeric = [c for c in frame.columns if not pd.api.types.is_numeric_dtype(frame[c])]
    if non_numeric:
        return non_numeric[0]
    return frame.columns[0] if len(frame.columns) else None


def _pick_y(frame: pd.DataFrame, plan: AnalysisPlan, x_col: str | None) -> str | None:
    if plan.visualization.y:
        short = plan.visualization.y.split(".")[-1]
        if short in frame.columns:
            return short
        if plan.visualization.y in frame.columns:
            return plan.visualization.y
    numeric = [
        c
        for c in frame.columns
        if c != x_col and pd.api.types.is_numeric_dtype(frame[c])
    ]
    if numeric:
        return numeric[0]
    remaining = [c for c in frame.columns if c != x_col]
    return remaining[0] if remaining else None

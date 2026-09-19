"""Pydantic models for schema metadata and analysis plans."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Intent = Literal[
    "aggregation",
    "comparison",
    "trend",
    "filtering",
    "ranking",
    "distribution",
    "lookup",
]
Aggregation = Literal["sum", "avg", "count", "min", "max", "none"]
SortDirection = Literal["asc", "desc"]
ChartType = Literal["bar", "line", "pie", "scatter", "table", "none"]
FilterOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "like", "between"]
JoinType = Literal["inner", "left"]


class ColumnMeta(BaseModel):
    name: str
    dtype: str
    null_count: int = 0
    sample_values: list[Any] = Field(default_factory=list)
    distinct_values: list[str] = Field(default_factory=list)

    def is_numeric(self) -> bool:
        return self.dtype.lower().startswith(("int", "float", "uint", "decimal"))


class TableMeta(BaseModel):
    file_name: str
    table_name: str
    row_count: int
    column_count: int
    columns: list[ColumnMeta]
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)

    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    def get_column(self, name: str) -> ColumnMeta | None:
        lowered = name.lower()
        for column in self.columns:
            if column.name.lower() == lowered:
                return column
        return None


class Catalog(BaseModel):
    tables: list[TableMeta] = Field(default_factory=list)

    def table_names(self) -> list[str]:
        return [table.table_name for table in self.tables]

    def get_table(self, name: str) -> TableMeta | None:
        lowered = name.lower()
        for table in self.tables:
            if table.table_name.lower() == lowered:
                return table
        return None

    def file_for_table(self, table_name: str) -> str | None:
        table = self.get_table(table_name)
        return table.file_name if table else None


class JoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    how: JoinType = "inner"


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    operator: FilterOperator = "eq"
    value: Any = None


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = ""
    aggregation: Aggregation = "none"
    alias: str = ""
    ratio_over: str = ""


class SortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = ""
    direction: SortDirection = "desc"


class VisualizationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needed: bool = False
    type: ChartType = "none"
    x: str = ""
    y: str = ""


class AnalysisPlan(BaseModel):
    """Structured, constrained analysis plan produced by the LLM."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    tables: list[str] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    metric: MetricSpec = Field(default_factory=MetricSpec)
    extra_metrics: list[MetricSpec] = Field(default_factory=list)
    sort: SortSpec = Field(default_factory=SortSpec)
    limit: int = 10
    visualization: VisualizationSpec = Field(default_factory=VisualizationSpec)
    ambiguity: bool = False
    clarification_question: str = ""
    candidate_columns: list[str] = Field(default_factory=list)
    out_of_scope: bool = False

    @field_validator("limit")
    @classmethod
    def limit_bounds(cls, value: int) -> int:
        if value < 1:
            return 1
        if value > 200:
            return 200
        return value


class Provenance(BaseModel):
    files: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    calculation: str = ""
    rows_analyzed: int = 0
    sql: str = ""


class StepLog(BaseModel):
    """One timed stage of the pipeline, for the 'what happened' trace in the UI."""

    label: str
    seconds: float
    ok: bool = True


class AnalysisResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    question: str
    explanation: str
    plan: AnalysisPlan | None = None
    provenance: Provenance | None = None
    error: str | None = None
    needs_clarification: bool = False
    clarification_question: str = ""
    candidate_columns: list[str] = Field(default_factory=list)
    out_of_scope: bool = False
    steps: list[StepLog] = Field(default_factory=list)


FALLBACK_EXAMPLE_QUESTIONS = [
    "What is the average value by category?",
    "Which row has the highest value?",
    "Show me a summary of this data.",
]


def _looks_like_date(dtype: str, name: str) -> bool:
    dtype_l = dtype.lower()
    if "datetime" in dtype_l or "date" in dtype_l:
        return True
    return bool(re.search(r"date|_dt$|_at$|timestamp", name, re.IGNORECASE))


def _looks_like_identifier(name: str) -> bool:
    """
    True for columns that are numeric IDs/keys rather than measures — e.g.
    order_id, customer_id, sku, uuid. Averaging or comparing an ID column
    produces a nonsensical example question even though it's numeric.
    """
    return bool(re.search(r"(^|_)(id|ids|code|sku|uuid|guid|key)$", name, re.IGNORECASE))


def generate_example_questions(catalog: Catalog, limit: int = 5) -> list[str]:
    """
    Build example questions from whatever schema was actually uploaded,
    instead of a fixed list tied to one sample dataset. Used by both the
    UI's example buttons and the greeting message, so a demo on any file —
    not just the bundled sample data — always shows relevant, real examples.

    Grouping columns are chosen from `distinct_values`, which the loader
    only populates for genuinely low-cardinality columns (see
    `_low_cardinality_values` in utils/file_loader.py) — this avoids picking
    a high-cardinality free-text column like `name` or `email` to group by,
    which would produce a degenerate "one row per group" example.
    """
    if not catalog.tables:
        return []

    questions: list[str] = []
    for table in catalog.tables:
        date_cols = [c.name for c in table.columns if _looks_like_date(c.dtype, c.name)]
        numeric_cols = [
            c.name
            for c in table.columns
            if c.is_numeric() and not _looks_like_identifier(c.name)
        ]
        # Prefer real categorical dimensions (low cardinality); fall back to
        # any non-numeric, non-date, non-identifier column if none exist.
        categorical_cols = [c.name for c in table.columns if c.distinct_values] or [
            c.name
            for c in table.columns
            if not c.is_numeric() and c.name not in date_cols and not _looks_like_identifier(c.name)
        ]

        if numeric_cols and categorical_cols:
            questions.append(f"What is the average {numeric_cols[0]} by {categorical_cols[0]}?")
            second_metric = numeric_cols[1] if len(numeric_cols) > 1 else numeric_cols[0]
            second_cat = categorical_cols[1] if len(categorical_cols) > 1 else categorical_cols[0]
            questions.append(f"Which {second_cat} has the highest total {second_metric}?")
        if numeric_cols and date_cols:
            questions.append(f"How did {numeric_cols[0]} change over time?")
        if len(numeric_cols) >= 2:
            questions.append(f"Compare {numeric_cols[0]} and {numeric_cols[1]}.")

        if len(questions) >= limit - 1:
            break

    if len(catalog.tables) > 1:
        first_table, second_table = catalog.tables[0], catalog.tables[1]
        shared_metric = next(
            (c.name for c in first_table.columns if c.is_numeric() and not _looks_like_identifier(c.name)),
            None,
        )
        if shared_metric:
            questions.append(
                f"Combine {first_table.table_name} and {second_table.table_name} "
                f"to analyze {shared_metric}."
            )

    seen: set[str] = set()
    deduped: list[str] = []
    for question in questions:
        if question not in seen:
            seen.add(question)
            deduped.append(question)
    return deduped[:limit] or FALLBACK_EXAMPLE_QUESTIONS

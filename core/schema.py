"""Pydantic models for schema metadata and analysis plans."""

from __future__ import annotations

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

"""Orchestrate: plan → validate → SQL → DuckDB → explanation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import pandas as pd

from core.executor import ExecutionError, SQLSafetyError, connect_and_register, execute_query
from core.llm import LLMClient, LLMError
from core.query_builder import SQLBuildError, build_sql
from core.schema import (
    AnalysisPlan,
    AnalysisResult,
    Catalog,
    Provenance,
    StepLog,
    generate_example_questions,
)
from core.validators import (
    PlanValidationError,
    detect_ambiguity,
    detect_greeting,
    detect_out_of_scope,
    validate_plan,
)
from core.visualization import build_chart
from utils.helpers import get_ai_narration_enabled, get_max_result_rows

LOGGER = logging.getLogger("datapilot.planner")

MIN_STEP_SECONDS = 0.1


class _StepClock:
    """Timed pipeline stages with optional live UI callbacks."""

    def __init__(
        self,
        on_step: Callable[[StepLog], None] | None = None,
        on_step_start: Callable[[str], None] | None = None,
    ) -> None:
        self.steps: list[StepLog] = []
        self._start = time.perf_counter()
        self._pending: str | None = None
        self._on_step = on_step
        self._on_step_start = on_step_start

    def begin(self, label: str) -> None:
        """Announce a stage before it runs (for live streaming UI)."""
        self._pending = label
        self._start = time.perf_counter()
        if self._on_step_start is not None:
            self._on_step_start(label)

    def mark(self, label: str | None = None, ok: bool = True) -> None:
        resolved = label if label is not None else (self._pending or "Step")
        elapsed = max(time.perf_counter() - self._start, MIN_STEP_SECONDS)
        step = StepLog(label=resolved, seconds=elapsed, ok=ok)
        self.steps.append(step)
        LOGGER.info("Step '%s' took %.2fs (ok=%s)", resolved, elapsed, ok)
        if self._on_step is not None:
            self._on_step(step)
        self._pending = None
        self._start = time.perf_counter()


def _calculation_text(plan: AnalysisPlan) -> str:
    metric = plan.metric
    parts: list[str] = []
    agg = metric.aggregation.upper() if metric.aggregation != "none" else ""
    column = metric.column.split(".")[-1] if metric.column else ""
    if metric.ratio_over:
        denom = metric.ratio_over.split(".")[-1]
        parts.append(f"{agg or 'RATIO'}({column} / {denom})")
    elif agg:
        parts.append(f"{agg}({column or '*'})")
    for extra in plan.extra_metrics:
        extra_agg = extra.aggregation.upper() if extra.aggregation != "none" else ""
        extra_col = extra.column.split(".")[-1]
        parts.append(f"{extra_agg}({extra_col})")
    if plan.group_by:
        groups = ", ".join(item.split(".")[-1] for item in plan.group_by)
        parts.append(f"GROUP BY {groups}")
    if plan.filters:
        parts.append(f"FILTERS: {len(plan.filters)}")
    return " ".join(parts) if parts else "SELECT matching rows"


def _key_columns(plan: AnalysisPlan) -> list[str]:
    names: list[str] = []
    for ref in [
        plan.metric.column,
        plan.metric.ratio_over,
        *[item.column for item in plan.extra_metrics],
        *plan.group_by,
        *[item.column for item in plan.filters],
        *[join.left_column for join in plan.joins],
        *[join.right_column for join in plan.joins],
    ]:
        if not ref or ref == "*":
            continue
        short = ref.split(".")[-1]
        if short not in names:
            names.append(short)
    return names


def round_numeric(frame: pd.DataFrame, decimals: int = 2) -> pd.DataFrame:
    """Round float columns so displayed, explained and charted values agree."""
    rounded = frame.copy()
    for column in rounded.columns:
        if pd.api.types.is_float_dtype(rounded[column]):
            rounded[column] = rounded[column].round(decimals)
    return rounded


def _greeting_result(catalog: Catalog, question: str) -> AnalysisResult:
    if catalog.tables:
        examples = generate_example_questions(catalog, limit=2)
        example_text = (
            " or ".join(f'"{example}"' for example in examples)
            if examples
            else "a totals, average, or trend question"
        )
        message = f"Hello! Ask me a question about your uploaded data — for example, {example_text}"
    else:
        message = (
            "Hi! Upload a CSV or Excel file first, then ask me about totals, "
            "averages, trends, or comparisons in your data."
        )
    return AnalysisResult(question=question, explanation=message, out_of_scope=True)


OUT_OF_SCOPE_MESSAGE = (
    "This question falls outside the scope of your uploaded data. "
    "Please try a question about your datasets."
)


def _out_of_scope_result(question: str, catalog: Catalog) -> AnalysisResult:
    message = OUT_OF_SCOPE_MESSAGE if catalog.tables else (
        "Please upload a CSV or Excel file before asking a question."
    )
    return AnalysisResult(
        question=question,
        explanation=message,
        out_of_scope=True,
    )


def _fallback_explanation(frame: pd.DataFrame, plan: AnalysisPlan) -> str:
    if frame.empty:
        return "No matching rows were found for that question."
    if len(frame) == 1 and len(frame.columns) == 1:
        value = frame.iloc[0, 0]
        label = str(frame.columns[0]).replace("_", " ")
        return f"The {label} is {value}."
    if plan.group_by and len(frame):
        label_col = plan.group_by[0].split(".")[-1]
        value_col = [c for c in frame.columns if c != label_col]
        top = frame.iloc[0]
        if label_col in frame.columns and value_col:
            return (
                f"{top[label_col]} has the leading {value_col[0].replace('_', ' ')} "
                f"at {top[value_col[0]]}."
            )
    return f"The query returned {len(frame)} row(s)."


def run_analysis(
    question: str,
    catalog: Catalog,
    tables: dict[str, pd.DataFrame],
    llm: LLMClient,
    clarification: str = "",
    narrate: bool | None = None,
    on_step: Callable[[StepLog], None] | None = None,
    on_step_start: Callable[[str], None] | None = None,
) -> tuple[AnalysisResult, pd.DataFrame | None, Any]:
    """
    Execute the full analysis pipeline.
    Returns result payload, result frame, and optional Plotly figure.

    `narrate` controls whether a second LLM call rephrases the result into a
    sentence; defaults to the AI_NARRATION env flag (on).

    `on_step_start` / `on_step` let the UI stream each stage live as it
    begins and finishes.

    Every stage is timed via `clock`, and the trace is attached to the
    returned AnalysisResult.steps regardless of which path the function
    exits through — success, clarification, out-of-scope, or error — so the
    UI can always show exactly what happened and how long each part took.
    """
    clock = _StepClock(on_step=on_step, on_step_start=on_step_start)
    use_ai_narration = get_ai_narration_enabled() if narrate is None else narrate

    clock.begin("Checking whether this is a data question")
    if detect_greeting(question):
        clock.mark()
        LOGGER.info("Greeting/small-talk detected; skipping the LLM entirely")
        result = _greeting_result(catalog, question)
        result.steps = clock.steps
        return result, None, None

    out_of_scope = (not clarification) and detect_out_of_scope(question, catalog)
    ambiguous, message, candidates = (
        (False, "", []) if out_of_scope else detect_ambiguity(question, catalog)
    )
    clock.mark()

    if out_of_scope:
        LOGGER.info("Question rejected as out of scope for the uploaded schema")
        result = _out_of_scope_result(question, catalog)
        result.steps = clock.steps
        return result, None, None

    if ambiguous and not clarification:
        LOGGER.info("Ambiguous question detected before planning")
        result = AnalysisResult(
            question=question,
            explanation="",
            needs_clarification=True,
            clarification_question=message,
            candidate_columns=candidates,
            steps=clock.steps,
        )
        return result, None, None

    clock.begin("Asking the AI to plan the query")
    try:
        plan = llm.generate_analysis_plan(question, catalog, clarification=clarification)
    except LLMError as exc:
        clock.mark(ok=False)
        result = AnalysisResult(
            question=question, explanation="", error=exc.user_message, steps=clock.steps
        )
        return result, None, None
    clock.mark()

    if plan.out_of_scope:
        LOGGER.info("Planner reported the question as unanswerable from the schema")
        result = _out_of_scope_result(question, catalog)
        result.steps = clock.steps
        return result, None, None

    if plan.ambiguity and not clarification:
        result = AnalysisResult(
            question=question,
            explanation="",
            plan=plan,
            needs_clarification=True,
            clarification_question=plan.clarification_question
            or "I couldn't determine which metric you meant. Please choose one.",
            candidate_columns=plan.candidate_columns,
            steps=clock.steps,
        )
        return result, None, None

    clock.begin("Validating the plan against your data")
    try:
        plan = validate_plan(plan, catalog)
        sql = build_sql(plan)
    except PlanValidationError as exc:
        clock.mark(ok=False)
        LOGGER.warning("Plan validation failed: %s", exc)
        result = AnalysisResult(
            question=question, explanation="", error=exc.user_message, steps=clock.steps
        )
        return result, None, None
    except SQLBuildError as exc:
        clock.mark(ok=False)
        LOGGER.warning("SQL build failed: %s", exc)
        result = AnalysisResult(
            question=question, explanation="", error=exc.user_message, steps=clock.steps
        )
        return result, None, None
    clock.mark()

    clock.begin("Running the query on DuckDB")
    try:
        connection = connect_and_register(tables)
        try:
            frame = execute_query(connection, sql)
        finally:
            connection.close()
    except SQLSafetyError as exc:
        clock.mark(ok=False)
        LOGGER.warning("SQL safety rejected query: %s", exc)
        result = AnalysisResult(
            question=question, explanation="", error=exc.user_message, steps=clock.steps
        )
        return result, None, None
    except ExecutionError as exc:
        clock.mark(ok=False)
        result = AnalysisResult(
            question=question, explanation="", error=exc.user_message, steps=clock.steps
        )
        return result, None, None
    clock.mark()

    preview = round_numeric(frame.head(get_max_result_rows()))
    calculation = _calculation_text(plan)
    files = [catalog.file_for_table(name) or name for name in plan.tables]
    rows_analyzed = sum(
        table.row_count for table in catalog.tables if table.table_name in plan.tables
    )
    provenance = Provenance(
        files=[name for name in files if name],
        columns=_key_columns(plan),
        calculation=calculation,
        rows_analyzed=rows_analyzed,
        sql=sql,
    )

    clock.begin("Writing the answer")
    if preview.empty:
        explanation = "No matching rows were found for that question."
    elif use_ai_narration:
        records = preview.head(20).to_dict(orient="records")
        try:
            explanation = llm.explain_result(
                question=question,
                intent=plan.intent,
                calculation=calculation,
                result_preview=records,
            )
        except LLMError:
            LOGGER.warning("Explanation LLM failed; using deterministic fallback")
            explanation = _fallback_explanation(preview, plan)
    else:
        explanation = _fallback_explanation(preview, plan)
    clock.mark()

    clock.begin("Building the chart")
    figure = build_chart(preview, plan)
    clock.mark()

    result = AnalysisResult(
        question=question,
        explanation=explanation,
        plan=plan,
        provenance=provenance,
        steps=clock.steps,
    )
    return result, preview, figure

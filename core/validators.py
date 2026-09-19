"""Validate untrusted AnalysisPlan objects against the uploaded catalog."""

from __future__ import annotations

import logging
import re

from core.schema import (
    AnalysisPlan,
    Catalog,
    FilterSpec,
    JoinSpec,
    MetricSpec,
    TableMeta,
)

LOGGER = logging.getLogger("datapilot.validators")


class PlanValidationError(Exception):
    """Raised when an analysis plan cannot be executed safely."""

    def __init__(self, message: str, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or message


STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "what", "which", "who", "whom",
        "how", "many", "much", "does", "did", "are", "was", "were", "has", "have",
        "had", "can", "you", "your", "our", "its", "from", "into", "than", "then",
        "there", "here", "about", "between", "across", "over", "under", "per",
        "show", "give", "tell", "find", "list", "display", "please", "also",
        "some", "any", "all", "each", "more", "most", "less", "least", "best",
        "worst", "top", "bottom", "first", "last", "next", "based", "using",
    }
)

MEASURE_WORDS = frozenset(
    {
        "average", "avg", "mean", "total", "sum", "count", "number", "highest",
        "lowest", "max", "maximum", "min", "minimum", "compare", "comparison",
        "trend", "change", "percentage", "percent", "ratio", "rate", "distribution",
        "breakdown", "group", "rank", "ranking",
    }
)

GENERIC_DATA_WORDS = frozenset(
    {"row", "record", "entry", "file", "data", "dataset", "table", "column", "field"}
)

# Small-talk vocabulary. A question is treated as a greeting only when EVERY
# token it contains is drawn from this set — so "hi, what's the average
# revenue" still goes to the planner, but "hi", "thanks!", "good morning" or
# "ok bye" are answered instantly without spending an LLM call on them.
GREETING_WORDS = frozenset(
    {
        "hi", "hello", "hey", "yo", "hiya", "greetings", "sup",
        "good", "morning", "afternoon", "evening", "day",
        "how", "are", "you", "whats", "what's", "up",
        "thanks", "thank", "cheers", "appreciate", "it",
        "bye", "goodbye", "see", "later", "cya",
        "ok", "okay", "cool", "nice", "great", "awesome", "there",
    }
)


def detect_greeting(question: str) -> bool:
    """True when the message is small talk, not a question about the data."""
    tokens = re.findall(r"[a-z0-9']+", question.lower())
    if not tokens:
        return False
    return all(token in GREETING_WORDS for token in tokens)


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _singular(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _split_tokens(value: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]+", str(value).lower())
    return {_singular(part) for part in parts if len(part) > 2}


def question_tokens(question: str) -> set[str]:
    """Meaningful, singularized tokens from a user question."""
    tokens = _split_tokens(question)
    return {token for token in tokens if token not in STOPWORDS}


def _original_wording(question: str) -> dict[str, str]:
    """Map each singularized token back to the word the user actually typed."""
    mapping: dict[str, str] = {}
    for word in re.findall(r"[A-Za-z0-9]+", question):
        mapping.setdefault(_singular(word.lower()), word.lower())
    return mapping


def schema_tokens(catalog: Catalog) -> set[str]:
    """Every token a question could legitimately refer to in the uploaded data."""
    tokens: set[str] = set(GENERIC_DATA_WORDS)
    for table in catalog.tables:
        tokens |= _split_tokens(table.table_name)
        tokens |= _split_tokens(table.file_name.rsplit(".", 1)[0])
        for column in table.columns:
            tokens |= _split_tokens(column.name)
            for value in [*column.distinct_values, *column.sample_values]:
                tokens |= _split_tokens(value)
    return tokens


def detect_out_of_scope(question: str, catalog: Catalog) -> bool:
    """
    True when the question shares no vocabulary with the uploaded data, so no
    plan over these files could answer it.
    """
    if not catalog.tables:
        return False
    asked = question_tokens(question)
    if not asked:
        return True
    known = schema_tokens(catalog)
    meaningful = asked - MEASURE_WORDS
    if not meaningful:
        return False
    return not (meaningful & known)


def parse_column_ref(raw: str) -> tuple[str | None, str]:
    text = raw.strip().strip('"').strip("`")
    if "." in text:
        table, column = text.split(".", 1)
        return table.strip(), column.strip()
    return None, text


def semantic_column_matches(
    needle: str,
    catalog: Catalog,
    table_hint: str | None = None,
) -> list[tuple[str, str, float]]:
    """
    Lightweight structural matching. Original column names are never renamed.
    """
    target = _normalize_token(needle)
    if not target:
        return []

    scored: list[tuple[str, str, float]] = []
    tables = catalog.tables
    if table_hint:
        found = catalog.get_table(table_hint)
        tables = [found] if found else tables

    for table in tables:
        for column in table.columns:
            name = _normalize_token(column.name)
            score = 0.0
            if name == target:
                score = 1.0
            elif target in name or name in target:
                score = 0.82
            else:
                continue
            scored.append((table.table_name, column.name, score))

    scored.sort(key=lambda item: item[2], reverse=True)
    return scored


def resolve_column(
    raw: str,
    catalog: Catalog,
    table_hint: str | None = None,
    required: bool = True,
) -> tuple[str, str] | None:
    if not raw or not raw.strip():
        if required:
            raise PlanValidationError(
                "Missing column reference",
                "I couldn't find a compatible column for that question.",
            )
        return None

    table_name, column_name = parse_column_ref(raw)
    if table_name and catalog.get_table(table_name) is None:
        if required:
            raise PlanValidationError(
                f"Unknown table {table_name}",
                "I couldn't find a compatible column for that question.",
            )
        return None
    hint = table_name or table_hint

    if hint:
        table = catalog.get_table(hint)
        if table is not None:
            column = table.get_column(column_name)
            if column:
                return table.table_name, column.name
            matches = semantic_column_matches(column_name, catalog, table.table_name)
            if len(matches) == 1 or (matches and matches[0][2] >= 0.99):
                return matches[0][0], matches[0][1]

    exact: list[tuple[str, str]] = []
    for table in catalog.tables:
        column = table.get_column(column_name)
        if column:
            exact.append((table.table_name, column.name))
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1 and table_hint:
        for item in exact:
            if item[0] == table_hint:
                return item
    if len(exact) > 1:
        matches = semantic_column_matches(column_name, catalog)
        if matches:
            return matches[0][0], matches[0][1]

    matches = semantic_column_matches(column_name, catalog)
    high = [item for item in matches if item[2] >= 0.82]
    unique_cols = {(item[0], item[1]) for item in high}
    if len(unique_cols) == 1:
        table, column, _ = high[0]
        return table, column
    if required:
        raise PlanValidationError(
            f"Unknown column {raw}",
            "I couldn't find a compatible column for that question.",
        )
    return None


def _types_compatible(left: TableMeta, left_col: str, right: TableMeta, right_col: str) -> bool:
    left_meta = left.get_column(left_col)
    right_meta = right.get_column(right_col)
    if left_meta is None or right_meta is None:
        return False
    left_num = left_meta.dtype.startswith(("int", "float", "Int", "Float", "uint"))
    right_num = right_meta.dtype.startswith(("int", "float", "Int", "Float", "uint"))
    return left_num == right_num


def _validate_join(join: JoinSpec, catalog: Catalog) -> JoinSpec:
    left_table = catalog.get_table(join.left_table)
    right_table = catalog.get_table(join.right_table)
    if left_table is None or right_table is None:
        raise PlanValidationError(
            "Join references a missing table",
            "Those files do not appear to share a usable key for this analysis.",
        )
    left = left_table.get_column(join.left_column)
    right = right_table.get_column(join.right_column)
    if left is None:
        resolved = resolve_column(join.left_column, catalog, left_table.table_name)
        join.left_column = resolved[1] if resolved else join.left_column
        left = left_table.get_column(join.left_column)
    if right is None:
        resolved = resolve_column(join.right_column, catalog, right_table.table_name)
        join.right_column = resolved[1] if resolved else join.right_column
        right = right_table.get_column(join.right_column)
    if left is None or right is None:
        raise PlanValidationError(
            "Join column missing",
            "Those files do not appear to share a usable key for this analysis.",
        )
    if not _types_compatible(left_table, left.name, right_table, right.name):
        raise PlanValidationError(
            "Join types incompatible",
            "Those files do not appear to share a usable key for this analysis.",
        )
    return JoinSpec(
        left_table=left_table.table_name,
        left_column=left.name,
        right_table=right_table.table_name,
        right_column=right.name,
        how=join.how,
    )


def _validate_metric(metric: MetricSpec, catalog: Catalog, tables: list[str]) -> MetricSpec:
    hint = tables[0] if tables else None
    if metric.aggregation == "count" and not metric.column:
        metric.column = "*"
        return metric
    if metric.column in {"", "*"}:
        if metric.aggregation == "count":
            metric.column = "*"
            return metric
        raise PlanValidationError(
            "Metric column missing",
            "I couldn't find a compatible column for that question.",
        )
    resolved = resolve_column(metric.column, catalog, hint)
    if resolved is None:
        raise PlanValidationError(
            "Metric column missing",
            "I couldn't find a compatible column for that question.",
        )
    metric.column = f"{resolved[0]}.{resolved[1]}"
    if metric.ratio_over:
        ratio = resolve_column(metric.ratio_over, catalog, resolved[0])
        if ratio is None:
            raise PlanValidationError(
                "Ratio column missing",
                "I couldn't find a compatible column for that question.",
            )
        metric.ratio_over = f"{ratio[0]}.{ratio[1]}"
    return metric


def _validate_filter(spec: FilterSpec, catalog: Catalog, tables: list[str]) -> FilterSpec:
    hint = tables[0] if tables else None
    resolved = resolve_column(spec.column, catalog, hint)
    if resolved is None:
        raise PlanValidationError(
            "Filter column missing",
            "I couldn't find a compatible column for that question.",
        )
    spec.column = f"{resolved[0]}.{resolved[1]}"
    if spec.operator == "in" and not isinstance(spec.value, list):
        spec.value = [spec.value]
    if spec.operator == "between":
        if not isinstance(spec.value, list) or len(spec.value) != 2:
            raise PlanValidationError(
                "Between filter needs two values",
                "I couldn't apply that filter. Please rephrase the question.",
            )
    return spec


def _names_a_specific_column(asked: set[str], catalog: Catalog) -> bool:
    """True when the question spells out a multi-word column such as 'sales amount'."""
    for table in catalog.tables:
        for column in table.columns:
            parts = _split_tokens(column.name)
            if len(parts) >= 2 and parts <= asked:
                return True
    return False


def detect_ambiguity(question: str, catalog: Catalog) -> tuple[bool, str, list[str]]:
    """
    Flag a question when one of its words maps to several possible measures,
    e.g. 'sales' with sales_amount, units_sold and sales_target present.
    """
    if not catalog.tables:
        return False, "", []

    asked = question_tokens(question)
    if not asked or _names_a_specific_column(asked, catalog):
        return False, "", []

    wording = _original_wording(question)
    for token in sorted(asked):
        if token in MEASURE_WORDS or token in GENERIC_DATA_WORDS:
            continue
        candidates: list[str] = []
        matched_names: set[str] = set()
        for table in catalog.tables:
            for column in table.columns:
                if not column.is_numeric():
                    continue
                parts = _split_tokens(column.name)
                if token in parts or any(token in part for part in parts):
                    candidates.append(f"{table.table_name}.{column.name}")
                    matched_names.add(column.name.lower())
        if len(matched_names) >= 2 and token not in matched_names:
            message = (
                f"I found multiple possible meanings for '{wording.get(token, token)}'. "
                "Which metric should I use?"
            )
            return True, message, candidates
    return False, "", []


def validate_plan(plan: AnalysisPlan, catalog: Catalog) -> AnalysisPlan:
    """Return a normalized plan or raise PlanValidationError."""
    if not catalog.tables:
        raise PlanValidationError(
            "No tables",
            "Please upload at least one CSV or Excel file first.",
        )

    resolved_tables: list[str] = []
    for name in plan.tables:
        table = catalog.get_table(name)
        if table is None:
            raise PlanValidationError(
                f"Unknown table {name}",
                "I couldn't find a compatible column for that question.",
            )
        resolved_tables.append(table.table_name)
    if not resolved_tables:
        resolved_tables = [catalog.tables[0].table_name]

    joins = [_validate_join(join, catalog) for join in plan.joins]
    join_tables = {join.left_table for join in joins} | {join.right_table for join in joins}
    for table_name in join_tables:
        if table_name not in resolved_tables:
            resolved_tables.append(table_name)

    if len(resolved_tables) > 1 and not joins:
        inferred = infer_joins(resolved_tables, catalog)
        if not inferred:
            raise PlanValidationError(
                "No join path",
                "Those files do not appear to share a usable key for this analysis.",
            )
        joins = inferred

    plan.tables = resolved_tables
    plan.joins = joins
    plan.metric = _validate_metric(plan.metric, catalog, resolved_tables)
    plan.extra_metrics = [
        _validate_metric(metric, catalog, resolved_tables) for metric in plan.extra_metrics
    ]
    plan.filters = [
        _validate_filter(spec, catalog, resolved_tables) for spec in plan.filters
    ]

    resolved_groups: list[str] = []
    for group in plan.group_by:
        resolved = resolve_column(group, catalog, resolved_tables[0])
        if resolved is None:
            raise PlanValidationError(
                f"Unknown group column {group}",
                "I couldn't find a compatible column for that question.",
            )
        resolved_groups.append(f"{resolved[0]}.{resolved[1]}")
    plan.group_by = resolved_groups

    if plan.sort.column:
        sort_resolved = resolve_column(
            plan.sort.column, catalog, resolved_tables[0], required=False
        )
        if sort_resolved:
            plan.sort.column = f"{sort_resolved[0]}.{sort_resolved[1]}"

    if plan.visualization.x:
        x_resolved = resolve_column(
            plan.visualization.x, catalog, resolved_tables[0], required=False
        )
        if x_resolved:
            plan.visualization.x = f"{x_resolved[0]}.{x_resolved[1]}"
    if plan.visualization.y:
        y_resolved = resolve_column(
            plan.visualization.y, catalog, resolved_tables[0], required=False
        )
        if y_resolved:
            plan.visualization.y = f"{y_resolved[0]}.{y_resolved[1]}"

    LOGGER.info("Validated analysis plan for tables %s", plan.tables)
    return plan


def infer_joins(tables: list[str], catalog: Catalog) -> list[JoinSpec]:
    """Infer simple same-name join keys across tables."""
    if len(tables) < 2:
        return []
    base = catalog.get_table(tables[0])
    if base is None:
        return []
    joins: list[JoinSpec] = []
    for other_name in tables[1:]:
        other = catalog.get_table(other_name)
        if other is None:
            continue
        overlap = [
            column
            for column in base.column_names()
            if other.get_column(column) is not None
        ]
        preferred = [name for name in overlap if name.lower().endswith("id")] or overlap
        if not preferred:
            continue
        key = preferred[0]
        if not _types_compatible(base, key, other, key):
            continue
        joins.append(
            JoinSpec(
                left_table=base.table_name,
                left_column=key,
                right_table=other.table_name,
                right_column=key,
                how="inner",
            )
        )
    return joins

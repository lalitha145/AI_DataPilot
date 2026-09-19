"""DuckDB execution with a strict read-only SQL gate."""

from __future__ import annotations

import logging
import re

import duckdb
import pandas as pd

LOGGER = logging.getLogger("datapilot.executor")

FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "ATTACH",
    "COPY",
    "INSTALL",
    "LOAD",
    "PRAGMA",
    "EXPORT",
    "IMPORT",
    "REPLACE",
    "MERGE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "CALL",
    "EXECUTE",
)


class SQLSafetyError(Exception):
    def __init__(self, message: str, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or "That query is not allowed."


class ExecutionError(Exception):
    def __init__(self, message: str, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or "I couldn't complete that analysis. Please try again."


def assert_readonly_select(sql: str) -> None:
    """Reject anything that is not a single SELECT statement."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise SQLSafetyError("Empty SQL")
    if ";" in stripped:
        raise SQLSafetyError("Multiple statements are not allowed")

    compact = re.sub(r"\s+", " ", stripped)
    if not re.match(r"^SELECT\b", compact, flags=re.IGNORECASE):
        raise SQLSafetyError("Only SELECT queries are allowed")

    upper = compact.upper()
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            LOGGER.warning("Rejected SQL containing forbidden keyword %s", keyword)
            raise SQLSafetyError(f"Forbidden keyword: {keyword}")


def connect_and_register(tables: dict[str, pd.DataFrame]) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(database=":memory:")
    for name, frame in tables.items():
        connection.register(name, frame)
    return connection


def execute_query(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
) -> pd.DataFrame:
    """Validate and execute a read-only DuckDB query."""
    assert_readonly_select(sql)
    try:
        LOGGER.info("Executing validated SQL")
        result = connection.execute(sql).fetchdf()
    except SQLSafetyError:
        raise
    except Exception as exc:
        LOGGER.exception("DuckDB execution failed")
        raise ExecutionError(str(exc)) from exc
    return result

"""Load CSV/Excel files into pandas DataFrames and catalog metadata."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import BinaryIO

import pandas as pd

from core.schema import Catalog, ColumnMeta, TableMeta
from utils.helpers import safe_identifier, unique_name

LOGGER = logging.getLogger("datapilot.loader")

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


class FileLoadError(Exception):
    """Raised when an uploaded file cannot be loaded."""


def _extension(filename: str) -> str:
    lowered = filename.lower()
    for ext in (".xlsx", ".xls", ".csv"):
        if lowered.endswith(ext):
            return ext
    return ""


def read_tabular_file(filename: str, data: bytes | BinaryIO) -> pd.DataFrame:
    """Read a CSV or Excel payload into a DataFrame."""
    ext = _extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise FileLoadError(
            "That file type is not supported. Please upload CSV or Excel files."
        )

    buffer = BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    try:
        if ext == ".csv":
            frame = pd.read_csv(buffer)
        elif ext == ".xlsx":
            frame = pd.read_excel(buffer, engine="openpyxl")
        else:
            frame = pd.read_excel(buffer)
    except Exception as exc:
        LOGGER.exception("Failed to parse file %s", filename)
        raise FileLoadError(
            "I couldn't read that file. Please check the format and try again."
        ) from exc

    if frame.empty:
        raise FileLoadError("That file appears to be empty.")

    frame.columns = [str(column).strip() for column in frame.columns]
    if len(frame.columns) != len(set(frame.columns)):
        LOGGER.warning("Duplicate columns detected in %s; keeping pandas suffixes", filename)

    return frame


def _low_cardinality_values(series: pd.Series, limit: int = 25) -> list[str]:
    """
    List distinct values for small categorical columns so the planner can map
    question wording (e.g. "Bangalore") to real filter values.
    """
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return []
    unique = series.dropna().astype(str).unique()
    if len(unique) > limit:
        return []
    return [str(value) for value in unique]


def extract_table_meta(
    file_name: str,
    table_name: str,
    frame: pd.DataFrame,
    sample_rows: int = 5,
) -> TableMeta:
    columns: list[ColumnMeta] = []
    for column in frame.columns:
        series = frame[column]
        sample = (
            series.dropna().astype(str).head(3).tolist()
        )
        columns.append(
            ColumnMeta(
                name=str(column),
                dtype=str(series.dtype),
                null_count=int(series.isna().sum()),
                sample_values=sample,
                distinct_values=_low_cardinality_values(series),
            )
        )
    records = frame.head(sample_rows).where(frame.head(sample_rows).notna(), None)
    return TableMeta(
        file_name=file_name,
        table_name=table_name,
        row_count=int(len(frame)),
        column_count=int(len(frame.columns)),
        columns=columns,
        sample_rows=records.to_dict(orient="records"),
    )


def register_uploads(
    files: list[tuple[str, bytes]],
    existing_tables: set[str] | None = None,
) -> tuple[dict[str, pd.DataFrame], Catalog]:
    """
    Load multiple files, assign unique table names, and build a catalog.
    """
    tables: dict[str, pd.DataFrame] = {}
    metas: list[TableMeta] = []
    taken = set(existing_tables or [])

    for filename, payload in files:
        frame = read_tabular_file(filename, payload)
        base = safe_identifier(filename)
        table_name = unique_name(base, taken)
        taken.add(table_name)
        tables[table_name] = frame
        metas.append(extract_table_meta(filename, table_name, frame))
        LOGGER.info(
            "Loaded file %s as table %s (%s rows, %s columns)",
            filename,
            table_name,
            len(frame),
            len(frame.columns),
        )

    return tables, Catalog(tables=metas)

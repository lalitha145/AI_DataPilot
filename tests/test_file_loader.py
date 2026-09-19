"""Tests for CSV/Excel loading and multi-file registration."""

from __future__ import annotations

from io import BytesIO

import pandas as pd
import pytest

from utils.file_loader import FileLoadError, read_tabular_file, register_uploads


def test_csv_loading() -> None:
    csv = b"employee_id,name\n1,Ada\n2,Grace\n"
    frame = read_tabular_file("people.csv", csv)
    assert len(frame) == 2
    assert list(frame.columns) == ["employee_id", "name"]


def test_excel_loading() -> None:
    source = pd.DataFrame({"employee_id": [1, 2], "month": ["2025-01", "2025-02"]})
    buffer = BytesIO()
    source.to_excel(buffer, index=False, engine="openpyxl")
    frame = read_tabular_file("attendance.xlsx", buffer.getvalue())
    assert len(frame) == 2
    assert "month" in frame.columns


def test_empty_file_rejected() -> None:
    with pytest.raises(FileLoadError):
        read_tabular_file("empty.csv", b"col_a,col_b\n")


def test_unsupported_file() -> None:
    with pytest.raises(FileLoadError):
        read_tabular_file("notes.txt", b"hello")


def test_multi_file_registration() -> None:
    files = [
        ("employees.csv", b"employee_id,department\n1,Eng\n"),
        ("performance.csv", b"employee_id,score\n1,4.2\n"),
    ]
    tables, catalog = register_uploads(files)
    assert set(tables) == {"employees", "performance"}
    assert catalog.get_table("employees") is not None
    assert catalog.get_table("performance").row_count == 1


def test_duplicate_table_names() -> None:
    files = [
        ("employees.csv", b"id,name\n1,A\n"),
        ("employees.xlsx", _xlsx_bytes(pd.DataFrame({"id": [2], "name": ["B"]}))),
    ]
    tables, catalog = register_uploads(files)
    assert "employees" in tables
    assert "employees_2" in tables
    assert len(catalog.tables) == 2


def _xlsx_bytes(frame: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()

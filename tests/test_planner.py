"""Tests for AnalysisPlan parsing and planner JSON handling."""

from __future__ import annotations

import pytest

from core.llm import LLMError, parse_analysis_plan
from core.schema import AnalysisPlan


VALID_PLAN = """
{
  "intent": "aggregation",
  "tables": ["performance"],
  "joins": [],
  "filters": [],
  "group_by": ["department"],
  "metric": {
    "column": "performance_score",
    "aggregation": "avg",
    "alias": "",
    "ratio_over": ""
  },
  "extra_metrics": [],
  "sort": {"column": "performance_score", "direction": "desc"},
  "limit": 10,
  "visualization": {"needed": true, "type": "bar", "x": "department", "y": "performance_score"},
  "ambiguity": false,
  "clarification_question": "",
  "candidate_columns": []
}
"""


def test_valid_analysis_plan() -> None:
    plan = parse_analysis_plan(VALID_PLAN)
    assert isinstance(plan, AnalysisPlan)
    assert plan.intent == "aggregation"
    assert plan.metric.aggregation == "avg"


def test_plan_from_fenced_json() -> None:
    plan = parse_analysis_plan("Here you go:\n```json\n" + VALID_PLAN + "\n```")
    assert plan.tables == ["performance"]


def test_malformed_llm_response() -> None:
    with pytest.raises(LLMError):
        parse_analysis_plan("I think Engineering is highest.")


def test_invalid_enum_rejected() -> None:
    raw = VALID_PLAN.replace('"aggregation": "avg"', '"aggregation": "median"')
    with pytest.raises(LLMError):
        parse_analysis_plan(raw)


def test_unknown_fields_rejected() -> None:
    raw = VALID_PLAN.replace('"candidate_columns": []', '"candidate_columns": [], "sql": "DROP TABLE x"')
    with pytest.raises(LLMError):
        parse_analysis_plan(raw)

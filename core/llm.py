"""OpenRouter LLM client. Provider details stay in this module."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from pydantic import ValidationError

from core.schema import AnalysisPlan, Catalog
from utils.helpers import get_llm_timeout, get_model, get_openrouter_api_key, get_openrouter_base_url

LOGGER = logging.getLogger("datapilot.llm")

PROMPT_VALUE_LIMIT = 12

PLAN_PROMPT = """You are DataPilot's query planner for tabular data analysis.

Return ONLY valid JSON that matches this schema (no markdown, no commentary):
{{
  "intent": "aggregation | comparison | trend | filtering | ranking | distribution | lookup",
  "tables": ["table_name"],
  "joins": [
    {{
      "left_table": "",
      "left_column": "",
      "right_table": "",
      "right_column": "",
      "how": "inner"
    }}
  ],
  "filters": [
    {{"column": "table.column", "operator": "eq", "value": ""}}
  ],
  "group_by": ["table.column"],
  "metric": {{
    "column": "table.column",
    "aggregation": "sum | avg | count | min | max | none",
    "alias": "",
    "ratio_over": ""
  }},
  "extra_metrics": [],
  "sort": {{"column": "", "direction": "asc | desc"}},
  "limit": 10,
  "visualization": {{
    "needed": true,
    "type": "bar | line | pie | scatter | table | none",
    "x": "",
    "y": ""
  }},
  "ambiguity": false,
  "clarification_question": "",
  "candidate_columns": [],
  "out_of_scope": false
}}

Rules:
- Return only the keys you actually need. Omitted keys fall back to safe defaults, so never emit empty arrays, empty strings, or false flags.
- You do NOT calculate numbers. You only describe how to compute them.
- Use only tables and columns listed in the catalog. Never invent names.
- Prefer table.column references.
- For a rate or percentage-style metric (e.g. attendance rate, completion rate, pass rate), find two numeric columns where one represents an achieved/present count and the other represents a total/possible count, and compute their ratio (aggregation avg or sum, with ratio_over set to the total column). Infer this from whatever columns exist in the catalog — never assume specific column names.
- For percentages of a category, use aggregation count, a matching filter, and grouping if useful.
- Join tables when the question needs columns from more than one file.
- Set visualization.needed true only when a chart helps (comparisons, rankings, trends, distributions). Simple totals should use needed=false and type=none.
- Trends over time should use a line chart with the time column on x.
- Rankings and grouped comparisons should use a bar chart.
- Scatter only when relating two numeric measures.
- If the question is genuinely ambiguous (multiple plausible metrics, dates, or join keys), set ambiguity=true, write a short clarification_question, list candidate_columns as table.column entries, and do not guess.
- Do not ask for clarification when the intent is obvious.
- If the question cannot be answered from the catalog at all (general knowledge, chit-chat, advice, or data that simply is not in these files), set out_of_scope=true and leave the rest of the plan empty. Never invent a column to force an answer.
- Categorical filter values must come from the catalog's distinct_values when available.
- Keep extra_metrics for a second measure when the user asks to compare two metrics.
- Operators allowed: eq, ne, gt, gte, lt, lte, in, like, between.
- Filter values must be literals, not SQL.
- limit should be small (default 10). Ranking "highest/lowest" should sort accordingly and often use limit 1 or 10.

User question:
{question}

Catalog JSON:
{catalog}

{clarification}
"""

EXPLAIN_PROMPT = """You explain DuckDB query results for a business user.

Write 1-3 concise sentences.
Use ONLY the numbers present in the result JSON, exactly as written (they are already rounded to two decimals). Never invent, round-shift, or recalculate values.
If the result is empty, say no matching rows were found.
Mention comparison or trend direction when relevant.
Do not mention SQL, DuckDB, JSON, or internal planning.

Question: {question}
Intent: {intent}
Calculation: {calculation}
Result JSON: {result}
"""


class LLMError(Exception):
    def __init__(self, message: str, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or "I couldn't reach the AI service. Please try again."


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else get_openrouter_api_key()
        self.model = model or get_model()
        self.base_url = base_url or get_openrouter_base_url()
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        # Re-read at call time so Streamlit Cloud secrets / reboots pick up the key
        # even if this client was cache_resource'd before secrets were available.
        if not self.api_key:
            self.api_key = get_openrouter_api_key()
        if not self.model:
            self.model = get_model()
        if not self.base_url:
            self.base_url = get_openrouter_base_url()
        if not self.api_key:
            raise LLMError(
                "Missing OPENROUTER_API_KEY",
                "The AI service is not configured. On Streamlit Cloud, open "
                "Manage app → Settings → Secrets and set OPENROUTER_API_KEY "
                '(TOML: OPENROUTER_API_KEY = "your-key"). Locally, set it in .env.',
            )
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=get_llm_timeout(),
                max_retries=0,
                default_headers={
                    "HTTP-Referer": "https://datapilot.streamlit.app",
                    "X-Title": "DataPilot",
                },
            )
        return self._client

    def _complete(self, prompt: str) -> str:
        client = self._get_client()
        start = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a careful structured-planning assistant. Output valid JSON when asked.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
        except RateLimitError as exc:
            LOGGER.exception("OpenRouter rate limit after %.1fs", time.perf_counter() - start)
            raise LLMError(str(exc), "The AI service is busy. Please try again in a moment.") from exc
        except APIConnectionError as exc:
            LOGGER.exception("OpenRouter connection failure after %.1fs", time.perf_counter() - start)
            raise LLMError(str(exc), "I couldn't reach the AI service. Please try again.") from exc
        except APIStatusError as exc:
            LOGGER.exception("OpenRouter API status error after %.1fs", time.perf_counter() - start)
            raise LLMError(str(exc), "I couldn't reach the AI service. Please try again.") from exc
        except Exception as exc:
            LOGGER.exception("OpenRouter unexpected failure after %.1fs", time.perf_counter() - start)
            raise LLMError(str(exc), "I couldn't reach the AI service. Please try again.") from exc

        LOGGER.info("LLM call completed in %.1fs (model=%s)", time.perf_counter() - start, self.model)
        choice = response.choices[0].message.content if response.choices else None
        if not choice:
            raise LLMError("Empty LLM response", "I couldn't understand the AI response. Please try again.")
        return choice

    def generate_analysis_plan(
        self,
        question: str,
        catalog: Catalog,
        clarification: str = "",
    ) -> AnalysisPlan:
        extra = f"User clarification: {clarification}" if clarification else ""
        prompt = PLAN_PROMPT.format(
            question=question,
            catalog=json.dumps(compact_catalog(catalog), default=str),
            clarification=extra,
        )
        LOGGER.info("Requesting analysis plan from LLM")
        raw = self._complete(prompt)
        return parse_analysis_plan(raw)

    def explain_result(
        self,
        question: str,
        intent: str,
        calculation: str,
        result_preview: list[dict[str, Any]],
    ) -> str:
        prompt = EXPLAIN_PROMPT.format(
            question=question,
            intent=intent,
            calculation=calculation,
            result=json.dumps(result_preview, default=str),
        )
        LOGGER.info("Requesting result explanation from LLM")
        text = self._complete(prompt).strip()
        return text.strip('"')


def compact_catalog(catalog: Catalog) -> dict[str, Any]:
    """
    Schema summary for the planner prompt. Sample rows and per-column stats are
    useful in the UI but only slow the model down, so they are left out.
    """
    tables = []
    for table in catalog.tables:
        columns = []
        for column in table.columns:
            entry: dict[str, Any] = {"name": column.name, "dtype": column.dtype}
            if column.distinct_values:
                entry["values"] = column.distinct_values[:PROMPT_VALUE_LIMIT]
            elif column.sample_values:
                entry["example"] = column.sample_values[0]
            columns.append(entry)
        tables.append(
            {
                "table": table.table_name,
                "file": table.file_name,
                "rows": table.row_count,
                "columns": columns,
            }
        )
    return {"tables": tables}


def parse_analysis_plan(raw: str) -> AnalysisPlan:
    """Parse and validate LLM JSON into an AnalysisPlan."""
    payload = extract_json_object(raw)
    try:
        return AnalysisPlan.model_validate(payload)
    except ValidationError as exc:
        LOGGER.warning("Malformed analysis plan: %s", exc)
        raise LLMError(
            f"Invalid analysis plan: {exc}",
            "I couldn't interpret that question into a valid analysis. Please rephrase it.",
        ) from exc


def extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMError(
                "LLM response was not JSON",
                "I couldn't interpret that question into a valid analysis. Please rephrase it.",
            )
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(
            "Malformed JSON from LLM",
            "I couldn't interpret that question into a valid analysis. Please rephrase it.",
        ) from exc
    if not isinstance(data, dict):
        raise LLMError(
            "LLM JSON was not an object",
            "I couldn't interpret that question into a valid analysis. Please rephrase it.",
        )
    return data

"""Configuration helpers and identifier sanitization."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger("datapilot")

DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731:free"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MAX_RESULT_ROWS = 50
DEFAULT_TIMEOUT = 60


def get_env(name: str, default: str | None = None) -> str | None:
    """Read from OS env first, then Streamlit secrets (Cloud deploy)."""
    value = os.getenv(name)
    if value is None or not str(value).strip():
        value = _secret_value(name)
    if value is None:
        return default
    stripped = str(value).strip()
    return stripped or default


def _secret_value(name: str) -> str | None:
    """Best-effort read of a flat Streamlit secret."""
    try:
        import streamlit as st

        secrets = st.secrets
    except Exception:
        return None

    try:
        raw = secrets[name]
    except Exception:
        raw = None
    if raw is not None and str(raw).strip():
        return str(raw)

    # Support nested tables like [openrouter] OPENROUTER_API_KEY = "..."
    try:
        for item in secrets.values():
            if isinstance(item, dict) and name in item:
                nested = item[name]
                if nested is not None and str(nested).strip():
                    return str(nested)
            try:
                nested = item[name]  # type: ignore[index]
                if nested is not None and str(nested).strip():
                    return str(nested)
            except Exception:
                continue
    except Exception:
        return None
    return None


def hydrate_streamlit_secrets() -> None:
    """Copy Streamlit secrets into os.environ so the rest of the app can use getenv."""
    keys = (
        "OPENROUTER_API_KEY",
        "MODEL",
        "OPENROUTER_BASE_URL",
        "APP_TITLE",
        "MAX_RESULT_ROWS",
        "LLM_TIMEOUT_SECONDS",
        "AI_NARRATION",
    )
    for key in keys:
        if os.getenv(key):
            continue
        value = _secret_value(key)
        if value:
            os.environ[key] = value


def get_model() -> str:
    return get_env("MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def get_openrouter_base_url() -> str:
    return get_env("OPENROUTER_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL


def get_openrouter_api_key() -> str | None:
    return get_env("OPENROUTER_API_KEY")


def get_max_result_rows() -> int:
    raw = get_env("MAX_RESULT_ROWS", str(DEFAULT_MAX_RESULT_ROWS))
    try:
        return max(1, int(raw or DEFAULT_MAX_RESULT_ROWS))
    except ValueError:
        return DEFAULT_MAX_RESULT_ROWS


def get_llm_timeout() -> int:
    raw = get_env("LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT))
    try:
        return max(10, int(raw or DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def get_ai_narration_enabled() -> bool:
    """
    Whether the deterministic result gets rephrased into natural language by
    a second LLM call. On by default — the earlier off-by-default choice
    traded away answer quality for a latency saving that turned out not to
    be the real bottleneck (both calls measured ~1.5-2s in practice). Kept
    as an env override in case a slower/rate-limited provider ever makes
    the trade-off worth it again, but there's no UI toggle for it.
    """
    raw = get_env("AI_NARRATION", "true") or "true"
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def configure_logging() -> None:
    if LOGGER.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def safe_identifier(name: str, fallback: str = "table") -> str:
    """Create a DuckDB-safe identifier from a filename or column."""
    stem = Path(name).stem if "." in name else name
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", stem).strip("_").lower()
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"{fallback}_{cleaned}"
    return cleaned


def unique_name(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    index = 2
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"


def quote_ident(name: str) -> str:
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def qualify(table: str, column: str) -> str:
    return f"{quote_ident(table)}.{quote_ident(column)}"

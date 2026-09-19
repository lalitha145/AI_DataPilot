"""DataPilot Streamlit UI — upload, ask, visualize."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from core.llm import LLMClient
from core.planner import MIN_STEP_SECONDS, run_analysis
from core.schema import AnalysisResult, Catalog, StepLog
from utils.file_loader import FileLoadError, register_uploads
from utils.helpers import configure_logging, hydrate_streamlit_secrets

configure_logging()
LOGGER = logging.getLogger("datapilot.app")

EXAMPLE_QUESTIONS = [
    "Which department has the highest average performance score?",
    "What is the average attendance by department?",
    "Compare employees across locations.",
    "How did average performance change over time?",
    "Does the department with the lowest attendance also have the lowest performance?",
]

CUSTOM_CSS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Syne:wght@600;700;800&display=swap" rel="stylesheet">
<style>
    :root {
        --ink: #0F1C24;
        --muted: #5A6B75;
        --line: #C9D8D4;
        --surface: #FFFFFF;
        --surface-soft: #F3F8F6;
        --accent: #0E7C6B;
        --accent-deep: #0A5C50;
        --accent-soft: #D6EFE9;
        --ok: #0F8A5F;
        --shadow: 0 12px 40px rgba(15, 28, 36, 0.07);
    }

    html, body, [class*="css"] {
        font-family: "DM Sans", sans-serif;
        color: var(--ink);
    }

    .stApp {
        background:
            radial-gradient(1100px 520px at 12% -8%, rgba(14, 124, 107, 0.14), transparent 55%),
            radial-gradient(900px 480px at 100% 8%, rgba(15, 28, 36, 0.06), transparent 50%),
            linear-gradient(180deg, #E8F2F0 0%, #F4F7F6 42%, #EEF3F1 100%);
    }

    .stApp::before {
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        opacity: 0.35;
        background-image:
            linear-gradient(rgba(15, 28, 36, 0.035) 1px, transparent 1px),
            linear-gradient(90deg, rgba(15, 28, 36, 0.035) 1px, transparent 1px);
        background-size: 28px 28px;
        mask-image: linear-gradient(180deg, rgba(0,0,0,0.55), transparent 70%);
    }

    .block-container {
        max-width: 820px;
        padding-top: 1.6rem;
        padding-bottom: 3.5rem;
    }

    #MainMenu, footer { visibility: hidden; }

    .hero {
        text-align: center;
        padding: 1.4rem 0.5rem 1.8rem;
        animation: rise 0.55s ease-out both;
    }
    .hero-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 3rem;
        height: 3rem;
        margin-bottom: 0.85rem;
        border-radius: 14px;
        background: linear-gradient(145deg, var(--accent) 0%, var(--accent-deep) 100%);
        color: #fff;
        font-family: "Syne", sans-serif;
        font-weight: 800;
        font-size: 1.15rem;
        letter-spacing: -0.04em;
        box-shadow: 0 10px 24px rgba(14, 124, 107, 0.28);
    }
    .hero h1 {
        font-family: "Syne", sans-serif;
        font-weight: 800;
        font-size: clamp(2.4rem, 5vw, 3.15rem);
        letter-spacing: -0.045em;
        line-height: 1.05;
        margin: 0;
        color: var(--ink);
    }
    .hero-tag {
        margin: 0.75rem auto 0;
        max-width: 28rem;
        color: var(--muted);
        font-size: 1.05rem;
        line-height: 1.45;
        font-weight: 400;
    }

    .section-head {
        display: flex;
        align-items: baseline;
        gap: 0.65rem;
        margin: 1.55rem 0 0.7rem;
        animation: rise 0.55s ease-out both;
    }
    .section-head h3 {
        font-family: "Syne", sans-serif;
        font-size: 1.05rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        margin: 0;
        color: var(--ink);
    }
    .section-head span {
        color: var(--muted);
        font-size: 0.86rem;
    }

    .panel {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 1.15rem 1.2rem 1.2rem;
        box-shadow: var(--shadow);
        animation: rise 0.6s ease-out both;
    }

    .file-chip {
        display: inline-block;
        background: var(--accent-soft);
        color: var(--accent-deep);
        border: 1px solid rgba(14, 124, 107, 0.18);
        border-radius: 8px;
        padding: 0.28rem 0.7rem;
        margin: 0.2rem 0.35rem 0.2rem 0;
        font-size: 0.82rem;
        font-weight: 600;
        letter-spacing: -0.01em;
    }
    .file-meta {
        color: var(--muted);
        font-size: 0.88rem;
        margin-top: 0.45rem;
    }

    .answer-card {
        background: var(--surface);
        border: 1px solid var(--line);
        border-left: 4px solid var(--accent);
        border-radius: 16px;
        padding: 1.25rem 1.35rem;
        margin: 0.35rem 0 1rem;
        box-shadow: var(--shadow);
        animation: rise 0.5s ease-out both;
    }
    .answer {
        font-size: 1.14rem;
        line-height: 1.6;
        color: var(--ink);
        font-weight: 500;
    }
    .ok {
        color: var(--ok);
        font-weight: 700;
        margin-right: 0.4rem;
    }

    .prov-card {
        background: var(--surface-soft);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 1.1rem 1.2rem 1.15rem;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.85rem 1.25rem;
    }
    .prov-item { min-width: 0; }
    .prov-label {
        color: var(--muted);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-weight: 600;
        margin-bottom: 0.2rem;
    }
    .prov-value {
        color: var(--ink);
        font-size: 0.95rem;
        font-weight: 500;
        word-break: break-word;
    }
    @media (max-width: 640px) {
        .prov-card { grid-template-columns: 1fr; }
    }

    .step-line {
        font-size: 0.95rem;
        line-height: 1.7;
        color: var(--ink);
    }
    .step-time {
        color: var(--muted);
        font-style: italic;
        font-size: 0.88rem;
    }

    div[data-testid="stFileUploader"] section {
        background: var(--surface-soft);
        border: 1.5px dashed var(--line);
        border-radius: 14px;
        padding: 0.35rem;
    }
    div[data-testid="stFileUploader"] section:hover {
        border-color: var(--accent);
        background: #ECF7F4;
    }

    .stButton > button {
        border-radius: 11px !important;
        font-family: "DM Sans", sans-serif !important;
        font-weight: 600 !important;
        letter-spacing: -0.01em;
        border: 1px solid var(--line) !important;
        transition: transform 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
    }
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 8px 18px rgba(15, 28, 36, 0.08);
    }
    .stButton > button[kind="primary"],
    .stButton > button[data-testid="baseButton-primary"] {
        background: var(--accent) !important;
        border-color: var(--accent) !important;
        color: #fff !important;
    }
    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid="baseButton-primary"]:hover {
        background: var(--accent-deep) !important;
        border-color: var(--accent-deep) !important;
    }

    div[data-testid="stTextInput"] input {
        border-radius: 12px !important;
        border: 1px solid var(--line) !important;
        background: var(--surface) !important;
        font-family: "DM Sans", sans-serif !important;
        padding: 0.7rem 0.9rem !important;
    }
    div[data-testid="stTextInput"] input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px rgba(14, 124, 107, 0.18) !important;
    }

    div[data-testid="stExpander"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 14px;
        box-shadow: none;
    }

    div[data-testid="stStatusWidget"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 14px;
    }

    [data-testid="stDataFrame"] {
        border: 1px solid var(--line);
        border-radius: 12px;
        overflow: hidden;
    }

    @keyframes rise {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
    }
</style>
"""


@st.cache_resource(show_spinner=False)
def _llm_client() -> LLMClient:
    """One client per session so each question reuses the open connection."""
    return LLMClient()


def _init_state() -> None:
    defaults: dict = {
        "tables": {},
        "catalog": Catalog(),
        "upload_sig": None,
        "pending_question": "",
        "result": None,
        "result_frame": None,
        "chart": None,
        "clarification": "",
        "error": "",
        "steps_streamed": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _signature(files: list) -> tuple:
    return tuple((item.name, item.size) for item in files)


def _step_line(step: StepLog) -> str:
    icon = "✅" if step.ok else "❌"
    seconds = max(step.seconds, MIN_STEP_SECONDS)
    return (
        f"<span class='step-line'>{icon} {step.label} &nbsp;&nbsp;"
        f"<span class='step-time'>({seconds:.1f} seconds)</span></span>"
    )


def _render_live_steps(done: list[str], pending: str | None = None) -> str:
    lines = list(done)
    if pending:
        lines.append(f"<span class='step-line'>⏳ {pending}…</span>")
    return "<br>".join(lines)


def _load_files(files: list) -> None:
    sig = _signature(files)
    if sig == st.session_state.upload_sig:
        return
    payloads = [(item.name, item.getvalue()) for item in files]
    try:
        tables, catalog = register_uploads(payloads)
    except FileLoadError as exc:
        st.session_state.error = str(exc)
        LOGGER.warning("Upload failed: %s", exc)
        return
    st.session_state.tables = tables
    st.session_state.catalog = catalog
    st.session_state.upload_sig = sig
    st.session_state.error = ""
    st.session_state.result = None
    st.session_state.result_frame = None
    st.session_state.chart = None
    LOGGER.info("Registered %s uploaded tables", len(tables))


def _run_question(question: str) -> None:
    question = question.strip()
    if not question:
        return
    if not st.session_state.tables:
        st.session_state.error = "Please upload at least one CSV or Excel file first."
        return

    done_lines: list[str] = []
    with st.status("Analyzing your data…", expanded=True) as status:
        live = st.empty()

        def on_step_start(label: str) -> None:
            live.markdown(_render_live_steps(done_lines, pending=label), unsafe_allow_html=True)

        def on_step(step: StepLog) -> None:
            done_lines.append(_step_line(step))
            live.markdown(_render_live_steps(done_lines), unsafe_allow_html=True)

        try:
            result, frame, chart = run_analysis(
                question=question,
                catalog=st.session_state.catalog,
                tables=st.session_state.tables,
                llm=_llm_client(),
                clarification=st.session_state.clarification,
                on_step=on_step,
                on_step_start=on_step_start,
            )
        except Exception:
            LOGGER.exception("Analysis crashed")
            result = AnalysisResult(
                question=question,
                explanation="",
                error="Something went wrong while analyzing. Please try again.",
            )
            frame, chart = None, None
            status.update(label="Analysis failed", state="error", expanded=True)
        else:
            total = sum(max(step.seconds, MIN_STEP_SECONDS) for step in result.steps)
            if result.error:
                status.update(label="Analysis failed", state="error", expanded=True)
            else:
                status.update(
                    label=f"Done — {len(result.steps)} steps · {total:.1f}s",
                    state="complete",
                    expanded=True,
                )

    st.session_state.result = result
    st.session_state.result_frame = frame
    st.session_state.chart = chart
    st.session_state.error = result.error or ""
    st.session_state.pending_question = ""
    st.session_state.clarification = ""
    st.session_state.steps_streamed = True


def _section(title: str, hint: str = "") -> None:
    hint_html = f"<span>{hint}</span>" if hint else ""
    st.markdown(
        f"<div class='section-head'><h3>{title}</h3>{hint_html}</div>",
        unsafe_allow_html=True,
    )


def _render_header() -> None:
    st.markdown(
        """
        <div class="hero">
            <div class="hero-mark">DP</div>
            <h1>DataPilot</h1>
            <p class="hero-tag">Upload your data. Ask questions. Get insights.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_uploads() -> None:
    _section("Upload", "CSV or Excel")
    files = st.file_uploader(
        "Choose files",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )
    if files:
        _load_files(files)

    catalog: Catalog = st.session_state.catalog
    if catalog.tables:
        chips = " ".join(
            f"<span class='file-chip'>{table.file_name}</span>" for table in catalog.tables
        )
        count = len(catalog.tables)
        st.markdown(
            f"<div class='panel'>{chips}"
            f"<div class='file-meta'>{count} file{'s' if count != 1 else ''} ready</div></div>",
            unsafe_allow_html=True,
        )
        with st.expander("Preview schema"):
            for table in catalog.tables:
                st.markdown(
                    f"**{table.file_name}** → `{table.table_name}` · "
                    f"{table.row_count:,} rows · {table.column_count} columns"
                )
                st.dataframe(pd.DataFrame(table.sample_rows), hide_index=True, width="stretch")


def _render_examples() -> None:
    if not st.session_state.tables:
        return
    _section("Examples", "click to ask")
    cols = st.columns(len(EXAMPLE_QUESTIONS[:3]))
    for col, question in zip(cols, EXAMPLE_QUESTIONS[:3]):
        if col.button(question, use_container_width=True):
            st.session_state.question_input = question
            st.session_state.pending_question = question
            st.rerun()
    extra = st.columns(2)
    for col, question in zip(extra, EXAMPLE_QUESTIONS[3:]):
        if col.button(question, use_container_width=True):
            st.session_state.question_input = question
            st.session_state.pending_question = question
            st.rerun()


def _render_ask() -> None:
    _section("Ask", "about your data")
    left, right = st.columns([5, 1])
    with left:
        st.text_input(
            "Question",
            key="question_input",
            placeholder="e.g. Which department has the highest average performance score?",
            label_visibility="collapsed",
        )
    with right:
        ask_clicked = st.button("Ask", type="primary", use_container_width=True)

    if ask_clicked:
        st.session_state.pending_question = st.session_state.get("question_input", "")
        st.rerun()

    if st.session_state.pending_question:
        _run_question(st.session_state.pending_question)


def _option_label(reference: str) -> str:
    """Show clarification choices by file name rather than internal table name."""
    table_name, _, column = reference.partition(".")
    catalog: Catalog = st.session_state.catalog
    table = catalog.get_table(table_name)
    return f"{column} — {table.file_name}" if table else reference


def _render_clarification(result: AnalysisResult) -> None:
    st.info(
        result.clarification_question
        or "I found more than one possible meaning. Please choose one."
    )
    options = result.candidate_columns or []
    if options:
        choice = st.radio(
            "Possible fields",
            options,
            format_func=_option_label,
            key="clarify_choice",
        )
        if st.button("Use this field", type="primary"):
            st.session_state.clarification = f"Use column {choice}"
            st.session_state.pending_question = st.session_state.get(
                "question_input", result.question
            )
            st.rerun()


def _render_steps(result: AnalysisResult) -> None:
    if not result.steps:
        return
    # Same-run streaming already showed these inside st.status — skip duplicate.
    if st.session_state.pop("steps_streamed", False):
        return
    total = sum(max(step.seconds, MIN_STEP_SECONDS) for step in result.steps)
    with st.expander(
        f"Steps completed — {len(result.steps)} · {total:.1f}s total",
        expanded=False,
    ):
        for step in result.steps:
            st.markdown(_step_line(step), unsafe_allow_html=True)


def _render_result() -> None:
    result: AnalysisResult | None = st.session_state.result
    if result is not None:
        _render_steps(result)
    if st.session_state.error and (result is None or result.error):
        st.error(st.session_state.error)
        return
    if result is None:
        return
    if result.needs_clarification:
        _render_clarification(result)
        return
    if result.out_of_scope:
        st.info(result.explanation)
        return

    _section("Answer")
    st.markdown(
        f"<div class='answer-card'><div class='answer'><span class='ok'>✓</span>"
        f"{result.explanation}</div></div>",
        unsafe_allow_html=True,
    )

    if st.session_state.chart is not None:
        st.plotly_chart(st.session_state.chart, width="stretch")

    frame = st.session_state.result_frame
    if frame is not None and not frame.empty:
        _section("Result summary")
        st.dataframe(frame, hide_index=True, width="stretch")

    if result.provenance:
        prov = result.provenance
        _section("Analysis details")
        files = ", ".join(prov.files) if prov.files else "—"
        columns = ", ".join(prov.columns) if prov.columns else "—"
        st.markdown(
            f"<div class='prov-card'>"
            f"<div class='prov-item'><div class='prov-label'>Data used</div>"
            f"<div class='prov-value'>{files}</div></div>"
            f"<div class='prov-item'><div class='prov-label'>Key columns</div>"
            f"<div class='prov-value'>{columns}</div></div>"
            f"<div class='prov-item'><div class='prov-label'>Calculation</div>"
            f"<div class='prov-value'>{prov.calculation}</div></div>"
            f"<div class='prov-item'><div class='prov-label'>Rows analyzed</div>"
            f"<div class='prov-value'>{prov.rows_analyzed:,}</div></div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def main() -> None:
    st.set_page_config(page_title="DataPilot", page_icon="📊", layout="centered")
    hydrate_streamlit_secrets()
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    _init_state()
    _render_header()
    _render_uploads()
    _render_examples()
    _render_ask()
    _render_result()


if __name__ == "__main__":
    main()

"""
verify_fix.py — run this from the DataPilot project root to prove the latency
fix works, with real timing numbers from YOUR machine and YOUR API key.

Usage:
    cd DataPilot
    pip install -r requirements.txt
    python verify_fix.py

What it does:
    1. Loads the bundled sample data (employees.csv + performance.csv).
    2. Asks the same question twice through the real pipeline:
       - once with AI narration OFF (the new default)
       - once with AI narration ON (the old behavior: 2 LLM calls)
    3. Prints wall-clock time for each, plus the actual answer, so you can see
       both that it's faster AND that the deterministic explanation is still
       correct.

This makes real network calls to whatever provider is configured in your
.env (OPENROUTER_API_KEY / MODEL / OPENROUTER_BASE_URL), so you'll see your
actual production latency, not a simulation.
"""

import time

import pandas as pd

from core.llm import LLMClient
from core.planner import run_analysis
from utils.file_loader import register_uploads

QUESTION = "What is the average performance score by department?"


def load_sample_tables():
    files = []
    for name in ("sample_data/employees.csv", "sample_data/performance.csv"):
        with open(name, "rb") as f:
            files.append((name.split("/")[-1], f.read()))
    return register_uploads(files)


def timed_run(label: str, narrate: bool, tables, catalog, llm) -> None:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    start = time.perf_counter()
    result, frame, _fig = run_analysis(
        question=QUESTION,
        catalog=catalog,
        tables=tables,
        llm=llm,
        narrate=narrate,
    )
    elapsed = time.perf_counter() - start

    print(f"Elapsed: {elapsed:.2f}s")
    if result.error:
        print(f"ERROR: {result.error}")
        return
    print(f"Answer: {result.explanation}")
    if frame is not None:
        print(f"\nResult rows:\n{frame.to_string(index=False)}")


def main() -> None:
    print(f"Question: {QUESTION!r}")
    tables, catalog = load_sample_tables()
    llm = LLMClient()

    timed_run("Run 1 — AI_NARRATION OFF (new default, 1 LLM call)", False, tables, catalog, llm)
    timed_run("Run 2 — AI_NARRATION ON (old behavior, 2 LLM calls)", True, tables, catalog, llm)

    print(
        "\nCompare the two 'Elapsed' numbers above. Run 2 should be roughly "
        "double Run 1, since it makes an extra LLM round trip purely to "
        "reword the same numbers Run 1 already computed correctly."
    )


if __name__ == "__main__":
    main()

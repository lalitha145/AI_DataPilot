# DataPilot

**Upload your data. Ask questions. Get insights.**

Live demo: **[https://ai-datapilot.streamlit.app/](https://ai-datapilot.streamlit.app/)**

DataPilot lets you upload CSV/Excel files and ask analytical questions in plain English. Answers are **computed from your data with DuckDB** — the LLM only plans the query, it does not invent numbers.

---

## Try it now

1. Open the live app: [https://ai-datapilot.streamlit.app/](https://ai-datapilot.streamlit.app/)
2. Upload one or more CSV / Excel files (or use the sample files from `sample_data/`)
3. Ask a question or click an example
4. Review the answer, chart (if useful), result table, and analysis details

---

## What it does

| Step | What happens |
|------|----------------|
| 1. Upload | Files become in-memory DuckDB tables; schema is extracted |
| 2. Ask | You type a business question in plain English |
| 3. Plan | The LLM returns a structured `AnalysisPlan` (not SQL, not numbers) |
| 4. Validate | Tables, columns, joins, and aggregations are checked |
| 5. Compute | Python builds SQL → DuckDB runs it (source of truth) |
| 6. Explain | A short answer + optional chart + provenance |

**Rule:** LLM proposes → code validates → DuckDB computes → UI explains.

---

## Key features

- Multi-file CSV / Excel upload in one session
- Schema catalog for the model (types, samples) — full data stays local
- Structured planning with Pydantic (`AnalysisPlan`)
- Deterministic SQL + read-only DuckDB execution
- Cross-file joins when a question spans datasets
- Ambiguity prompts instead of silent guessing
- Out-of-scope guardrails for chit-chat / unrelated questions
- Live step progress while analysis runs
- Question-driven Plotly charts
- Provenance: files used, columns, calculation, rows analyzed

---

## Architecture

```
User → Streamlit UI → Question
         → LLM planner (OpenRouter) → AnalysisPlan
         → Validators → SQL builder → DuckDB
         → Chart + explanation → Answer
```

```mermaid
flowchart TD
    A[User question + uploaded files] --> B[Schema catalog]
    B --> C[LLM planner]
    C --> D[AnalysisPlan JSON]
    D --> E[Validate tables/columns/joins]
    E --> F[Python SQL builder]
    F --> G[Read-only SQL gate]
    G --> H[DuckDB]
    H --> I[Result frame]
    I --> J[Plotly chart if useful]
    I --> K[LLM explanation from compact result]
    J --> L[UI answer + provenance]
    K --> L
```

---

## Tech stack

- Python 3.11+
- Streamlit
- Pandas, OpenPyXL, DuckDB
- Plotly, Pydantic
- OpenRouter (default: `deepseek/deepseek-v4-flash-0731:free`)

---

## Local setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Environment variables

Copy `.env.example` to `.env` (or create `.env`) and set:

```bash
OPENROUTER_API_KEY=your_openrouter_api_key_here
MODEL=deepseek/deepseek-v4-flash-0731:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
APP_TITLE=DataPilot
MAX_RESULT_ROWS=50
LLM_TIMEOUT_SECONDS=60
AI_NARRATION=true
```

Never commit `.env`. On Streamlit Cloud, set the same keys under **Manage app → Settings → Secrets** (TOML format).

### Run

```bash
streamlit run app.py
```

Sample data: `sample_data/`. Regenerate with:

```bash
python scripts/generate_sample_data.py
```

---

## Example questions

1. Which department has the highest average performance score?
2. What is the average attendance by department?
3. Which location has the highest number of employees?
4. How did average performance change over time?
5. Compare attendance between Bangalore and Hyderabad.
6. Which department has the lowest attendance?
7. Does the department with the lowest attendance also have the lowest performance?

---

## Testing

```bash
pytest
```

Covers file loading, plan parsing, validation failures, dangerous SQL rejection, cross-file queries, empty results, and malformed LLM output.

---

## Known limitations

- Best for tidy spreadsheet-style tables (messy merged Excel headers are out of scope)
- Join inference is name/type based, not a full schema graph
- Large files are held in memory (no chunked compute yet)
- Free OpenRouter models can be slow or rate-limited under load

---

## Future improvements

DataPilot already answers questions from uploaded spreadsheets. These are the next steps to make it faster, safer, and easier for everyone — written in plain English.

### Make answers more trustworthy
- Use a paid AI model so the app is less likely to slow down or fail when many people use it
- Add extra tests that replay the same questions on sample files, so a code change cannot silently give a wrong number
- When two files are combined, show how they were joined and let the user confirm before running
- If a result is cut short (too many rows), tell the user clearly instead of hiding it

### Keep data safe
- Keep each user's uploaded files private to their own session
- Store API keys only in the host's secret settings — never in the code or in logs
- Keep a simple activity log of questions asked, without saving personal details by default
- Keep queries read-only (view data only; never change or delete it)

### Handle bigger files
- Set a file-size limit and show a friendly message when a file is too large
- Load large CSVs in parts instead of holding everything in memory at once
- Optionally save data in a database so it does not disappear when the session ends
- Remember recent questions for the same files so repeat asks are faster
- For slow questions, keep working in the background and show progress

### Make the product nicer to use
- Let people save favorite questions and reuse them later
- Export the answer, table, and chart (CSV, image, or PDF)
- Remember the last question so follow-ups like “now by location” work
- For teams: simple roles such as “can view” vs “can analyze”

### Watch how the app is doing
- Track how often answers succeed, how long they take, and why they fail
- Show whether the AI service is up
- Test changes on a staging copy before updating the live app
- Alert the team if many analyses start failing

### Improve data quality
- On upload, warn about missing values, duplicate IDs, or mixed column types
- Support common business calcs (this month vs last month) in code — not by guessing numbers
- Let teams define official meanings for terms like “revenue” or “attendance rate” so everyone gets the same calculation

---

## Repository

GitHub: [https://github.com/lalitha145/AI_DataPilot](https://github.com/lalitha145/AI_DataPilot)

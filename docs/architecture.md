# How DataPilot is built

DataPilot has a React frontend and a FastAPI backend. Vite sends `/api` requests from the frontend to FastAPI while developing locally. DuckDB stores the uploaded data, and LangChain provides structured model responses for planning, SQL generation, and the final answer. The model client can use OpenAI or a local Ollama server through its OpenAI-compatible endpoint.

## From upload to dataset page

1. `ingestion/loader.py` reads a CSV file or each sheet in an XLSX workbook and gives tables and columns safe SQL names.
2. `database/engine.py` saves the tables in a DuckDB file for that upload.
3. `ingestion/profiler.py` saves a profile with column types, missing values, distinct counts, and a few sample values.
4. `workspace/manager.py` keeps the file, database, and profile together under one temporary workspace ID.
5. The frontend uses the profile and a paginated preview to show the Dataset page.

## From question to answer

```text
Question + optional dataset note
  → review the saved columns
  → description question: answer from the saved profile
  → analytical question:
      plan up to three checks
      → generate, validate, and run SQL with bounded retries
      → write an answer from the returned rows
```

The steps live in `analysis/workflow.py`. `analysis/model.py` selects the configured provider, defines structured response types, and validates each model response with Pydantic. Description questions can be answered directly from the saved profile, with references to existing columns and no SQL execution. Questions that need statistics follow the query steps. `analysis/schema.py` gives the model the saved column profile before planning. `analysis/sql_safety.py` checks each proposed query with SQLGlot, and `analysis/execute.py` runs accepted queries in DuckDB.

The JSON and streaming analysis endpoints use the same workflow. Streaming events let the question page show planning, validation, retries, and query results as they happen. The report includes the executed SQL and supporting rows. The backend checks numeric statements in the generated answer against those rows and uses a direct result description if that check fails.

## Frontend files

- `src/App.tsx` sets up the page shell.
- `src/hooks/useDataset.ts` manages the upload, selected sheet, and preview.
- `src/components/workspace/DatasetWorkspace.tsx` manages the Dataset page and saved question pages.
- `src/components/analysis/QuestionPage.tsx` shows an answer, its supporting results, SQL, and activity log.
- `src/lib/api.ts` handles API calls and checks responses with Zod schemas from `src/types/dataset.ts`.

The browser keeps question pages and the optional dataset note in session storage. The backend workspace expires after 60 minutes without access.

## Query limits and isolation

SQLGlot allows one read-only `SELECT` against the selected workspace table. DuckDB opens the workspace database in read-only mode with external access and extension loading disabled. The backend sets a query timeout and caps rows and response bytes. Each upload has its own local workspace directory and opaque ID.

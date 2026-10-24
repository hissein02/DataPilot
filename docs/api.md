# Dataset API

The API is served by FastAPI at `http://localhost:8000`. OpenAPI docs are available at `/docs` when the backend runs.

## Current endpoints

- `GET /api/health` returns service status.
- `POST /api/datasets` accepts multipart form field `file` containing `.csv` or `.xlsx`. It returns an opaque workspace ID, source filename, all table profiles, active table, and upload duration in milliseconds.
- `GET /api/datasets/{workspace_id}` returns the same stored metadata and refreshes expiry.
- `GET /api/datasets/{workspace_id}/preview?table=<name>&offset=0&limit=20` returns at most 100 rows with total row count. `table` defaults to the first sheet.
- `DELETE /api/datasets/{workspace_id}` removes the upload and DuckDB database.

Upload validation errors have the form `{"error":"message"}`. Invalid or expired workspace IDs return 404. Requests with invalid pagination values return FastAPI's validation error response.

Workspaces expire after 60 minutes without access by default. Cleanup runs on access and every five minutes while the service is running. The ID is a bearer capability; keep it private.

Excel is read in data-only mode. Formula code is never evaluated, and formula cells with no cached result appear blank.

## Analysis

`POST /api/analysis` accepts JSON containing `workspace_id`, `question`, an optional `table` name, and optional `dataset_context` (up to 4,000 characters). Context is passed to planning, SQL generation, and reporting as unverified background. It returns an answer, optional limitation, public analysis plan and activity log, every executed query with its supporting rows, and timing. The top-level `sql`, `columns`, and `rows` fields mirror the first successful query for compatibility. Set `LLM_PROVIDER=openai` with `OPENAI_API_KEY` and optional `ANALYSIS_MODEL`, or set `LLM_PROVIDER=ollama` with `OLLAMA_MODEL` and optional `OLLAMA_BASE_URL`, in the root `.env`. Missing model configuration returns 503; an unavailable model or invalid structured response returns 502. Invalid or unsafe SQL is rejected before execution and retried within the configured limits. Exhausted steps appear as limitations in the report.

`POST /api/analysis/stream` accepts the same request and emits Server-Sent Events containing JSON objects with `type` values `progress`, `result`, `error`, and `done`. The `result.report` schema matches the JSON endpoint. The first progress event reviews column names and types from the profile saved at upload; planning then uses that profile without a schema-discovery SQL query. Later events show planning, validation, retries, and executions.

The workflow plans at most three steps, tries SQL at most three times per step, and executes at most five queries. Each query is a single SELECT against the selected workspace table. Results are capped at 100 rows and 100 KB by default. Queries run on read-only DuckDB connections with external access and extension autoloading disabled, plus a 10-second interrupt timer. This is intended for local or trusted use; process isolation is still needed before public deployment.

Questions about the dataset's subject or structure can use the profile saved at upload. These answers return the same report format with `sql: null`, an empty `queries` list, and `execution_count: 0`. The planner's supporting column references are checked against the uploaded schema.

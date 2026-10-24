import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis import model as analysis_model
from app.analysis.model import (
    AnalysisPlan,
    AnalysisStep,
    AnalysisSummary,
    Finding,
    ProfileAnswer,
    QueryProposal,
)
from app.analysis.schema import schema_context, validate_measure_roles
from app.analysis.sql_safety import UnsafeQuery, validate_sql
from app.analysis.workflow import _plan, analysis_events
from app.core.config import get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def mock_result_summary(monkeypatch):
    # These tests exercise workflow behavior without a network or paid model call.
    monkeypatch.setattr(
        "app.analysis.workflow.summarize_results",
        lambda question, table, results, dataset_context="": AnalysisSummary(
            findings=[
                Finding(query_index=1, observation="Results are shown in the supporting table.")
            ]
        ),
    )


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT * FROM orders; DROP TABLE orders",
        "SELECT * FROM other_table",
        "SELECT * FROM main.orders",
        "SELECT * FROM read_csv('/tmp/secrets.csv')",
        "SELECT read_text('/tmp/secrets') FROM orders",
        "SELECT * FROM orders WHERE quantity IN (SELECT quantity FROM other_table)",
        "WITH x AS (SELECT * FROM other_table) SELECT * FROM x",
        "SELECT * FROM 'private.csv'",
        "SELECT * INTO copied FROM orders",
        "SELECT * FROM orders LIMIT 0",
        "SELECT * FROM (SELECT * FROM orders LIMIT 0)",
    ],
)
def test_rejects_unsafe_sql(sql: str) -> None:
    with pytest.raises(UnsafeQuery):
        validate_sql(sql, {"orders"})


def test_accepts_bounded_analytical_select() -> None:
    sql = "WITH totals AS (SELECT item, SUM(quantity) AS total FROM orders GROUP BY item) SELECT * FROM totals"
    assert "SUM" in validate_sql(sql, {"orders"})
    assert "MODE" in validate_sql("SELECT MODE(quantity) FROM orders", {"orders"})


def test_schema_inspection_precedes_planning_without_sql(monkeypatch, tmp_path) -> None:
    table = {
        "name": "orders",
        "row_count": 2,
        "columns": [
            {"name": "item", "display_name": "Item", "type": "text"},
            {"name": "quantity", "display_name": "Quantity", "type": "integer"},
        ],
    }
    planned = []

    def plan(question, table, feedback=None, dataset_context=""):
        planned.append(True)
        return AnalysisPlan(steps=[], limitation="No query needed.")

    monkeypatch.setattr("app.analysis.workflow.plan_analysis", plan)
    events = analysis_events("What can you tell me?", table, tmp_path / "missing.duckdb")
    first = next(events)
    assert first["step"] == "inspect_schema"
    assert "item (text)" in first["message"]
    assert "quantity (integer)" in first["message"]
    assert planned == []
    remaining = list(events)
    assert planned == [True]
    report = next(event["report"] for event in remaining if event["type"] == "result")
    assert report["execution_count"] == 0
    assert report["trace"][0]["step"] == "inspect_schema"


def test_planning_context_uses_uploaded_column_profile() -> None:
    table = {
        "name": "orders",
        "row_count": 2,
        "columns": [
            {
                "name": "item",
                "display_name": "Item",
                "type": "text",
                "distinct_count": 2,
                "null_percentage": 0,
                "sample_values": ["Pen", "Book"],
            }
        ],
    }
    profile = json.loads(schema_context(table))
    assert profile["columns"][0]["sample_values"] == ["Pen", "Book"]
    assert profile["columns"][0]["type"] == "text"


@pytest.mark.parametrize(
    "data, question, description, columns",
    [
        (
            b"item,quantity\nPen,2\nBook,3\n",
            "What is this dataset about?",
            "This table lists items and their quantities.",
            ["item", "quantity"],
        ),
        (
            b"station,recorded_at,temperature\nA,2026-01-01,12\nB,2026-01-02,15\n",
            "Give an overview of what this file contains.",
            "This table contains temperature observations with a station and recording date.",
            ["station", "recorded_at", "temperature"],
        ),
    ],
)
def test_dataset_description_uses_profile_without_sql(
    monkeypatch, tmp_path, data, question, description, columns
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()

    def plan(question, table, feedback=None, dataset_context=""):
        assert table["columns"][0]["sample_values"]
        assert dataset_context == "Context supplied for this upload."
        return AnalysisPlan(
            steps=[], profile_answer=ProfileAnswer(answer=description, columns=columns)
        )

    def unexpected_query(*args, **kwargs):
        pytest.fail("A dataset description should use the uploaded profile.")

    monkeypatch.setattr("app.analysis.workflow.plan_analysis", plan)
    monkeypatch.setattr("app.analysis.workflow.propose_query", unexpected_query)
    monkeypatch.setattr("app.analysis.workflow.execute_query", unexpected_query)
    monkeypatch.setattr("app.analysis.workflow.summarize_results", unexpected_query)
    try:
        with TestClient(create_app()) as client:
            upload = client.post("/api/datasets", files={"file": ("example.csv", data)})
            payload = {
                "workspace_id": upload.json()["workspace_id"],
                "question": question,
                "dataset_context": "Context supplied for this upload.",
            }
            response = client.post("/api/analysis", json=payload)
            assert response.status_code == 200
            report = response.json()
            assert report["answer"] == description
            assert report["limitation"] is None
            assert report["execution_count"] == 0
            assert report["queries"] == []
            assert report["sql"] is None
            assert [event["step"] for event in report["trace"]] == [
                "inspect_schema",
                "plan",
                "report",
            ]
            streamed = client.post("/api/analysis/stream", json=payload)
            events = [
                json.loads(line.removeprefix("data: "))
                for line in streamed.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[-2]["report"]["answer"] == description
            assert events[-1]["type"] == "done"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("problem", ["unknown_column", "mixed_plan"])
def test_profile_answer_retries_invalid_column_evidence_or_mixed_plan(monkeypatch, problem) -> None:
    state = {
        "question": "What does this file contain?",
        "table": {"columns": [{"name": "item"}]},
        "trace": [],
        "dataset_context": "",
    }
    feedback_seen = []

    def plan(question, table, feedback=None, dataset_context=""):
        feedback_seen.append(feedback)
        steps = (
            [AnalysisStep(purpose="Count items", question="How many items?")]
            if feedback is None and problem == "mixed_plan"
            else []
        )
        columns = ["invented"] if feedback is None and problem == "unknown_column" else ["item"]
        return AnalysisPlan(
            steps=steps,
            profile_answer=ProfileAnswer(answer="This table contains items.", columns=columns),
        )

    monkeypatch.setattr("app.analysis.workflow.plan_analysis", plan)
    planned = _plan(state)
    assert feedback_seen[1]
    assert planned["profile_answer"] == "This table contains items."
    assert planned["steps"] == []
    assert planned["limitation"] is None
    assert [event["status"] for event in planned["trace"]] == ["retry", "complete"]


def test_analysis_uses_uploaded_rows(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.analysis.workflow.plan_analysis",
        lambda question, table, feedback=None, dataset_context="": AnalysisPlan(
            steps=[AnalysisStep(purpose="Total quantity", question=question)]
        ),
    )
    monkeypatch.setattr(
        "app.analysis.workflow.propose_query",
        lambda question, table, feedback=None, dataset_context="": QueryProposal(
            sql="SELECT SUM(quantity) AS total FROM orders", limitation=None
        ),
    )
    try:
        with TestClient(create_app()) as client:
            upload = client.post(
                "/api/datasets",
                files={"file": ("orders.csv", b"item,quantity\nPen,2\nBook,3\n", "text/csv")},
            )
            assert upload.status_code == 200
            workspace_id = upload.json()["workspace_id"]
            response = client.post(
                "/api/analysis",
                json={"workspace_id": workspace_id, "question": "How many items?"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["rows"] == [{"total": 5}]
            assert "supporting table" in body["answer"]
            assert body["sql"] == "SELECT SUM(quantity) AS total FROM orders"
            assert body["execution_count"] == 1
    finally:
        get_settings.cache_clear()


def test_missing_credentials_is_clear(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            upload = client.post("/api/datasets", files={"file": ("orders.csv", b"quantity\n2\n")})
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Total?"},
            )
            assert response.status_code == 503
            assert "OPENAI_API_KEY" in response.json()["detail"]
    finally:
        get_settings.cache_clear()


def test_ollama_runs_through_structured_outputs_and_sql_validation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    model_calls = []

    class FakeChatModel:
        def __init__(self, **options):
            model_calls.append({"client": options})

        def with_structured_output(self, schema, **options):
            model_calls[-1].update({"schema": schema, "format": options})
            self.schema = schema
            return self

        def invoke(self, prompt):
            if self.schema is AnalysisPlan:
                return {"steps": [{"purpose": "Count rows", "question": "How many rows?"}]}
            if self.schema is QueryProposal:
                sql = (
                    "SELECT COUNT(*) AS total FROM orders"
                    if "Previous attempt failed" in prompt
                    else "SELECT * FROM other_table"
                )
                return {"sql": sql, "limitation": None}
            return {"findings": [{"query_index": 1, "observation": "There are 2 rows."}]}

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatModel)
    monkeypatch.setattr("app.analysis.workflow.summarize_results", analysis_model.summarize_results)
    try:
        with TestClient(create_app()) as client:
            upload = client.post(
                "/api/datasets", files={"file": ("orders.csv", b"quantity\n2\n3\n")}
            )
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Count rows"},
            )
            assert response.status_code == 200
            assert response.json()["answer"] == "There are 2 rows."
            assert response.json()["execution_count"] == 1
            assert any(event["status"] == "retry" for event in response.json()["trace"])
            assert [call["schema"] for call in model_calls] == [
                AnalysisPlan,
                QueryProposal,
                QueryProposal,
                AnalysisSummary,
            ]
            assert all(call["format"] == {"method": "json_schema"} for call in model_calls)
            assert all(call["client"]["model"] == "local-test-model" for call in model_calls)
            assert all(call["client"]["api_key"] == "ollama" for call in model_calls)
            assert all(call["client"]["timeout"] == 120 for call in model_calls)
            assert all(
                call["client"]["base_url"] == "http://localhost:11434/v1" for call in model_calls
            )
    finally:
        get_settings.cache_clear()


def test_ollama_compatible_client_parses_json_schema_response() -> None:
    """Check the actual LangChain client without needing a running model server."""
    from langchain_openai import ChatOpenAI

    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-local-test",
                "object": "chat.completion",
                "created": 1,
                "model": "local-test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"steps":[{"purpose":"Count rows","question":"How many rows?"}],"limitation":null}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    model = ChatOpenAI(
        model="local-test-model",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        http_client=client,
    )
    plan = model.with_structured_output(AnalysisPlan, method="json_schema").invoke("Count rows")
    assert isinstance(plan, AnalysisPlan)
    assert plan.steps[0].purpose == "Count rows"
    assert requests[0]["response_format"]["type"] == "json_schema"


def test_ollama_response_must_match_the_plan_schema(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    get_settings.cache_clear()

    class BadResponse:
        def with_structured_output(self, schema, **options):
            return self

        def invoke(self, prompt):
            return {"steps": "invalid"}

    monkeypatch.setattr(analysis_model, "_model", lambda: BadResponse())
    try:
        with pytest.raises(ValidationError):
            analysis_model.plan_analysis(
                "Count rows", {"name": "orders", "row_count": 1, "columns": []}
            )
        with TestClient(create_app()) as client:
            upload = client.post("/api/datasets", files={"file": ("orders.csv", b"quantity\n2\n")})
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Count rows"},
            )
            assert response.status_code == 502
            assert "invalid response" in response.json()["detail"]
            streamed = client.post(
                "/api/analysis/stream",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Count rows"},
            )
            events = [
                json.loads(line.removeprefix("data: "))
                for line in streamed.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[-2]["type"] == "error"
            assert events[-1]["type"] == "done"
    finally:
        get_settings.cache_clear()


def test_ollama_requires_a_model_name(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            upload = client.post("/api/datasets", files={"file": ("orders.csv", b"quantity\n2\n")})
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Count rows"},
            )
            assert response.status_code == 503
            assert "OLLAMA_MODEL" in response.json()["detail"]
    finally:
        get_settings.cache_clear()


def test_api_rejects_model_sql_for_other_table(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.analysis.workflow.plan_analysis",
        lambda question, table, feedback=None, dataset_context="": AnalysisPlan(
            steps=[AnalysisStep(purpose="Inspect data", question=question)]
        ),
    )
    monkeypatch.setattr(
        "app.analysis.workflow.propose_query",
        lambda question, table, feedback=None, dataset_context="": QueryProposal(
            sql="SELECT * FROM secret", limitation=None
        ),
    )
    try:
        with TestClient(create_app()) as client:
            upload = client.post("/api/datasets", files={"file": ("orders.csv", b"quantity\n2\n")})
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Show data"},
            )
            assert response.status_code == 200
            assert response.json()["execution_count"] == 0
            assert "workspace" in response.json()["limitation"]
            assert (
                len(
                    [event for event in response.json()["trace"] if event["step"] == "validate_sql"]
                )
                == 3
            )
    finally:
        get_settings.cache_clear()


def test_broad_question_runs_multiple_queries_and_streams_progress(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.analysis.workflow.plan_analysis",
        lambda question, table, feedback=None, dataset_context="": AnalysisPlan(
            steps=[
                AnalysisStep(
                    purpose="Quantity summary",
                    question="How many rows and what is average quantity?",
                    columns=["quantity"],
                ),
                AnalysisStep(
                    purpose="Items by frequency",
                    question="Which items occur most often?",
                    columns=["item"],
                ),
            ]
        ),
    )

    def propose(question, table, feedback=None, dataset_context=""):

        sql = (
            "SELECT COUNT(*) AS records, AVG(quantity) AS average_quantity FROM orders"
            if "average quantity" in question
            else "SELECT item AS category, COUNT(*) AS records FROM orders GROUP BY item ORDER BY records DESC, category LIMIT 100"
        )
        return QueryProposal(sql=sql, limitation=None)

    monkeypatch.setattr("app.analysis.workflow.propose_query", propose)
    try:
        with TestClient(create_app()) as client:
            upload = client.post(
                "/api/datasets",
                files={"file": ("orders.csv", b"item,quantity\nPen,2\nBook,3\nPen,1\n")},
            )
            payload = {
                "workspace_id": upload.json()["workspace_id"],
                "question": "Analyse the dataset",
            }
            response = client.post("/api/analysis", json=payload)
            assert response.status_code == 200
            report = response.json()
            assert report["execution_count"] == 2
            assert report["queries"][0]["rows"] == [{"records": 3, "average_quantity": 2.0}]
            assert report["queries"][1]["rows"][0] == {"category": "Pen", "records": 2}
            streamed = client.post("/api/analysis/stream", json=payload)
            events = [
                json.loads(line.removeprefix("data: "))
                for line in streamed.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[0]["step"] == "inspect_schema"
            assert "item (text)" in events[0]["message"]
            assert events[-1]["type"] == "done"
            assert [event["type"] for event in events].count("result") == 1
            assert any(event.get("step") == "execute_sql" for event in events)
            assert [query["rows"] for query in events[-2]["report"]["queries"]] == [
                query["rows"] for query in report["queries"]
            ]
    finally:
        get_settings.cache_clear()


def test_retries_invalid_function_before_execution(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.analysis.workflow.plan_analysis",
        lambda question, table, feedback=None, dataset_context="": AnalysisPlan(
            steps=[AnalysisStep(purpose="Count rows", question=question)]
        ),
    )
    feedback_seen = []

    def propose(question, table, feedback=None, dataset_context=""):

        feedback_seen.append(feedback)
        return QueryProposal(
            sql="SELECT READ_TEXT('secret') FROM orders"
            if feedback is None
            else "SELECT COUNT(*) AS total FROM orders",
            limitation=None,
        )

    monkeypatch.setattr("app.analysis.workflow.propose_query", propose)
    try:
        with TestClient(create_app()) as client:
            upload = client.post("/api/datasets", files={"file": ("orders.csv", b"quantity\n2\n")})
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Count rows"},
            )
            assert response.status_code == 200
            assert response.json()["rows"] == [{"total": 1}]
            assert response.json()["execution_count"] == 1
            assert len(feedback_seen) == 2
            assert "READ_TEXT" in feedback_seen[1]
            assert any(event["status"] == "retry" for event in response.json()["trace"])
    finally:
        get_settings.cache_clear()


def test_retries_execution_error_with_feedback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.analysis.workflow.plan_analysis",
        lambda question, table, feedback=None, dataset_context="": AnalysisPlan(
            steps=[AnalysisStep(purpose="Count rows", question=question)]
        ),
    )
    feedback_seen = []

    def propose(question, table, feedback=None, dataset_context=""):

        feedback_seen.append(feedback)
        return QueryProposal(
            sql="SELECT missing_column FROM orders"
            if feedback is None
            else "SELECT COUNT(*) AS total FROM orders",
            limitation=None,
        )

    monkeypatch.setattr("app.analysis.workflow.propose_query", propose)
    try:
        with TestClient(create_app()) as client:
            upload = client.post("/api/datasets", files={"file": ("orders.csv", b"quantity\n2\n")})
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Count rows"},
            )
            assert response.status_code == 200
            assert response.json()["rows"] == [{"total": 1}]
            assert response.json()["execution_count"] == 2
            assert "missing_column" in feedback_seen[1]
    finally:
        get_settings.cache_clear()


def test_mode_query_executes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.analysis.workflow.plan_analysis",
        lambda question, table, feedback=None, dataset_context="": AnalysisPlan(
            steps=[AnalysisStep(purpose="Most common category", question=question)]
        ),
    )
    monkeypatch.setattr(
        "app.analysis.workflow.propose_query",
        lambda question, table, feedback=None, dataset_context="": QueryProposal(
            sql="SELECT MODE(category) AS top_category FROM orders", limitation=None
        ),
    )
    try:
        with TestClient(create_app()) as client:
            upload = client.post(
                "/api/datasets", files={"file": ("orders.csv", b"category\nA\nA\nB\n")}
            )
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Top category?"},
            )
            assert response.status_code == 200
            assert response.json()["rows"] == [{"top_category": "A"}]
            assert response.json()["execution_count"] == 1
    finally:
        get_settings.cache_clear()


def test_broad_plan_retries_incomplete_steps(monkeypatch) -> None:
    state = {
        "question": "Analyse the dataset. What insights stand out?",
        "table": {
            "name": "orders",
            "row_count": 3,
            "columns": [
                {
                    "name": "order_id",
                    "display_name": "Order ID",
                    "type": "integer",
                    "distinct_count": 3,
                },
                {
                    "name": "quantity",
                    "display_name": "Quantity",
                    "type": "integer",
                    "distinct_count": 2,
                },
                {"name": "item", "display_name": "Item", "type": "text", "distinct_count": 2},
            ],
        },
        "trace": [],
        "dataset_context": "",
    }
    feedback_seen = []

    def plan(question, table, feedback=None, dataset_context=""):

        feedback_seen.append(feedback)
        if feedback is None:
            return AnalysisPlan(
                steps=[AnalysisStep(purpose="Overview", question="SELECT COUNT(*) AS total,")]
            )
        return AnalysisPlan(
            steps=[
                AnalysisStep(
                    purpose="Quantity",
                    question="What is the typical quantity?",
                    columns=["quantity"],
                ),
                AnalysisStep(purpose="Items", question="Which items are common?", columns=["item"]),
            ]
        )

    monkeypatch.setattr("app.analysis.workflow.plan_analysis", plan)
    planned = _plan(state)
    assert len(planned["steps"]) == 2
    assert "incomplete" in feedback_seen[1]
    assert [event["status"] for event in planned["trace"]] == ["retry", "complete"]


def test_dataset_context_reaches_every_model_stage(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    received = []

    def plan(question, table, feedback=None, dataset_context=""):
        received.append(("plan", dataset_context))
        return AnalysisPlan(steps=[AnalysisStep(purpose="Count rows", question="How many rows?")])

    def propose(question, table, feedback=None, dataset_context=""):
        received.append(("query", dataset_context))
        return QueryProposal(sql="SELECT COUNT(*) AS total FROM orders", limitation=None)

    def summarize(question, table, results, dataset_context=""):
        received.append(("summary", dataset_context))
        return AnalysisSummary(
            findings=[Finding(query_index=1, observation="The query returned 2 rows.")]
        )

    monkeypatch.setattr("app.analysis.workflow.plan_analysis", plan)
    monkeypatch.setattr("app.analysis.workflow.propose_query", propose)
    monkeypatch.setattr("app.analysis.workflow.summarize_results", summarize)
    try:
        with TestClient(create_app()) as client:
            upload = client.post(
                "/api/datasets", files={"file": ("orders.csv", b"item\nPen\nBook\n")}
            )
            response = client.post(
                "/api/analysis",
                json={
                    "workspace_id": upload.json()["workspace_id"],
                    "question": "How many rows?",
                    "dataset_context": "Each row is one order.",
                },
            )
            assert response.status_code == 200
            assert received == [
                ("plan", "Each row is one order."),
                ("query", "Each row is one order."),
                ("summary", "Each row is one order."),
            ]
    finally:
        get_settings.cache_clear()


def test_identifier_is_not_a_measurement() -> None:
    table = {
        "columns": [
            {"name": "order_id", "display_name": "Order ID", "type": "integer"},
            {"name": "amount", "display_name": "Amount", "type": "double"},
        ]
    }
    with pytest.raises(UnsafeQuery, match="identifier"):
        validate_measure_roles("SELECT AVG(order_id) FROM orders", table)
    with pytest.raises(UnsafeQuery, match="identifier"):
        validate_measure_roles("SELECT MIN(order_id), MAX(order_id) FROM orders", table)
    validate_measure_roles("SELECT COUNT(order_id), AVG(amount) FROM orders", table)


def test_identifier_measurement_is_retried_with_model_feedback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.analysis.workflow.plan_analysis",
        lambda question, table, feedback=None, dataset_context="": AnalysisPlan(
            steps=[AnalysisStep(purpose="Order overview", question="How many orders are there?")]
        ),
    )
    feedback_seen = []

    def propose(question, table, feedback=None, dataset_context=""):

        feedback_seen.append(feedback)
        sql = (
            "SELECT AVG(order_id) AS average_id FROM orders"
            if feedback is None
            else "SELECT COUNT(*) AS orders FROM orders"
        )
        return QueryProposal(sql=sql, limitation=None)

    monkeypatch.setattr("app.analysis.workflow.propose_query", propose)
    try:
        with TestClient(create_app()) as client:
            upload = client.post(
                "/api/datasets", files={"file": ("orders.csv", b"order_id,amount\n1,10\n2,20\n")}
            )
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Count orders"},
            )
            assert response.status_code == 200
            assert response.json()["rows"] == [{"orders": 2}]
            assert response.json()["execution_count"] == 1
            assert "identifier" in feedback_seen[1]
    finally:
        get_settings.cache_clear()


def test_unverified_summary_falls_back_to_query_rows(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.analysis.workflow.plan_analysis",
        lambda question, table, feedback=None, dataset_context="": AnalysisPlan(
            steps=[AnalysisStep(purpose="Order count", question="How many orders are there?")]
        ),
    )
    monkeypatch.setattr(
        "app.analysis.workflow.propose_query",
        lambda question, table, feedback=None, dataset_context="": QueryProposal(
            sql="SELECT COUNT(*) AS total FROM orders", limitation=None
        ),
    )
    monkeypatch.setattr(
        "app.analysis.workflow.summarize_results",
        lambda question, table, results, dataset_context="": AnalysisSummary(
            findings=[Finding(query_index=1, observation="There were 999 orders.")]
        ),
    )
    try:
        with TestClient(create_app()) as client:
            upload = client.post(
                "/api/datasets", files={"file": ("orders.csv", b"item\nPen\nBook\n")}
            )
            response = client.post(
                "/api/analysis",
                json={"workspace_id": upload.json()["workspace_id"], "question": "Count orders"},
            )
            assert response.status_code == 200
            assert "total: 2" in response.json()["answer"]
            assert "999" not in response.json()["answer"]
            assert response.json()["trace"][-1]["status"] == "fallback"
    finally:
        get_settings.cache_clear()

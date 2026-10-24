"""Bounded, inspectable analysis workflow for one uploaded table."""

import re
from pathlib import Path
from time import perf_counter
from typing import Any, TypedDict

from app.analysis.execute import execute_query
from app.analysis.model import ProfileAnswer, plan_analysis, propose_query, summarize_results
from app.analysis.schema import is_identifier, validate_measure_roles
from app.analysis.sql_safety import UnsafeQuery, validate_sql
from app.core.config import get_settings


class AnalysisState(TypedDict):
    """Values carried through one question's analysis steps."""

    question: str
    dataset_context: str
    table: dict[str, object]
    database_path: str
    steps: list[dict[str, Any]]
    step_index: int
    attempt: int
    execution_count: int
    current_sql: str | None
    feedback: str | None
    results: list[dict[str, object]]
    trace: list[dict[str, object]]
    limitation: str | None
    profile_answer: str | None
    report: dict[str, object] | None


def _trace(state: AnalysisState, step: str, status: str, message: str) -> list[dict[str, object]]:
    """Append a user-visible activity event without changing the old list."""
    return [*state["trace"], {"step": step, "status": status, "message": message}]


def _inspect_schema(state: AnalysisState) -> dict[str, object]:
    """Review the profile saved at upload before asking the model to plan."""
    table = state["table"]
    columns = table["columns"]
    shown = ", ".join(f"{column['name']} ({column['type']})" for column in columns[:12])
    remaining = len(columns) - min(len(columns), 12)
    suffix = f", and {remaining} more" if remaining else ""
    return {
        "trace": _trace(
            state,
            "inspect_schema",
            "complete",
            f"Reviewed {len(columns)} uploaded column(s): {shown}{suffix}.",
        )
    }


def _is_broad(question: str) -> bool:
    """Spot open-ended requests that need more than one distinct check."""
    return bool(
        re.search(
            r"\b(analy[sz]e|insights?|overview|explore|summari[sz]e)\b", question, re.IGNORECASE
        )
    )


def _plan_problem(
    steps: list[dict[str, object]],
    table: dict[str, object],
    broad: bool,
    profile_answer: ProfileAnswer | None = None,
) -> str | None:
    """Explain why a proposed plan needs correction, if it does."""
    available = {str(column["name"]).lower(): column for column in table["columns"]}
    if profile_answer:
        if not profile_answer.answer.strip():
            return "The profile answer must contain a description."
        if steps:
            return "Choose either a profile-based answer or SQL checks for this request."
        for name in profile_answer.columns:
            if name.lower() not in available:
                return f"The profile answer references unavailable column {name}."
        return None
    if not steps:
        return None
    seen: set[str] = set()
    for step in steps:
        purpose = str(step["purpose"]).strip()
        question = str(step["question"]).strip()
        if purpose.lower() in seen:
            return "The plan repeats the same check."
        seen.add(purpose.lower())
        if re.search(
            r"\bSELECT\b|\bFROM\b|\bAS\s+\w+", question, re.IGNORECASE
        ) or question.endswith(","):
            return f"{purpose} contains SQL or an incomplete question; write a complete natural-language question."
        for name in step.get("columns", []):
            if str(name).lower() not in available:
                return f"{purpose} references unavailable column {name}."
        if broad and any(
            is_identifier(available[str(name).lower()]) for name in step.get("columns", [])
        ):
            if re.search(
                r"\b(average|mean|sum|range|minimum|maximum|median|mode)\b", question, re.IGNORECASE
            ):
                return f"{purpose} treats an identifier as a measurement."
    if (
        broad
        and len([column for column in table["columns"] if not is_identifier(column)]) >= 2
        and len(steps) < 2
    ):
        return "An open-ended request needs at least two distinct, supported checks."
    return None


def _plan(state: AnalysisState) -> dict[str, object]:
    """Ask for a short plan and retry once when its steps are invalid."""
    feedback = None
    trace = state["trace"]
    steps: list[dict[str, object]] = []
    limitation = None
    profile_answer = None
    for attempt in range(1, 3):
        proposed = plan_analysis(
            state["question"], state["table"], feedback, state["dataset_context"]
        )
        steps = [step.model_dump() for step in proposed.steps[:3]]
        limitation = proposed.limitation
        problem = _plan_problem(
            steps, state["table"], _is_broad(state["question"]), proposed.profile_answer
        )
        if not problem:
            profile_answer = proposed.profile_answer.answer if proposed.profile_answer else None
            break
        trace = [
            *trace,
            {"step": "plan", "status": "retry" if attempt == 1 else "failed", "message": problem},
        ]
        feedback = problem
        steps = []
    if not steps and not profile_answer and not limitation:
        limitation = feedback or "The available columns do not support this question."
    message = (
        "Describe the dataset using its saved column profile."
        if profile_answer
        else (
            "; ".join(
                f"{step['purpose']}: {step['rationale']}"
                if step.get("rationale")
                else str(step["purpose"])
                for step in steps
            )
            if steps
            else "No supported analysis steps."
        )
    )
    return {
        "steps": steps,
        "limitation": limitation,
        "profile_answer": profile_answer,
        "trace": [
            *trace,
            {
                "step": "plan",
                "status": "complete" if steps or profile_answer else "failed",
                "message": message,
            },
        ],
    }


def _generate(state: AnalysisState) -> dict[str, object]:
    """Generate SQL for the current plan step, passing retry feedback."""
    step = state["steps"][state["step_index"]]
    attempt = state["attempt"] + 1
    query_question = step["question"]
    if state["feedback"]:
        query_question = f"{state['question']} Focus on {step['purpose']}. {step['question']}"
    proposal = propose_query(
        query_question, state["table"], state["feedback"], state["dataset_context"]
    )
    sql = proposal.sql
    limitation = proposal.limitation
    if sql is None:
        message = limitation or "The model could not form a query for this step."
        return {
            "attempt": attempt,
            "current_sql": None,
            "feedback": message,
            "trace": _trace(
                state,
                "generate_sql",
                "retry" if attempt < get_settings().max_sql_retries else "failed",
                f"{step['purpose']}: {message}",
            ),
        }
    return {
        "attempt": attempt,
        "current_sql": sql,
        "trace": _trace(
            state, "generate_sql", "complete", f"Prepared {step['purpose']} (attempt {attempt})."
        ),
    }


def _validate(state: AnalysisState) -> dict[str, object]:
    """Check table access and measure roles before any query runs."""
    step = state["steps"][state["step_index"]]
    try:
        safe_sql = validate_sql(state["current_sql"] or "", {str(state["table"]["name"]).lower()})
        validate_measure_roles(safe_sql, state["table"])
    except UnsafeQuery as exc:
        return {
            "current_sql": None,
            "feedback": str(exc),
            "trace": _trace(
                state,
                "validate_sql",
                "retry" if state["attempt"] < get_settings().max_sql_retries else "failed",
                f"{step['purpose']}: {exc}",
            ),
        }
    return {
        "current_sql": safe_sql,
        "feedback": None,
        "trace": _trace(
            state, "validate_sql", "complete", f"Checked {step['purpose']} for safe SQL."
        ),
    }


def _execute(state: AnalysisState) -> dict[str, object]:
    """Execute one safe query and record its rows or retry feedback."""
    step = state["steps"][state["step_index"]]
    count = state["execution_count"] + 1
    try:
        result = execute_query(
            Path(state["database_path"]),
            state["current_sql"] or "",
            {str(state["table"]["name"]).lower()},
        )
    except UnsafeQuery as exc:
        return {
            "execution_count": count,
            "current_sql": None,
            "feedback": str(exc),
            "trace": _trace(
                state,
                "execute_sql",
                "retry"
                if state["attempt"] < get_settings().max_sql_retries
                and count < get_settings().max_sql_queries
                else "failed",
                f"{step['purpose']}: {exc}",
            ),
        }
    query = {"purpose": step["purpose"], **result}
    return {
        "execution_count": count,
        "results": [*state["results"], query],
        "feedback": None,
        "trace": _trace(
            state,
            "execute_sql",
            "complete",
            f"{step['purpose']}: returned {len(result['rows'])} row(s).",
        ),
    }


def _advance(state: AnalysisState) -> dict[str, object]:
    """Move to the next plan step and clear the previous SQL attempt."""
    return {
        "step_index": state["step_index"] + 1,
        "attempt": 0,
        "current_sql": None,
        "feedback": None,
    }


def _display(value: object) -> str:
    """Format a value for the direct-result fallback answer."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _describe(result: dict[str, object]) -> str:
    """Describe a few returned rows when narrative reporting fails."""
    rows = result["rows"]
    if not rows:
        return f"{result['purpose']}: no matching rows."
    selected = rows[:3]
    values = [
        ", ".join(f"{key.replace('_', ' ')}: {_display(value)}" for key, value in row.items())
        for row in selected
    ]
    suffix = " (showing the first 3 rows)" if len(rows) > 3 else ""
    return f"{result['purpose']}: {'; '.join(values)}{suffix}."


def _grounded_findings(summary, results: list[dict[str, object]]) -> list[str]:
    """Check that a finding cites a query and uses numbers present in its rows."""
    findings = []
    for finding in summary.findings:
        if finding.query_index > len(results):
            raise ValueError("Finding refers to a missing query.")
        rows = results[finding.query_index - 1]["rows"][:12]
        available = {
            float(match.replace(",", ""))
            for row in rows
            for value in row.values()
            for match in re.findall(r"-?\d[\d,]*(?:\.\d+)?", str(value))
        }
        for match in re.findall(r"-?\d[\d,]*(?:\.\d+)?%?", finding.observation):
            if match.endswith("%") or float(match.replace(",", "")) not in available:
                raise ValueError("Finding contains a number absent from its cited query.")
        findings.append(finding.observation)
    if not findings:
        raise ValueError("Summary did not contain findings.")
    return findings


def _report(state: AnalysisState) -> dict[str, object]:
    """Build the answer and supporting evidence for the API response."""
    results = state["results"]
    failures = [
        event["message"] for event in state["trace"] if event["status"] in ("failed", "skipped")
    ]
    limitation_parts = [part for part in [state["limitation"], *failures] if part]
    report_status = "complete"
    report_message = "Summarized the findings from executed query results."
    if state["profile_answer"]:
        answer = state["profile_answer"]
        report_message = "Answered from the column profile saved at upload."
    elif results:
        try:
            summary = summarize_results(
                state["question"], state["table"], results, state["dataset_context"]
            )
            answer = "\n".join(_grounded_findings(summary, results))
            if summary.limitation:
                limitation_parts.append(summary.limitation)
        except Exception:
            answer = "\n".join(_describe(result) for result in results)
            report_status = "fallback"
            report_message = (
                "Used the executed rows because a grounded narrative summary was unavailable."
            )
    else:
        answer = state["limitation"] or "I could not complete a supported query for this question."
    if state["step_index"] < len(state["steps"]):
        limitation_parts.append(
            "The query execution limit was reached before all planned checks finished."
        )
    if any(result["truncated"] for result in results):
        limitation_parts.append("Some supporting results were truncated to the row limit.")
    first = results[0] if results else None
    report = {
        "answer": answer,
        "limitation": " ".join(dict.fromkeys(limitation_parts)) or None,
        "table": state["table"]["name"],
        "sql": first["sql"] if first else None,
        "columns": first["columns"] if first else [],
        "rows": first["rows"] if first else [],
        "truncated": any(result["truncated"] for result in results),
        "execution_duration_ms": round(
            sum(result["execution_duration_ms"] for result in results), 1
        ),
        "plan": [step["purpose"] for step in state["steps"]],
        "queries": results,
        "trace": state["trace"],
        "execution_count": state["execution_count"],
    }
    return {
        "report": report,
        "trace": _trace(state, "report", report_status, report_message),
    }


def analysis_events(
    question: str, table: dict[str, object], database_path: Path, dataset_context: str = ""
):
    """Yield progress, result, and done events for one bounded analysis."""
    started = perf_counter()
    state: AnalysisState = {
        "question": question,
        "dataset_context": dataset_context,
        "table": table,
        "database_path": str(database_path),
        "steps": [],
        "step_index": 0,
        "attempt": 0,
        "execution_count": 0,
        "current_sql": None,
        "feedback": None,
        "results": [],
        "trace": [],
        "limitation": None,
        "profile_answer": None,
        "report": None,
    }
    state.update(_inspect_schema(state))
    yield {"type": "progress", **state["trace"][-1]}
    state.update(_plan(state))
    for entry in state["trace"][1:]:
        yield {"type": "progress", **entry}
    while (
        state["step_index"] < len(state["steps"])
        and state["execution_count"] < get_settings().max_sql_queries
    ):
        state.update(_generate(state))
        yield {"type": "progress", **state["trace"][-1]}
        if state["current_sql"] is None:
            if state["attempt"] < get_settings().max_sql_retries:
                continue
            state.update(_advance(state))
            continue
        state.update(_validate(state))
        yield {"type": "progress", **state["trace"][-1]}
        if state["current_sql"] is None:
            if state["attempt"] < get_settings().max_sql_retries:
                continue
            state.update(_advance(state))
            continue
        state.update(_execute(state))
        yield {"type": "progress", **state["trace"][-1]}
        if (
            state["feedback"]
            and state["attempt"] < get_settings().max_sql_retries
            and state["execution_count"] < get_settings().max_sql_queries
        ):
            continue
        state.update(_advance(state))
    state.update(_report(state))
    yield {"type": "progress", **state["trace"][-1]}
    report = state["report"]
    if report is None:
        raise RuntimeError("Analysis did not produce a report.")
    report["trace"] = state["trace"]
    report["total_duration_ms"] = round((perf_counter() - started) * 1000, 1)
    yield {"type": "result", "report": report}
    yield {"type": "done"}

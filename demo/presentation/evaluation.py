"""Build the question-level evaluation panel the demo shows beside an answer."""

from __future__ import annotations

from typing import Any

# _expected_values and _value_matches hold the project's canonical value-matching
# rules, including the "round to the gold value's own precision" tolerance. The
# demo compares values server side so the browser never reimplements Decimal work.
from evaluation.metrics import _expected_values, _value_matches
from models import RunRecord

from .labels import (
    CHECK_DETAILS,
    CHECK_HINTS,
    CHECK_LABELS,
    REVIEWED_CHECKS,
    category_label,
    model_label,
    presentable,
)
from .store import _curated_by_id, _saved_batch


def _expected_answer(question_id: str) -> dict[str, Any]:
    """Render the gold answer for one question in a single shape."""
    curated = _curated_by_id().get(question_id)
    if curated is not None:
        return curated["expected_answer"]
    gold = _saved_batch()[question_id]["gold"]
    answer = gold.get("answer", {})
    text = (
        " ".join(answer.get("required_claims", []))
        if answer.get("response_type") == "paragraph"
        else f"{answer.get('value')} {answer.get('unit')}"
    )
    return {
        "text": text,
        "values": [
            {
                "label": value.get("label"),
                "value": value.get("value"),
                "unit": value.get("unit"),
                "period": None,
            }
            for value in _expected_values(gold)
        ],
    }


def _value_comparison(record: RunRecord, question_id: str) -> list[dict[str, Any]]:
    """Match every reported value against the gold values it should satisfy."""
    gold = _saved_batch()[question_id]["gold"]
    if not gold:
        return []
    expected = _expected_values(gold)
    remaining = list(expected)
    rows: list[dict[str, Any]] = []
    for value in record.answer.values if record.answer else []:
        match = next((item for item in remaining if _value_matches(value, item)), None)
        if match is not None:
            remaining.remove(match)
        rows.append(
            {
                "label": value.label,
                "value": value.value,
                "unit": value.unit,
                "period": value.period,
                "matched": match is not None,
                "expected_label": match.get("label") if match else None,
                "expected_value": match.get("value") if match else None,
            }
        )
    for item in remaining:
        rows.append(
            {
                "label": item.get("label"),
                "value": None,
                "unit": item.get("unit"),
                "period": None,
                "matched": False,
                "missing": True,
                "expected_label": item.get("label"),
                "expected_value": item.get("value"),
            }
        )
    return rows


def answer_issues(question_id: str) -> list[dict[str, Any]]:
    """Explain what the deterministic evaluation found wrong with one answer.

    Args:
        question_id: Benchmark question ID with a saved run.

    Returns:
        One entry per failed check, with the recorded detail values.
    """
    entry = _saved_batch()[question_id]
    row = entry["question_row"]
    issues: list[dict[str, Any]] = []
    if row.get("status") != "success":
        issues.append(
            {
                "check": "status",
                "label": "Pipeline failure",
                "hint": "The pipeline did not produce an answer, so every "
                "correctness check is recorded as failed.",
                "detail": [
                    str(row.get("failure_category") or "unknown"),
                    str(entry["record"].failure_message or ""),
                ],
            }
        )
    for check in REVIEWED_CHECKS:
        if row.get(check) is not False:
            continue
        detail: list[str] = []
        for field in CHECK_DETAILS.get(check, ()):
            detail.extend(str(item) for item in row.get(field) or [])
        issues.append(
            {
                "check": check,
                "label": CHECK_LABELS[check],
                "hint": CHECK_HINTS[check],
                "detail": detail,
            }
        )
    return issues


def _retrieval_metrics(question_id: str) -> dict[str, Any]:
    """Group the saved per-search retrieval metrics for one question."""
    rows = _saved_batch()[question_id]["retrieval_rows"]
    if not rows:
        return {
            "applicable": False,
            "reason": "This question is scored on normalized fact lookup, "
            "so retrieval quality does not apply.",
        }
    return {
        "applicable": True,
        "search_calls": [
            {
                "search_call_index": row["search_call_index"],
                "query": row["query"],
                "evidence_recall_at_5": row["evidence_recall_at_5"],
                "irrelevant_retrieval_rate_at_5": row["irrelevant_retrieval_rate_at_5"],
                "returned_evidence_ids": row["returned_evidence_ids"],
            }
            for row in rows
        ],
    }


def evaluation_panel(question_id: str) -> dict[str, Any]:
    """Collect every question-level metric the demo shows beside an answer.

    Args:
        question_id: Benchmark question ID with a saved run.

    Returns:
        Gold answer, value comparison, deterministic checks, and saved metrics.
    """
    entry = _saved_batch()[question_id]
    record = entry["record"]
    row = entry["question_row"]
    judge = entry["judge_row"]
    curated = _curated_by_id().get(question_id)
    return {
        "question_id": question_id,
        "category_label": category_label(record.question.category),
        "expected_answer": _expected_answer(question_id),
        "value_comparison": _value_comparison(record, question_id),
        "issues": answer_issues(question_id),
        "note": presentable((curated or {}).get("evaluation", {}).get("note")),
        "user_facing_answer_matches_expected": (curated or {})
        .get("evaluation", {})
        .get("user_facing_answer_matches_expected"),
        "status": row.get("status"),
        "failure_category": row.get("failure_category"),
        "evidence_count": len(record.answer.evidence_ids) if record.answer else 0,
        "deterministic": {
            name: row.get(name)
            for name in (
                "numerical_correct",
                "evidence_valid",
                "evidence_complete",
                "value_evidence_valid",
                "periods_valid",
                "formula_correct",
                "formatting_correct",
                "audit_trail_complete",
            )
        },
        "workflow": {
            "accepted_math_attempt": row.get("accepted_math_attempt"),
            "math_attempt_count": row.get("math_attempt_count"),
            "math_repair_attempted": row.get("math_repair_attempted"),
            "math_repair_successful": row.get("math_repair_successful"),
            "required_tools": row.get("required_tools"),
            "invoked_tools": row.get("invoked_tools"),
        },
        "tool_metrics": {
            "precision": row.get("tool_precision"),
            "recall": row.get("tool_recall"),
            "f1": row.get("tool_f1"),
            "exact_match": row.get("tool_exact_match"),
        },
        "retrieval_metrics": _retrieval_metrics(question_id),
        "reliability": {
            "retries": row.get("retries"),
            "structured_output_repairs": row.get("structured_output_repairs"),
        },
        "performance": {
            "latency_ms": row.get("latency_ms"),
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "reasoning_tokens": row.get("reasoning_tokens"),
            "cost_usd": row.get("cost_usd"),
        },
        "judge": None
        if judge is None
        else {
            "model_label": model_label(judge.get("judge_model")),
            "answer_correctness_grade": judge.get("llm_answer_correctness_grade"),
            "workflow_correctness": judge.get("workflow_correctness"),
            "chronological_coherence": judge.get("chronological_coherence"),
            "workflow_efficiency": judge.get("workflow_efficiency"),
            "auditability": judge.get("auditability"),
            "explanation": presentable(judge.get("explanation")),
        },
        "langfuse_trace_url": record.langfuse_trace_url,
    }


def answer_payload(record: RunRecord) -> dict[str, Any]:
    """Describe one run's final answer for the browser."""
    if record.answer is None:
        return {}
    return {
        "text": record.answer.answer_text,
        "values": [value.model_dump() for value in record.answer.values],
        "evidence_ids": record.answer.evidence_ids,
        "calculations": [
            calculation.model_dump() for calculation in record.answer.calculations
        ],
        "conclusions": record.answer.conclusions,
    }

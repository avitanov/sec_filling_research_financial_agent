"""Calculate deterministic metrics from frozen benchmark data and saved agent runs."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any

from agent.data import load_company_facts, load_gold_questions
from config import PROJECT_ROOT
from models import AnswerValue, CalculationRecord, RunRecord, ToolEvent
from utils import read_jsonl, write_csv

TOOL_NAMES = {"search_filing", "lookup_fact", "calculate"}
PLAIN_DECIMAL_PATTERN = re.compile(r"-?\d+(?:\.\d+)?\Z")
MAGNITUDE_WORD_PATTERN = re.compile(
    r"\b(?:thousand|million|billion|trillion)\b", re.IGNORECASE
)
USD_DISPLAY_PATTERN = re.compile(r"(?:-\$|\$-?)\d{1,3}(?:,\d{3})*(?:\.\d+)?")
PERCENT_DISPLAY_PATTERN = re.compile(
    r"(?<![\d.])-?\d+(?:\.\d+)?\s*"
    r"(?:%|percent(?:age)?(?:\s+points?)?)(?!\w)",
    re.IGNORECASE,
)


def _normalized_unit(value: str, unit: str | None) -> str:
    combined = f"{value} {unit or ''}".lower()
    if "%" in combined or "percent" in combined or "percentage" in combined:
        return "percent"
    if "$" in combined or "usd" in combined or "dollar" in combined:
        return "USD"
    return (unit or "").strip().lower()


def _parse_plain_decimal(value: str) -> Decimal | None:
    """Parse a structured value that uses the required plain-decimal format."""
    text = value.strip()
    if not PLAIN_DECIMAL_PATTERN.fullmatch(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def _parse_to_decimal(value: str) -> Decimal | None:
    """Parse an unambiguous number for value comparison, ignoring display marks."""
    text = value.strip().replace(",", "")
    if text.startswith("$"):
        text = text[1:].strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def _matches_reported_precision(actual: Decimal, expected: Decimal) -> bool:
    """Round an exact value to the precision used by a reported value."""
    quantum = Decimal("1").scaleb(expected.as_tuple().exponent)
    try:
        return actual.quantize(quantum, rounding=ROUND_HALF_UP) == expected
    except InvalidOperation:
        return False


def _value_structure_is_valid(value: AnswerValue) -> bool:
    if _parse_plain_decimal(value.value) is None:
        return False
    if value.unit not in {"USD", "percent"} or not value.period:
        return False
    return True


def _displayed_numbers(pattern: re.Pattern[str], text: str) -> list[Decimal]:
    numbers: list[Decimal] = []
    for match in pattern.finditer(text):
        displayed = match.group().lower()
        displayed = re.sub(r"(?:%|percent(?:age)?(?:\s+points?)?)\Z", "", displayed)
        displayed = displayed.strip().replace(",", "").replace("$", "")
        parsed = _parse_to_decimal(displayed)
        if parsed is not None:
            numbers.append(parsed)
    return numbers


def _formatting_correctness(generated_record: RunRecord) -> bool:
    """Check that structured values and their human-readable forms agree."""
    answer = generated_record.answer
    if answer is None or not answer.values:
        return False
    if MAGNITUDE_WORD_PATTERN.search(answer.answer_text):
        return False

    for value in answer.values:
        parsed = _parse_plain_decimal(value.value)
        if parsed is None or not _value_structure_is_valid(value):
            return False
        if value.unit == "USD":
            displayed = _displayed_numbers(USD_DISPLAY_PATTERN, answer.answer_text)
            if parsed not in displayed:
                return False
        else:
            displayed = _displayed_numbers(PERCENT_DISPLAY_PATTERN, answer.answer_text)
            if parsed not in displayed:
                return False
    return True


def _expected_values(gold_record: dict[str, Any]) -> list[dict[str, str | None]]:
    answer = gold_record["answer"]
    if answer["response_type"] == "paragraph":
        return answer["required_values"]
    return [
        {
            "label": answer.get("label"),
            "value": answer["value"],
            "unit": answer["unit"],
        }
    ]


def _value_matches(actual: AnswerValue, expected: dict[str, str | None]) -> bool:
    actual_value = _parse_to_decimal(actual.value)
    expected_value = _parse_to_decimal(str(expected["value"]))
    if actual_value is None or expected_value is None:
        return False
    actual_unit = _normalized_unit(actual.value, actual.unit)
    expected_unit = _normalized_unit(
        str(expected["value"]),
        str(expected["unit"]) if expected.get("unit") is not None else None,
    )
    return actual_unit == expected_unit and _matches_reported_precision(
        actual_value, expected_value
    )


def _numerical_correctness(
    generated_record: RunRecord, gold_record: dict[str, Any]
) -> tuple[bool, list[str], list[str]]:
    if generated_record.answer is None:
        return False, ["No structured answer"], []
    expected_values = _expected_values(gold_record)
    unused_actual = list(generated_record.answer.values)
    missing: list[str] = []
    for expected in expected_values:
        match_index = next(
            (
                index
                for index, actual in enumerate(unused_actual)
                if _value_matches(actual, expected)
            ),
            None,
        )
        if match_index is None:
            missing.append(
                f"{expected.get('label') or 'answer'}={expected['value']} "
                f"{expected.get('unit') or ''}".strip()
            )
        else:
            unused_actual.pop(match_index)
    unexpected = [
        f"{value.label}={value.value} {value.unit or ''}".strip()
        for value in unused_actual
    ]
    return not missing and not unexpected, missing, unexpected


def _returned_evidence_ids(generated_record: RunRecord) -> set[str]:
    return {
        evidence_id
        for event in generated_record.tool_events
        if event.success
        for evidence_id in event.evidence_ids
    }


def _accepted_calculation_events(generated_record: RunRecord) -> list[ToolEvent]:
    """Return calculator events that support the accepted final answer."""
    events = [
        event
        for event in generated_record.tool_events
        if event.name == "calculate" and event.success
    ]
    if generated_record.architecture == "monolithic":
        return events
    if generated_record.accepted_math_attempt is None:
        return []
    return [
        event
        for event in events
        if event.agent_stage == "math"
        and event.agent_attempt == generated_record.accepted_math_attempt
    ]


def _math_attempts(generated_record: RunRecord) -> list[int]:
    """Return distinct Math stage attempts in chronological order."""
    return sorted(
        {
            call.agent_attempt
            for call in generated_record.model_calls
            if call.agent_stage == "math" and call.agent_attempt is not None
        }
    )


def _evidence_checks(
    generated_record: RunRecord, gold_record: dict[str, Any]
) -> tuple[bool, bool, list[str], list[str]]:
    if generated_record.answer is None:
        return False, False, [], list(gold_record.get("gold_evidence_ids", []))
    cited = set(generated_record.answer.evidence_ids)
    returned = _returned_evidence_ids(generated_record)
    invalid = sorted(cited - returned)
    values_have_sources = bool(generated_record.answer.values) and all(
        value.source_evidence_ids and set(value.source_evidence_ids) <= cited
        for value in generated_record.answer.values
    )
    missing = (
        []
        if values_have_sources
        else sorted(set(gold_record.get("gold_evidence_ids", [])) - cited)
    )
    return bool(cited) and not invalid, values_have_sources, invalid, missing


def _gold_sources(gold_record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *load_company_facts(str(gold_record["ticker"])),
        *gold_record.get("gold_numeric_inputs", []),
    ]


def _source_periods(gold_record: dict[str, Any]) -> dict[str, set[str]]:
    periods: dict[str, set[str]] = {}
    for source in _gold_sources(gold_record):
        evidence_id = source.get("evidence_id")
        period = source.get("period")
        if evidence_id and period:
            periods.setdefault(str(evidence_id), set()).add(str(period))
    return periods


def _runtime_source_periods(generated_record: RunRecord) -> dict[str, set[str]]:
    """Read periods attached to evidence returned by successful tools."""
    periods: dict[str, set[str]] = {}
    for event in generated_record.tool_events:
        if not event.success or not isinstance(event.result, dict):
            continue
        event_ticker = str(event.arguments.get("company", "")).upper().strip()
        if event_ticker != generated_record.question.ticker.upper():
            continue
        if event.name == "lookup_fact":
            sources = event.result.get("facts", [])
            for source in sources:
                if not isinstance(source, dict):
                    continue
                if str(source.get("ticker", "")).upper() != event_ticker:
                    continue
                evidence_id = source.get("evidence_id")
                period = source.get("period")
                if evidence_id and period:
                    periods.setdefault(str(evidence_id), set()).add(str(period))
        elif event.name == "search_filing":
            sources = event.result.get("records", [])
            for source in sources:
                if not isinstance(source, dict) or not source.get("evidence_id"):
                    continue
                metadata = source.get("metadata", {})
                if not isinstance(metadata, dict):
                    continue
                source_periods = metadata.get("table_periods", [])
                if not source_periods and metadata.get("report_year"):
                    source_periods = [metadata["report_year"]]
                for period in source_periods:
                    text = str(period)
                    normalized = text if text.startswith("FY") else f"FY{text}"
                    periods.setdefault(str(source["evidence_id"]), set()).add(
                        normalized
                    )
    return periods


def _value_evidence_and_period_checks(
    generated_record: RunRecord, gold_record: dict[str, Any]
) -> tuple[bool, bool, list[str]]:
    if generated_record.answer is None:
        return False, False, ["No structured answer"]
    cited = set(generated_record.answer.evidence_ids)
    returned = _returned_evidence_ids(generated_record)
    source_periods = _source_periods(gold_record)
    for evidence_id, periods in _runtime_source_periods(generated_record).items():
        source_periods.setdefault(evidence_id, set()).update(periods)
    invalid: list[str] = []
    periods_valid = True
    for value in generated_record.answer.values:
        value_sources = set(value.source_evidence_ids)
        if (
            not value_sources
            or not value_sources <= cited
            or not value_sources <= returned
            or not value_sources <= set(source_periods)
        ):
            invalid.append(value.label)
            continue
        available_periods = {
            period
            for evidence_id in value_sources
            for period in source_periods[evidence_id]
        }
        requested_periods = set(re.findall(r"FY\d{4}", value.period or ""))
        if requested_periods and not requested_periods <= available_periods:
            periods_valid = False
    return not invalid, periods_valid, invalid


def _fact_values(ticker: str) -> dict[str, str]:
    return {
        str(fact["evidence_id"]): str(fact["value"])
        for fact in load_company_facts(ticker)
    }


def _expected_calculations(gold_record: dict[str, Any]) -> list[dict[str, Any]]:
    resolved_values = _fact_values(str(gold_record["ticker"]))
    resolved_values.update(
        {
            str(value["input_id"]): str(value["value"])
            for value in gold_record.get("gold_numeric_inputs", [])
        }
    )
    calculations: list[dict[str, Any]] = []
    for calculation in gold_record.get("gold_calculations", []):
        inputs = [resolved_values[ref] for ref in calculation.get("input_refs", [])]
        inputs.extend(str(value) for value in calculation.get("constant_inputs", []))
        calculations.append(
            {
                "operation": calculation["operation"],
                "inputs": inputs,
                "result": str(calculation["result"]),
            }
        )
        resolved_values[str(calculation["calculation_id"])] = str(calculation["result"])
    return calculations


def _same_decimal(left: str, right: str) -> bool:
    left_value = _parse_plain_decimal(left)
    right_value = _parse_plain_decimal(right)
    return left_value is not None and left_value == right_value


def _calculation_matches(actual: CalculationRecord, expected: dict[str, Any]) -> bool:
    if actual.operation != expected["operation"]:
        return False
    if len(actual.inputs) != len(expected["inputs"]):
        return False
    if not all(
        _same_decimal(actual_input, expected_input)
        for actual_input, expected_input in zip(actual.inputs, expected["inputs"])
    ):
        return False
    return _same_decimal(actual.result, str(expected["result"]))


def _tool_calculations(generated_record: RunRecord) -> list[CalculationRecord]:
    """Read exact calculations from successful canonical tool events."""
    calculations: list[CalculationRecord] = []
    for event in _accepted_calculation_events(generated_record):
        event_inputs = event.arguments.get("inputs")
        result = event.result if isinstance(event.result, dict) else None
        if (
            not isinstance(event_inputs, list)
            or not all(isinstance(value, str) for value in event_inputs)
            or result is None
            or "result" not in result
        ):
            continue
        calculations.append(
            CalculationRecord(
                operation=event.arguments.get("operation"),
                inputs=event_inputs,
                result=str(result["result"]),
            )
        )
    return calculations


def _formula_correctness(
    generated_record: RunRecord, gold_record: dict[str, Any]
) -> bool | None:
    expected = _expected_calculations(gold_record)
    if not expected:
        return None
    if generated_record.answer is None:
        return False
    unmatched = _tool_calculations(generated_record)
    for expected_item in expected:
        match_index = next(
            (
                index
                for index, actual_item in enumerate(unmatched)
                ## Compare the generated calculation with the gold calculation.
                if _calculation_matches(actual_item, expected_item)
            ),
            None,
        )
        if match_index is None:
            return False
        unmatched.pop(match_index)
    return True


def _evidence_numbers(
    gold_record: dict[str, Any], evidence_ids: set[str]
) -> list[Decimal]:
    """Read exact frozen values for the evidence bound to one answer value."""
    numbers: list[Decimal] = []
    for source in _gold_sources(gold_record):
        if str(source.get("evidence_id")) not in evidence_ids:
            continue
        parsed = _parse_plain_decimal(source["value"])
        if parsed is not None:
            numbers.append(parsed)
    return numbers


def _calculation_numbers(generated_record: RunRecord) -> list[Decimal]:
    """Read calculator results that were actually returned during this run."""
    numbers: list[Decimal] = []
    for event in _accepted_calculation_events(generated_record):
        result = event.result if isinstance(event.result, dict) else {}
        if "result" in result:
            parsed = _parse_plain_decimal(str(result["result"]))
            if parsed is not None:
                numbers.append(parsed)
    return numbers


def _is_supported_value(
    value: AnswerValue, generated_record: RunRecord, gold_record: dict[str, Any]
) -> bool:
    parsed = _parse_plain_decimal(value.value)
    if parsed is None:
        return False
    actual = parsed
    direct_sources = _evidence_numbers(gold_record, set(value.source_evidence_ids))
    if any(source == actual for source in direct_sources):
        return True
    return any(
        source == actual or _matches_reported_precision(source, actual)
        for source in _calculation_numbers(generated_record)
    )


def _unsupported_values(
    generated_record: RunRecord, gold_record: dict[str, Any]
) -> list[str]:
    if generated_record.answer is None:
        return []
    return [
        f"{value.label}: {value.value} {value.unit or ''}".strip()
        for value in generated_record.answer.values
        if not _is_supported_value(value, generated_record, gold_record)
    ]


def _audit_trail_complete(
    generated_record: RunRecord,
    evidence_valid: bool,
    value_evidence_valid: bool,
    periods_valid: bool,
    formula_correct: bool | None,
    unsupported: list[str],
) -> bool:
    if generated_record.status != "success":
        return False
    if (
        generated_record.answer is None
        or not generated_record.tool_events
        or not generated_record.answer.values
    ):
        return False
    if (
        not generated_record.answer.evidence_ids
        or not evidence_valid
        or not value_evidence_valid
        or not periods_valid
        or formula_correct is False
        or unsupported
    ):
        return False
    if any(
        not _value_structure_is_valid(value) for value in generated_record.answer.values
    ):
        return False
    return True


def _failure_category(generated_record: RunRecord) -> str | None:
    """Normalize legacy Bedrock stream failures for reliability metrics."""
    if generated_record.failure_category == "agent_error" and (
        "modelstreamerrorexception" in (generated_record.failure_message or "").lower()
    ):
        return "provider_stream_error"
    return generated_record.failure_category


def evaluate_runs(
    generated_records: list[RunRecord], gold_records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate answer, audit, tool, reliability, and operational metrics.

    Args:
        generated_records: One batch of saved agent run records.
        gold_records: Frozen benchmark records for the same questions.

    Returns:
        Per-question rows and an aggregate metric summary.
    """
    gold_by_id = {
        gold_record["question_id"]: gold_record for gold_record in gold_records
    }
    rows: list[dict[str, Any]] = []
    tool_true_positive = 0
    tool_false_positive = 0
    tool_false_negative = 0
    conditional_correct = 0
    tool_pair_count = 0
    successful_tool_events = 0
    all_tool_events = 0
    total_values = 0
    total_unsupported = 0

    for generated_record in generated_records:
        gold_record = gold_by_id[generated_record.question.question_id]
        numerical_correct, numerical_issues, unexpected_values = _numerical_correctness(
            generated_record, gold_record
        )
        evidence_valid, evidence_complete, invalid_ids, missing_ids = _evidence_checks(
            generated_record, gold_record
        )
        value_evidence_valid, periods_valid, invalid_value_evidence = (
            _value_evidence_and_period_checks(generated_record, gold_record)
        )
        formula_correct = _formula_correctness(generated_record, gold_record)
        unsupported = _unsupported_values(generated_record, gold_record)
        formatting_correct = _formatting_correctness(generated_record)
        audit_complete = _audit_trail_complete(
            generated_record,
            evidence_valid,
            value_evidence_valid,
            periods_valid,
            formula_correct,
            unsupported,
        )
        if generated_record.status != "success":
            numerical_correct = False
            evidence_valid = False
            evidence_complete = False
            value_evidence_valid = False
            periods_valid = False
            formula_correct = False if formula_correct is not None else None
        required_tools = set(gold_record.get("required_tools", []))
        invoked_tools = {event.name for event in generated_record.tool_events}
        true_positive = len(required_tools & invoked_tools)
        false_positive = len(invoked_tools - required_tools)
        false_negative = len(required_tools - invoked_tools)
        precision = true_positive / len(invoked_tools) if invoked_tools else 0.0
        recall = true_positive / len(required_tools) if required_tools else 1.0
        f1 = (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )

        tool_true_positive += true_positive
        tool_false_positive += false_positive
        tool_false_negative += false_negative
        conditional_correct += sum(
            (name in required_tools) == (name in invoked_tools) for name in TOOL_NAMES
        )
        tool_pair_count += len(TOOL_NAMES)
        all_tool_events += len(generated_record.tool_events)
        successful_tool_events += sum(
            event.success for event in generated_record.tool_events
        )
        answer_values = (
            generated_record.answer.values if generated_record.answer else []
        )
        math_attempts = _math_attempts(generated_record)
        math_repair_attempted = (
            len(math_attempts) > 1
            if generated_record.architecture == "decomposed"
            else None
        )
        math_repair_successful = (
            generated_record.status == "success"
            and generated_record.accepted_math_attempt == math_attempts[-1]
            if math_repair_attempted
            else None
        )
        total_values += len(answer_values)
        total_unsupported += len(unsupported)

        rows.append(
            {
                "run_id": generated_record.run_id,
                "question_id": generated_record.question.question_id,
                "category": generated_record.question.category,
                "architecture": generated_record.architecture,
                "pipeline_variant": generated_record.pipeline_variant,
                "status": generated_record.status,
                "answer_text": generated_record.answer.answer_text
                if generated_record.answer
                else "",
                "numerical_correct": numerical_correct,
                "numerical_issues": numerical_issues,
                "unexpected_values": unexpected_values,
                "evidence_valid": evidence_valid,
                "invalid_evidence_ids": invalid_ids,
                "evidence_complete": evidence_complete,
                "missing_evidence_ids": missing_ids,
                "value_evidence_valid": value_evidence_valid,
                "invalid_value_evidence": invalid_value_evidence,
                "periods_valid": periods_valid,
                "formula_correct": formula_correct,
                "formatting_correct": formatting_correct,
                "unsupported_values": unsupported,
                "audit_trail_complete": audit_complete,
                "accepted_math_attempt": generated_record.accepted_math_attempt,
                "math_attempt_count": len(math_attempts),
                "math_repair_attempted": math_repair_attempted,
                "math_repair_successful": math_repair_successful,
                "required_tools": sorted(required_tools),
                "invoked_tools": sorted(invoked_tools),
                "tool_precision": precision,
                "tool_recall": recall,
                "tool_f1": f1,
                "tool_exact_match": required_tools == invoked_tools,
                "latency_ms": generated_record.latency_ms,
                "input_tokens": generated_record.token_usage.get("inputTokens", 0),
                "output_tokens": generated_record.token_usage.get("outputTokens", 0),
                "reasoning_tokens": generated_record.token_usage.get(
                    "reasoningTokens", 0
                ),
                "retries": generated_record.retries,
                "structured_output_repairs": generated_record.structured_output_repairs,
                "cost_usd": generated_record.cost_usd.get("total"),
                "failure_category": _failure_category(generated_record),
            }
        )

    run_count = len(rows)
    successful_runs = sum(row["status"] == "success" for row in rows)
    tool_precision = (
        tool_true_positive / (tool_true_positive + tool_false_positive)
        if tool_true_positive + tool_false_positive
        else 0.0
    )
    tool_recall = (
        tool_true_positive / (tool_true_positive + tool_false_negative)
        if tool_true_positive + tool_false_negative
        else 0.0
    )
    tool_f1 = (
        2 * tool_precision * tool_recall / (tool_precision + tool_recall)
        if tool_precision + tool_recall
        else 0.0
    )
    latencies = [float(row["latency_ms"]) for row in rows]
    known_costs = [
        Decimal(str(row["cost_usd"])) for row in rows if row["cost_usd"] is not None
    ]
    total_cost = sum(known_costs, Decimal("0"))

    def rate(field: str) -> float:
        return sum(bool(row[field]) for row in rows) / run_count if run_count else 0.0

    def applicable_rate(field: str) -> float:
        values = [row[field] for row in rows if row[field] is not None]
        return sum(bool(value) for value in values) / len(values) if values else 0.0

    summary: dict[str, Any] = {
        "run_count": run_count,
        "run_success_rate": successful_runs / run_count if run_count else 0.0,
        "numerical_correctness_rate": rate("numerical_correct"),
        "evidence_validity_rate": rate("evidence_valid"),
        "evidence_completeness_rate": rate("evidence_complete"),
        "formula_correctness_rate": applicable_rate("formula_correct"),
        "formatting_correctness_rate": rate("formatting_correct"),
        "unsupported_number_count": total_unsupported,
        "unsupported_number_rate": (
            total_unsupported / total_values if total_values else 0.0
        ),
        "audit_trail_completeness_rate": rate("audit_trail_complete"),
        "math_repair_invocation_rate": applicable_rate("math_repair_attempted"),
        "math_repair_success_rate": applicable_rate("math_repair_successful"),
        "tool_precision": tool_precision,
        "tool_recall": tool_recall,
        "tool_f1": tool_f1,
        "tool_exact_match_rate": rate("tool_exact_match"),
        "tool_invocation_rate": (
            sum(bool(row["invoked_tools"]) for row in rows) / run_count
            if run_count
            else 0.0
        ),
        "tool_execution_success_rate": (
            successful_tool_events / all_tool_events if all_tool_events else 0.0
        ),
        "conditional_execution_rate": (
            conditional_correct / tool_pair_count if tool_pair_count else 0.0
        ),
        "average_latency_ms": sum(latencies) / run_count if run_count else 0.0,
        "median_latency_ms": median(latencies) if latencies else 0.0,
        "total_input_tokens": sum(int(row["input_tokens"]) for row in rows),
        "total_output_tokens": sum(int(row["output_tokens"]) for row in rows),
        "total_reasoning_tokens": sum(int(row["reasoning_tokens"]) for row in rows),
        "total_retries": sum(int(row["retries"]) for row in rows),
        "total_structured_output_repairs": sum(
            int(row["structured_output_repairs"]) for row in rows
        ),
        "structured_output_repair_rate": (
            sum(bool(row["structured_output_repairs"]) for row in rows) / run_count
            if run_count
            else 0.0
        ),
        "infrastructure_failure_count": sum(
            "provider_" in str(row["failure_category"] or "") for row in rows
        ),
        "total_estimated_cost_usd": str(total_cost),
        "average_estimated_cost_usd": str(
            total_cost / len(known_costs) if known_costs else Decimal("0")
        ),
        "cost_coverage_rate": len(known_costs) / run_count if run_count else 0.0,
    }
    summary["by_category"] = {
        category: {
            "run_count": len(category_rows),
            "numerical_correctness_rate": sum(
                row["numerical_correct"] for row in category_rows
            )
            / len(category_rows),
            "evidence_completeness_rate": sum(
                row["evidence_complete"] for row in category_rows
            )
            / len(category_rows),
            "formula_correctness_rate": (
                sum(
                    bool(row["formula_correct"])
                    for row in category_rows
                    if row["formula_correct"] is not None
                )
                / sum(row["formula_correct"] is not None for row in category_rows)
                if any(row["formula_correct"] is not None for row in category_rows)
                else None
            ),
            "formatting_correctness_rate": sum(
                row["formatting_correct"] for row in category_rows
            )
            / len(category_rows),
        }
        for category in sorted({row["category"] for row in rows})
        if (category_rows := [row for row in rows if row["category"] == category])
    }
    return rows, summary


def evaluate_retrieval(
    generated_records: list[RunRecord], gold_records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Calculate retrieval metrics from canonical search events in saved runs.

    Args:
        generated_records: Saved agent runs containing search_filing tool events.
        gold_records: Frozen benchmark records for the evaluated questions.

    Returns:
        One row per filing-search event and question-level aggregate metrics.
    """
    gold_by_id = {
        gold_record["question_id"]: gold_record for gold_record in gold_records
    }
    rows: list[dict[str, Any]] = []
    generated_records_by_question = {
        generated_record.question.question_id: generated_record
        for generated_record in generated_records
    }
    for question_id, gold_record in gold_by_id.items():
        expected = {
            evidence_id
            for evidence_id in gold_record.get("gold_evidence_ids", [])
            if evidence_id.startswith("filing-")
        }
        if not expected:
            continue
        generated_record = generated_records_by_question.get(question_id)
        relevant = expected | set(
            gold_record.get("alternative_retrieval_evidence_ids", [])
        )
        search_events = (
            [
                event
                for event in generated_record.tool_events
                if event.name == "search_filing"
            ]
            if generated_record is not None
            else []
        )
        event_records: list[tuple[ToolEvent, list[dict[str, Any]]]] = []
        for event in search_events:
            result = event.result if isinstance(event.result, dict) else {}
            records = [
                item
                for item in result.get("records", [])[:5]
                if isinstance(item, dict) and item.get("evidence_id")
            ]
            event_records.append((event, records))

        # A relevant table and its supporting page are equivalent retrieval hits.
        expanded_relevant = set(relevant)
        for _, records in event_records:
            for record in records:
                evidence_id = str(record["evidence_id"])
                metadata = record.get("metadata", {})
                if evidence_id in relevant and isinstance(metadata, dict):
                    expanded_relevant.update(
                        str(page_id)
                        for page_id in metadata.get("supporting_page_ids", [])
                    )

        if not event_records:
            rows.append(
                {
                    "run_id": generated_record.run_id
                    if generated_record is not None
                    else None,
                    "question_id": question_id,
                    "search_call_index": None,
                    "search_sequence": None,
                    "query": None,
                    "returned_evidence_ids": [],
                    "evidence_recall_at_5": 0.0,
                    "irrelevant_retrieval_rate_at_5": 1.0,
                }
            )
            continue

        for call_index, (event, records) in enumerate(event_records, start=1):
            returned = [str(record["evidence_id"]) for record in records]
            returned_set = set(returned)
            rows.append(
                {
                    "run_id": generated_record.run_id
                    if generated_record is not None
                    else None,
                    "question_id": question_id,
                    "search_call_index": call_index,
                    "search_sequence": event.sequence,
                    "query": event.arguments.get("query"),
                    "returned_evidence_ids": returned,
                    "evidence_recall_at_5": float(
                        bool(expanded_relevant & returned_set)
                    ),
                    "irrelevant_retrieval_rate_at_5": (
                        sum(
                            evidence_id not in expanded_relevant
                            for evidence_id in returned
                        )
                        / len(returned)
                        if returned
                        else 1.0
                    ),
                }
            )

    question_ids = sorted({row["question_id"] for row in rows})
    question_scores = [
        (
            max(
                row["evidence_recall_at_5"]
                for row in rows
                if row["question_id"] == question_id
            ),
            sum(
                row["irrelevant_retrieval_rate_at_5"]
                for row in rows
                if row["question_id"] == question_id
            )
            / sum(row["question_id"] == question_id for row in rows),
        )
        for question_id in question_ids
    ]
    count = len(question_scores)
    return rows, {
        "evidence_recall_at_5": (
            sum(recall for recall, _ in question_scores) / count if count else 0.0
        ),
        "irrelevant_retrieval_rate_at_5": (
            sum(irrelevant for _, irrelevant in question_scores) / count
            if count
            else 0.0
        ),
    }


def _results_directory_for_run(run_id: str, results_root: Path) -> Path:
    matches = []
    for records_path in sorted(results_root.glob("*/runs.jsonl")):
        if any(
            str(record.get("run_id")) == run_id for record in read_jsonl(records_path)
        ):
            matches.append(records_path.parent)
    if len(matches) != 1:
        raise ValueError(
            f"Expected one saved result batch for run ID {run_id!r}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _latest_results_directory(
    results_root: Path, architecture: str, ticker: str
) -> Path:
    matches: list[Path] = []
    for records_path in sorted(results_root.glob("*/runs.jsonl")):
        records = read_jsonl(records_path)
        if not records:
            continue
        first = records[0]
        question = first.get("question", {})
        if (
            first.get("architecture") == architecture
            and str(question.get("ticker", "")).upper() == ticker.upper()
        ):
            matches.append(records_path.parent)
    if not matches:
        raise ValueError(
            f"No saved {architecture} result batch found for {ticker.upper()}"
        )
    # Result folder names start with a sortable creation timestamp. File updates
    # must not make an older experiment become the "latest" generated batch.
    return max(matches, key=lambda path: path.name)


def evaluate_saved_results(
    results_directory: Path | None = None,
    *,
    run_id: str | None = None,
    latest: bool = False,
    rerun_judge: bool = False,
    architecture: str | None = None,
    ticker: str | None = None,
    results_root: Path | None = None,
) -> dict[str, Any]:
    """Recompute metrics from one saved batch without rerunning the pipeline.

    Args:
        results_directory: Existing result directory containing ``runs.jsonl``.
        run_id: Optional run ID used to locate its complete saved batch.
        latest: Whether to select the latest batch for an architecture and ticker.
        rerun_judge: Whether to make new LLM calls for the qualitative judge.
        architecture: Required architecture when ``latest`` is true.
        ticker: Required company ticker when ``latest`` is true.
        results_root: Optional result root used for saved-batch selection.

    Returns:
        Saved runs, per-question rows, retrieval rows, and aggregate metrics.

    Raises:
        ValueError: If the reference is ambiguous or the batch is invalid.
    """
    selection_count = sum((results_directory is not None, run_id is not None, latest))
    if selection_count != 1:
        raise ValueError(
            "Provide exactly one of results_directory, run_id, or latest=True"
        )
    root = results_root or PROJECT_ROOT / "results"
    if run_id is not None:
        results_directory = _results_directory_for_run(run_id, root)
        evaluation_source = "saved_run_id"
    elif latest:
        if architecture is None or ticker is None:
            raise ValueError("latest=True requires architecture and ticker")
        results_directory = _latest_results_directory(root, architecture, ticker)
        evaluation_source = "latest_saved"
    else:
        evaluation_source = "saved_directory"
    assert results_directory is not None
    records_path = results_directory / "runs.jsonl"
    records = [RunRecord.model_validate(record) for record in read_jsonl(records_path)]
    if not records:
        raise ValueError(f"No saved runs found in {records_path}")
    tickers = {record.question.ticker for record in records}
    if len(tickers) != 1:
        raise ValueError("One saved batch must contain exactly one company ticker")

    gold_records = load_gold_questions(next(iter(tickers)))
    question_rows, summary = evaluate_runs(records, gold_records)
    retrieval_rows, retrieval_summary = evaluate_retrieval(records, gold_records)
    summary.update(retrieval_summary)
    judge_rows = None
    if rerun_judge:
        from evaluation.judge import evaluate_with_llm_judge

        judge_rows, judge_summary = evaluate_with_llm_judge(
            records, gold_records, question_rows
        )
        summary.update(judge_summary)
    return {
        "evaluation_source": evaluation_source,
        "results_directory": results_directory,
        "records": records,
        "question_rows": question_rows,
        "retrieval_rows": retrieval_rows,
        "judge_rows": judge_rows,
        "summary": summary,
    }


def save_run_records(
    generated_records: list[RunRecord],
    *,
    results_root: Path | None = None,
) -> Path:
    """Save one generated batch before evaluating it.

    Args:
        generated_records: Complete agent run records from one batch.
        results_root: Optional destination root. Defaults to ``results/``.

    Returns:
        Created result directory.

    Raises:
        ValueError: If the batch is empty or mixes architectures, models, or companies.
    """
    if not generated_records:
        raise ValueError("Cannot save an empty evaluation batch")
    models = {generated_record.model for generated_record in generated_records}
    tickers = {
        generated_record.question.ticker for generated_record in generated_records
    }
    architectures = {
        generated_record.architecture for generated_record in generated_records
    }
    variants = {
        generated_record.pipeline_variant for generated_record in generated_records
    }
    if (
        len(models) != 1
        or len(tickers) != 1
        or len(architectures) != 1
        or len(variants) != 1
    ):
        raise ValueError(
            "One result directory must contain one architecture variant, model, "
            "and company"
        )
    model_slug = re.sub(r"[^a-z0-9]+", "-", next(iter(models)).lower()).strip("-")
    architecture = next(iter(architectures))
    variant = next(iter(variants))
    ticker = next(iter(tickers)).upper()
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S%fZ")
    architecture_label = f"{architecture}-{variant}" if variant else architecture
    destination = (results_root or PROJECT_ROOT / "results") / (
        f"{timestamp}-{architecture_label}-{model_slug}-{ticker}"
    )
    destination.mkdir(parents=True)
    with (destination / "runs.jsonl").open("w", encoding="utf-8") as output:
        for generated_record in generated_records:
            output.write(generated_record.model_dump_json() + "\n")
    return destination


def save_evaluation_results(
    results_directory: Path,
    question_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    retrieval_rows: list[dict[str, Any]],
    *,
    judge_rows: list[dict[str, Any]] | None = None,
) -> Path:
    """Save computed metrics beside an existing saved run batch.

    Args:
        results_directory: Directory containing the batch ``runs.jsonl``.
        question_rows: Per-question evaluation results.
        summary: Aggregate metrics, including retrieval metrics.
        retrieval_rows: Per-search top-five retrieval results.
        judge_rows: Optional per-question LLM-judge results.

    Returns:
        Updated result directory.

    Raises:
        ValueError: If the run records have not been saved first.
    """
    if not (results_directory / "runs.jsonl").is_file():
        raise ValueError("Save run records before saving their evaluation")
    write_csv(results_directory / "question_metrics.csv", question_rows)
    write_csv(results_directory / "retrieval_metrics.csv", retrieval_rows)
    if judge_rows is not None:
        with (results_directory / "judge_records.jsonl").open(
            "w", encoding="utf-8"
        ) as output:
            for row in judge_rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
    (results_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return results_directory


def format_evaluation_summary(summary: dict[str, Any]) -> str:
    """Format the main experiment metrics for notebook inspection.

    Args:
        summary: Aggregate metrics returned by :func:`evaluate_runs`.

    Returns:
        Compact text summary.
    """
    percent_metrics = (
        "run_success_rate",
        "numerical_correctness_rate",
        "evidence_validity_rate",
        "evidence_completeness_rate",
        "formula_correctness_rate",
        "formatting_correctness_rate",
        "unsupported_number_rate",
        "audit_trail_completeness_rate",
        "math_repair_invocation_rate",
        "math_repair_success_rate",
        "tool_precision",
        "tool_recall",
        "tool_f1",
        "tool_exact_match_rate",
        "tool_invocation_rate",
        "tool_execution_success_rate",
        "conditional_execution_rate",
        "evidence_recall_at_5",
        "irrelevant_retrieval_rate_at_5",
        "judge_success_rate",
        "judge_cost_coverage_rate",
        "structured_output_repair_rate",
    )
    lines = [f"Runs: {summary['run_count']}"]
    lines.extend(
        f"{name}: {float(summary[name]):.2%}"
        for name in percent_metrics
        if name in summary
    )
    lines.extend(
        [
            f"Average latency: {float(summary['average_latency_ms']):.2f} ms",
            f"Input tokens: {summary['total_input_tokens']}",
            f"Output tokens: {summary['total_output_tokens']}",
            f"Reasoning tokens: {summary['total_reasoning_tokens']}",
            f"Retries: {summary['total_retries']}",
            f"Structured-output repairs: {summary['total_structured_output_repairs']}",
            f"Infrastructure failures: {summary['infrastructure_failure_count']}",
            f"Estimated cost: ${summary['total_estimated_cost_usd']}",
        ]
    )
    judge_scores = (
        "llm_answer_correctness_grade",
        "workflow_correctness",
        "chronological_coherence",
        "workflow_efficiency",
        "auditability",
    )
    lines.extend(
        f"Judge {name.replace('_', ' ')}: {float(summary[key]):.2f}/5"
        for name in judge_scores
        if (key := f"average_judge_{name}") in summary and summary[key] is not None
    )
    if "judge_total_input_tokens" in summary:
        lines.extend(
            [
                f"Judge model invocations: {summary['judge_total_model_invocations']}",
                f"Judge input tokens: {summary['judge_total_input_tokens']}",
                f"Judge output tokens: {summary['judge_total_output_tokens']}",
                f"Judge reasoning tokens: {summary['judge_total_reasoning_tokens']}",
                f"Judge retries: {summary['judge_total_retries']}",
                f"Judge estimated cost: ${summary['judge_total_estimated_cost_usd']}",
            ]
        )
    return "\n".join(lines)

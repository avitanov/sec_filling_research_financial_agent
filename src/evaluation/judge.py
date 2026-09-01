"""Grade qualitative run characteristics with one reusable LLM judge."""

from __future__ import annotations

import json
from decimal import Decimal
from statistics import mean
from typing import Any

from strands import Agent
from strands.models import Model

from agent.observation import extract_model_metrics
from agent.plugins import ProviderRetryStrategy, RetryTrackingPlugin
from agent.providers import build_model
from config import (
    AWS_REGION,
    EVALUATION_JUDGE_MAX_TOKENS,
    EVALUATION_JUDGE_MODEL_ID,
    EVALUATION_JUDGE_THINKING_BUDGET_TOKENS,
    EVALUATION_JUDGE_TIMEOUT_SECONDS,
    MODEL_MAX_RETRIES,
    MODEL_TOKEN_PRICES,
)
from model_usage import handle_model_invocation, model_cost
from models import JudgeScores, RunRecord, ToolEvent

JUDGE_SCORE_FIELDS = (
    "llm_answer_correctness_grade",
    "workflow_correctness",
    "chronological_coherence",
    "workflow_efficiency",
    "auditability",
)
PIPELINE_OUTPUT_NAMES = {
    "ResearchSubmission",
    "MathSubmission",
    "VerifierSubmission",
    "MonolithicAnswer",
}
JUDGE_SYSTEM_PROMPT = """You are evaluating an observable financial-research run.
Return five scores from 1 to 5 and a short explanation. Use only the supplied
question, gold requirements, deterministic checks, final answer, and tool events.

Score meanings:
1 = materially wrong or unusable
2 = major problems
3 = adequate but has important weaknesses
4 = correct with only minor weaknesses
5 = fully correct, coherent, efficient, and easy to audit

Categories:
- llm_answer_correctness_grade: semantic correctness and completeness of the
  final response,
  including whether each number describes the right financial concept.
- workflow_correctness: whether the visible evidence and calculations form a valid
  solution to the question.
- chronological_coherence: whether dependent work happens after its inputs exist.
  Independent lookups or calculations may occur in any order and must not be
  penalized for differing from the order in the gold record. For a decomposed run,
  check that a repaired Math attempt follows verifier feedback and that the final
  answer uses the accepted Math attempt.
- workflow_efficiency: whether calls are relevant and avoid unnecessary repetition.
  Do not penalize one justified Math repair; penalize unnecessary repetition.
- auditability: whether a reviewer can reconstruct the evidence and calculations
  that produced the answer. Score auditability independently from correctness: an
  incorrect answer can still be highly auditable when the trace clearly exposes
  where it went wrong.

Financial precision is strict, but keep deterministic correctness separate from
your grade. Never excuse a numeric, unit, or period mismatch in your explanation.
Do not infer hidden reasoning. Do not penalize a valid alternative ordering or
equivalent workflow merely because it differs from the gold presentation order.
Keep the explanation specific and at most three sentences. Return only structured
output.
"""


def _build_judge_model() -> Model:
    if EVALUATION_JUDGE_THINKING_BUDGET_TOKENS >= EVALUATION_JUDGE_MAX_TOKENS:
        raise ValueError("Judge thinking budget must be below its maximum tokens")
    return build_model(
        provider="bedrock",
        model_id=EVALUATION_JUDGE_MODEL_ID,
        region_name=AWS_REGION,
        timeout_seconds=EVALUATION_JUDGE_TIMEOUT_SECONDS,
        max_tokens=EVALUATION_JUDGE_MAX_TOKENS,
        temperature=None,
        model_parameters={
            "additional_request_fields": {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": EVALUATION_JUDGE_THINKING_BUDGET_TOKENS,
                }
            }
        },
    )


def _compact_tool_result(event: ToolEvent) -> Any:
    if not isinstance(event.result, dict):
        return event.result
    if event.name == "lookup_fact":
        return {
            "facts": [
                {
                    key: fact.get(key)
                    for key in ("evidence_id", "concept", "value", "unit", "period")
                }
                for fact in event.result.get("facts", [])
                if isinstance(fact, dict)
            ]
        }
    if event.name == "search_filing":
        return {
            "evidence_ids": [
                record.get("evidence_id")
                for record in event.result.get("records", [])
                if isinstance(record, dict)
            ]
        }
    return event.result


def _compact_pipeline_timeline(record: RunRecord) -> list[dict[str, Any]]:
    """Keep observable stage outputs and retries needed for workflow grading."""
    timeline: list[dict[str, Any]] = []
    for sequence, event in enumerate(record.observable_timeline, start=1):
        event_type = event.get("type")
        if event_type == "model_call":
            visible_output = []
            for block in event.get("output", []):
                if "text" in block:
                    visible_output.append({"text": block["text"]})
                elif "toolUse" in block:
                    tool_use = block["toolUse"]
                    if tool_use.get("name") in PIPELINE_OUTPUT_NAMES:
                        visible_output.append(
                            {
                                "structured_output": {
                                    "name": tool_use.get("name"),
                                    "value": tool_use.get("input"),
                                }
                            }
                        )
            if visible_output:
                timeline.append(
                    {
                        "sequence": sequence,
                        "type": "model_output",
                        "agent_stage": event.get("agent_stage"),
                        "agent_attempt": event.get("agent_attempt"),
                        "output": visible_output,
                    }
                )
        elif event_type == "retry":
            timeline.append(
                {
                    "sequence": sequence,
                    "type": "provider_retry",
                    "agent_stage": event.get("agent_stage"),
                    "agent_attempt": event.get("agent_attempt"),
                    "reason": event.get("exception") or event.get("stop_reason"),
                }
            )
    return timeline


def _judge_payload(
    record: RunRecord,
    gold: dict[str, Any],
    deterministic_row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "question": {
            "id": record.question.question_id,
            "category": record.question.category,
            "company": record.question.company,
            "text": record.question.question,
        },
        "pipeline": {
            "architecture": record.architecture,
            "variant": record.pipeline_variant,
            "accepted_math_attempt": record.accepted_math_attempt,
        },
        "gold_requirements": {
            "answer": gold["answer"],
            "calculations": gold.get("gold_calculations", []),
            "evidence_ids": gold.get("gold_evidence_ids", []),
            "required_tools": gold.get("required_tools", []),
        },
        "deterministic_checks": {
            key: deterministic_row.get(key)
            for key in (
                "status",
                "numerical_correct",
                "numerical_issues",
                "unexpected_values",
                "evidence_valid",
                "evidence_complete",
                "periods_valid",
                "formula_correct",
                "formatting_correct",
                "unsupported_values",
                "audit_trail_complete",
            )
        },
        "generated_answer": (
            record.answer.model_dump() if record.answer is not None else None
        ),
        "chronological_agent_timeline": _compact_pipeline_timeline(record),
        "chronological_tool_events": [
            {
                "sequence": event.sequence,
                "agent_stage": event.agent_stage,
                "agent_attempt": event.agent_attempt,
                "name": event.name,
                "arguments": event.arguments,
                "success": event.success,
                "result": _compact_tool_result(event),
                "error": event.error,
            }
            for event in record.tool_events
        ],
    }


class EvaluationJudge:
    """Own one Haiku judge and evaluate multiple independent saved runs."""

    def __init__(self, model: Model | None = None) -> None:
        self.model_id = EVALUATION_JUDGE_MODEL_ID
        self.retry_tracking = RetryTrackingPlugin()
        self.agent = Agent(
            model=model or _build_judge_model(),
            system_prompt=JUDGE_SYSTEM_PROMPT,
            tools=[],
            callback_handler=None,
            name="financial-run-judge",
            retry_strategy=ProviderRetryStrategy(max_attempts=MODEL_MAX_RETRIES + 1),
            plugins=[self.retry_tracking],
            structured_output_model=JudgeScores,
            trace_attributes={"role": "evaluation-judge"},
        )

    def score(
        self,
        record: RunRecord,
        gold: dict[str, Any],
        deterministic_row: dict[str, Any],
    ) -> dict[str, Any]:
        """Score one saved run against its frozen gold requirements.

        Args:
            record: Observable run record to grade.
            gold: Frozen gold record for the same question.
            deterministic_row: Existing deterministic metric results.

        Returns:
            Persistable judge result containing the grades and failure state.
        """
        self.agent.messages.clear()
        self.retry_tracking.reset()
        payload = _judge_payload(record, gold, deterministic_row)
        score_row = {
            "run_id": record.run_id,
            "question_id": record.question.question_id,
            "category": record.question.category,
            "judge_model": self.model_id,
        }
        try:
            result = self.agent(json.dumps(payload, ensure_ascii=False))
            if not isinstance(result.structured_output, JudgeScores):
                raise RuntimeError("Judge did not return the required scores")
            scores = result.structured_output
            score_row = {
                **score_row,
                **scores.model_dump(),
                "failure": None,
            }
        except Exception as error:
            score_row = {
                **score_row,
                **{field: None for field in JUDGE_SCORE_FIELDS},
                "explanation": "",
                "failure": f"{type(error).__name__}: {error}",
            }
        token_usage, model_calls = extract_model_metrics(
            self.agent.event_loop_metrics.get_summary(), self.model_id
        )
        call_usages = [model_call.token_usage for model_call in model_calls]
        if not call_usages and token_usage:
            call_usages = [token_usage]
        for call_usage in call_usages:
            handle_model_invocation(self.model_id, call_usage)
        pricing, cost = model_cost(
            token_usage, *MODEL_TOKEN_PRICES.get(self.model_id, (None, None))
        )
        score_row.update(
            {
                "model_invocations": len(call_usages),
                "token_usage": token_usage,
                "pricing_usd_per_million_tokens": pricing,
                "cost_usd": cost,
                "retries": self.retry_tracking.count,
            }
        )
        return score_row


def evaluate_with_llm_judge(
    records: list[RunRecord],
    gold_records: list[dict[str, Any]],
    deterministic_rows: list[dict[str, Any]],
    judge: EvaluationJudge | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Judge a batch and calculate average qualitative scores.

    Args:
        records: Saved agent runs.
        gold_records: Frozen gold records for the same questions.
        deterministic_rows: Per-question deterministic evaluation rows.
        judge: Optional reusable judge, primarily for controlled tests.

    Returns:
        Per-run judge rows and aggregate judge metrics.
    """
    gold_by_id = {item["question_id"]: item for item in gold_records}
    deterministic_by_run_id = {item["run_id"]: item for item in deterministic_rows}
    evaluator = judge or EvaluationJudge()
    rows = [
        evaluator.score(
            record,
            gold_by_id[record.question.question_id],
            deterministic_by_run_id[record.run_id],
        )
        for record in records
    ]
    successful = [row for row in rows if row["failure"] is None]
    known_costs = [
        Decimal(str(row["cost_usd"]["total"]))
        for row in rows
        if row.get("cost_usd", {}).get("total") is not None
    ]
    total_cost = sum(known_costs, Decimal("0"))
    summary = {
        "judge_run_count": len(rows),
        "judge_success_rate": len(successful) / len(rows) if rows else 0.0,
        "judge_total_model_invocations": sum(
            int(row["model_invocations"]) for row in rows
        ),
        "judge_total_input_tokens": sum(
            int(row["token_usage"].get("inputTokens", 0)) for row in rows
        ),
        "judge_total_output_tokens": sum(
            int(row["token_usage"].get("outputTokens", 0)) for row in rows
        ),
        "judge_total_reasoning_tokens": sum(
            int(row["token_usage"].get("reasoningTokens", 0)) for row in rows
        ),
        "judge_total_retries": sum(int(row["retries"]) for row in rows),
        "judge_total_estimated_cost_usd": str(total_cost),
        "judge_average_estimated_cost_usd": str(
            total_cost / len(known_costs) if known_costs else Decimal("0")
        ),
        "judge_cost_coverage_rate": (len(known_costs) / len(rows) if rows else 0.0),
        **{
            f"average_judge_{field}": (
                mean(float(row[field]) for row in successful) if successful else None
            )
            for field in JUDGE_SCORE_FIELDS
        },
    }
    summary["judge_by_category"] = {
        category: {
            "run_count": len(category_rows),
            **{
                f"average_{field}": mean(float(row[field]) for row in category_rows)
                for field in JUDGE_SCORE_FIELDS
            },
        }
        for category in sorted({row["category"] for row in successful})
        if (category_rows := [row for row in successful if row["category"] == category])
    }
    return rows, summary

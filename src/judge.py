"""Grade qualitative run characteristics with one reusable LLM judge."""

from __future__ import annotations

import json
from statistics import mean
from typing import Any

from strands import Agent, ModelRetryStrategy
from strands.models import Model

from agent.plugins import RetryTrackingPlugin
from agent.providers import build_model
from config import (
    AWS_REGION,
    BEDROCK_MANTLE_TIMEOUT_SECONDS,
    EVALUATION_JUDGE_MAX_TOKENS,
    EVALUATION_JUDGE_MODEL_ID,
    MODEL_MAX_RETRIES,
)
from models import JudgeScores, RunRecord, ToolEvent

JUDGE_SCORE_FIELDS = (
    "llm_answer_correctness_grade",
    "workflow_correctness",
    "chronological_coherence",
    "workflow_efficiency",
    "auditability",
)
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
  penalized for differing from the order in the gold record.
- workflow_efficiency: whether calls are relevant and avoid unnecessary repetition.
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
    return build_model(
        provider="bedrock",
        model_id=EVALUATION_JUDGE_MODEL_ID,
        region_name=AWS_REGION,
        timeout_seconds=BEDROCK_MANTLE_TIMEOUT_SECONDS,
        max_tokens=EVALUATION_JUDGE_MAX_TOKENS,
        temperature=0,
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
                "unsupported_values",
            )
        },
        "generated_answer": (
            record.answer.model_dump() if record.answer is not None else None
        ),
        "chronological_tool_events": [
            {
                "sequence": event.sequence,
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
            retry_strategy=ModelRetryStrategy(max_attempts=MODEL_MAX_RETRIES + 1),
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
            return {
                **score_row,
                **scores.model_dump(),
                "failure": None,
            }
        except Exception as error:
            return {
                **score_row,
                **{field: None for field in JUDGE_SCORE_FIELDS},
                "explanation": "",
                "failure": f"{type(error).__name__}: {error}",
            }


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
    summary = {
        "judge_run_count": len(rows),
        "judge_success_rate": len(successful) / len(rows) if rows else 0.0,
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

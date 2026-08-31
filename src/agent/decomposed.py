"""Run a three-stage SEC research pipeline with one shared run record."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

from strands import Agent
from strands.models import Model

from agent.data import load_questions
from agent.monolithic import (
    CALCULATION_RULES,
    EVIDENCE_RULES,
    FINAL_ANSWER_RULES,
    categorize_failure,
)
from agent.observation import (
    build_observable_timeline,
    check_langfuse,
    count_structured_output_repairs,
    create_trace,
    extract_model_metrics,
    extract_observable_messages,
    observe_run,
    propagate_run_attributes,
)
from agent.plugins import ProviderRetryStrategy, RetryTrackingPlugin
from agent.providers import ModelProvider, build_model
from agent.tools import ProjectTools
from config import (
    AGENT_MAX_OUTPUT_TOKENS,
    AGENT_MAX_TOTAL_TOKENS,
    AGENT_MAX_TURNS,
    AWS_REGION,
    BEDROCK_MANTLE_TIMEOUT_SECONDS,
    BEDROCK_NOVA_LITE_MODEL_ID,
    BEDROCK_SONNET_MODEL_ID,
    DECOMPOSED_MATH_MAX_TOKENS,
    DECOMPOSED_MATH_THINKING_BUDGET_TOKENS,
    DECOMPOSED_RESEARCH_MAX_TOKENS,
    DECOMPOSED_VERIFIER_MAX_TOKENS,
    DECOMPOSED_VERIFIER_THINKING_BUDGET_TOKENS,
    MODEL_MAX_RETRIES,
    MODEL_TOKEN_PRICES,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_PROVIDER,
    OPENROUTER_REASONING_EFFORT,
    OPENROUTER_TIMEOUT_SECONDS,
    QDRANT_COLLECTION,
    RUNS_DIR,
)
from model_usage import handle_model_invocation, model_cost
from models import (
    MathSubmission,
    ModelCallEvent,
    MonolithicAnswer,
    ResearchSubmission,
    RunRecord,
    VerifierSubmission,
)
from retrieval import check_qdrant
from utils import read_jsonl

RESEARCH_PROMPT = f"""You are the research stage of a read-only SEC filing
pipeline. Use only the supplied frozen-data tools. Do not give financial advice,
use outside knowledge, calculate, or answer the user directly.

{EVIDENCE_RULES}

Return the exact selected values, units, periods, and only evidence IDs returned by
the tools. Describe the calculation the math stage must perform, but do not perform
it yourself. If evidence is missing or ambiguous, return an empty facts list and
explain that in calculation_request.
"""

MATH_PROMPT = f"""You are the math stage of a read-only SEC filing pipeline. You
receive a frozen research package. Use only its listed values and the calculate
tool. Do not retrieve evidence, use outside knowledge, or write the final answer.

{CALCULATION_RULES}

If verifier feedback is present, correct only the stated calculation issue. If the
research package cannot support a calculation, return no calculations.
"""

MATH_WRITER_PROMPT = f"""You are the math and final-answer stage of a read-only SEC
filing pipeline without a separate verifier. You receive a frozen research package.
Use only its listed values and the calculate tool. Do not retrieve evidence, use
outside knowledge, or invent facts, evidence IDs, values, or calculations.

{CALCULATION_RULES}
{FINAL_ANSWER_RULES}

Answer the original question using only the research package and calculator
results. Return only the requested structured output, keep the answer concise, and
do not include hidden reasoning.
"""

VERIFIER_PROMPT = f"""You are the final verifier and writer of a read-only SEC
filing pipeline. You receive the question, the complete research package, and the
complete calculator output. Do not use outside knowledge, retrieve more evidence,
or invent facts, evidence IDs, values, or calculations.

Check whether the visible evidence, periods, units, and calculator results answer
the question and follow the calculation rules below. Approve only when they do.

{CALCULATION_RULES}
{FINAL_ANSWER_RULES}

If calculation is wrong but evidence is sufficient, reject with retry_stage="math"
and precise feedback for the math stage. If evidence is missing, ambiguous,
wrong-period, or wrong-unit, reject with retry_stage="none" because Research is
not retried. A rejected result has no answer. Return only the requested structured
output, keep the answer concise, and do not include hidden reasoning.
"""

StageName = Literal["research", "math", "verifier"]


@dataclass
class _StageResult:
    stage: StageName
    attempt: int
    output: Any | None
    error: Exception | None
    token_usage: dict[str, int]
    model_calls: list[ModelCallEvent]
    timeline: list[dict[str, Any]]
    retry_events: list[dict[str, Any]]
    retries: int


def _stage_model(
    provider: ModelProvider,
    model_id: str,
    max_tokens: int,
    thinking_budget_tokens: int = 0,
) -> Model:
    """Build one stage model, enabling Bedrock thinking for Sonnet only."""
    if provider == "bedrock":
        parameters: dict[str, Any] | None = None
        if model_id == BEDROCK_SONNET_MODEL_ID and thinking_budget_tokens > 0:
            if thinking_budget_tokens >= max_tokens:
                raise ValueError("Thinking budget must be below maximum tokens")
            parameters = {
                "additional_request_fields": {
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": thinking_budget_tokens,
                    }
                }
            }
        return build_model(
            provider=provider,
            model_id=model_id,
            region_name=AWS_REGION,
            timeout_seconds=BEDROCK_MANTLE_TIMEOUT_SECONDS,
            max_tokens=max_tokens,
            model_parameters=parameters,
        )
    return build_model(
        provider=provider,
        model_id=model_id,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout_seconds=OPENROUTER_TIMEOUT_SECONDS,
        max_tokens=max_tokens,
        model_parameters={
            "extra_body": {
                "reasoning": {
                    "enabled": True,
                    "effort": OPENROUTER_REASONING_EFFORT,
                },
                "provider": {
                    "only": [OPENROUTER_PROVIDER],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                },
            }
        },
    )


def _merge_token_usage(usages: list[dict[str, int]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for usage in usages:
        for name, value in usage.items():
            totals[name] = totals.get(name, 0) + value
    return totals


class DecomposedAgentHarness:
    """Own the configured decomposed agents for independent questions.

    Args:
        provider: Model provider used for all three stages.
        model_id: Optional provider model identifier.
        enable_verifier: Whether a separate verifier writes the final answer.
        enable_math_repair: Whether the verifier may request one Math retry.
    """

    def __init__(
        self,
        provider: ModelProvider = "bedrock",
        model_id: str | None = None,
        *,
        enable_verifier: bool = True,
        enable_math_repair: bool = True,
    ) -> None:
        if enable_math_repair and not enable_verifier:
            raise ValueError("Math repair requires the verifier")
        self.provider = provider
        self.model_id = model_id or (
            BEDROCK_NOVA_LITE_MODEL_ID if provider == "bedrock" else OPENROUTER_MODEL
        )
        self.research_reasoning_mode = "none"
        self.math_reasoning_mode = (
            "enabled"
            if (
                self.model_id == BEDROCK_SONNET_MODEL_ID
                and DECOMPOSED_MATH_THINKING_BUDGET_TOKENS > 0
            )
            else "none"
        )
        self.verifier_reasoning_mode = (
            "enabled"
            if (
                self.model_id == BEDROCK_SONNET_MODEL_ID
                and DECOMPOSED_VERIFIER_THINKING_BUDGET_TOKENS > 0
            )
            else "none"
        )
        self.enable_verifier = enable_verifier
        self.enable_math_repair = enable_math_repair
        if not enable_verifier:
            self.pipeline_variant = "no_verification"
        elif enable_math_repair:
            self.pipeline_variant = "verification_with_repair"
        else:
            self.pipeline_variant = "verification_without_repair"
        if not enable_verifier:
            self.verifier_reasoning_mode = "disabled"
        self.run_records_path = (
            RUNS_DIR / f"decomposed_{self.pipeline_variant}_{provider}.jsonl"
        )
        self._questions_by_text = {
            question.question: question for question in load_questions()
        }
        self.research_tools = ProjectTools(agent_stage="research")
        self.math_tools = ProjectTools(agent_stage="math")
        self.research_retry_tracking = RetryTrackingPlugin()
        self.math_retry_tracking = RetryTrackingPlugin()
        self.verifier_retry_tracking = RetryTrackingPlugin()
        self._ready = False
        self.research_agent = Agent(
            model=_stage_model(provider, self.model_id, DECOMPOSED_RESEARCH_MAX_TOKENS),
            system_prompt=RESEARCH_PROMPT,
            tools=[
                self.research_tools.search_filing,
                self.research_tools.lookup_fact,
            ],
            callback_handler=None,
            name="decomposed-sec-research",
            retry_strategy=ProviderRetryStrategy(max_attempts=MODEL_MAX_RETRIES + 1),
            plugins=[self.research_retry_tracking],
            structured_output_model=ResearchSubmission,
            trace_attributes={
                "architecture": "decomposed",
                "pipeline_variant": self.pipeline_variant,
                "stage": "research",
            },
        )
        self.math_agent = Agent(
            model=_stage_model(
                provider,
                self.model_id,
                DECOMPOSED_MATH_MAX_TOKENS,
                DECOMPOSED_MATH_THINKING_BUDGET_TOKENS,
            ),
            system_prompt=MATH_PROMPT if enable_verifier else MATH_WRITER_PROMPT,
            tools=[self.math_tools.calculate],
            callback_handler=None,
            name=(
                "decomposed-sec-math"
                if enable_verifier
                else "decomposed-sec-math-writer"
            ),
            retry_strategy=ProviderRetryStrategy(max_attempts=MODEL_MAX_RETRIES + 1),
            plugins=[self.math_retry_tracking],
            structured_output_model=(
                MathSubmission if enable_verifier else MonolithicAnswer
            ),
            trace_attributes={
                "architecture": "decomposed",
                "pipeline_variant": self.pipeline_variant,
                "stage": "math",
            },
        )
        self.verifier_agent = (
            Agent(
                model=_stage_model(
                    provider,
                    self.model_id,
                    DECOMPOSED_VERIFIER_MAX_TOKENS,
                    DECOMPOSED_VERIFIER_THINKING_BUDGET_TOKENS,
                ),
                system_prompt=VERIFIER_PROMPT,
                tools=[],
                callback_handler=None,
                name="decomposed-sec-verifier",
                retry_strategy=ProviderRetryStrategy(
                    max_attempts=MODEL_MAX_RETRIES + 1
                ),
                plugins=[self.verifier_retry_tracking],
                structured_output_model=VerifierSubmission,
                trace_attributes={
                    "architecture": "decomposed",
                    "pipeline_variant": self.pipeline_variant,
                    "stage": "verifier",
                },
            )
            if enable_verifier
            else None
        )

    def _reset_for_question(self) -> None:
        for agent in (self.research_agent, self.math_agent, self.verifier_agent):
            if agent is not None:
                agent.messages.clear()
        self.research_tools.reset_events()
        self.math_tools.reset_events()
        self.research_retry_tracking.reset()
        self.math_retry_tracking.reset()
        self.verifier_retry_tracking.reset()

    def _run_stage(
        self,
        stage: StageName,
        attempt: int,
        agent: Agent,
        retry_tracking: RetryTrackingPlugin,
        prompt: str,
    ) -> _StageResult:
        agent.messages.clear()
        retry_tracking.reset()
        output: Any | None = None
        error: Exception | None = None
        try:
            result = agent(
                prompt,
                limits={
                    "turns": AGENT_MAX_TURNS,
                    "output_tokens": AGENT_MAX_OUTPUT_TOKENS,
                    "total_tokens": AGENT_MAX_TOTAL_TOKENS,
                },
            )
            if result.stop_reason.startswith("limit_"):
                raise RuntimeError(f"{stage} stopped at {result.stop_reason}")
            output = result.structured_output
        except Exception as caught:
            error = caught
        token_usage, model_calls = extract_model_metrics(
            agent.event_loop_metrics.get_summary(), self.model_id
        )
        staged_model_calls = [
            call.model_copy(update={"agent_stage": stage, "agent_attempt": attempt})
            for call in model_calls
        ]
        timeline = [
            {**event, "agent_stage": stage, "agent_attempt": attempt}
            for event in build_observable_timeline(
                agent.system_prompt,
                agent.tool_registry.get_all_tools_config(),
                extract_observable_messages(agent.messages),
                staged_model_calls,
                retry_tracking.events,
            )
        ]
        return _StageResult(
            stage=stage,
            attempt=attempt,
            output=output,
            error=error,
            token_usage=token_usage,
            model_calls=staged_model_calls,
            timeline=timeline,
            retry_events=[
                {**event, "agent_stage": stage, "agent_attempt": attempt}
                for event in retry_tracking.events
            ],
            retries=retry_tracking.count,
        )

    def _record_model_usage(self, stages: list[_StageResult]) -> None:
        for stage in stages:
            usages = [call.token_usage for call in stage.model_calls]
            if not usages and stage.token_usage:
                usages = [stage.token_usage]
            for usage in usages:
                handle_model_invocation(self.model_id, usage)

    def preflight(self) -> dict[str, Any]:
        """Verify provider configuration, Langfuse, and frozen retrieval once."""
        if not self._ready:
            check_langfuse()
            check_qdrant()
            self._ready = True
        return {
            "ready": True,
            "provider": self.provider,
            "model": self.model_id,
            "architecture": "decomposed",
            "pipeline_variant": self.pipeline_variant,
            "reasoning": {
                "research": self.research_reasoning_mode,
                "math": self.math_reasoning_mode,
                "verifier": self.verifier_reasoning_mode,
            },
            "langfuse": "authenticated",
            "qdrant_collection": QDRANT_COLLECTION,
        }

    def _run_research(
        self, question: str, company: str, ticker: str, attempt: int
    ) -> _StageResult:
        self.research_tools.set_agent_attempt(attempt)
        return self._run_stage(
            "research",
            attempt,
            self.research_agent,
            self.research_retry_tracking,
            f"Company: {company}\nTicker: {ticker}\nQuestion: {question}",
        )

    def _run_math(
        self,
        question: str,
        research: ResearchSubmission,
        attempt: int,
        feedback: str = "",
    ) -> _StageResult:
        self.math_tools.set_agent_attempt(attempt)
        payload: dict[str, Any] = {
            "question": question,
            "research": research.model_dump(),
        }
        if feedback:
            payload["verifier_feedback"] = feedback
        return self._run_stage(
            "math",
            attempt,
            self.math_agent,
            self.math_retry_tracking,
            json.dumps(payload),
        )

    def _run_verifier(
        self,
        question: str,
        research: ResearchSubmission,
        math: MathSubmission,
        attempt: int,
    ) -> _StageResult:
        if self.verifier_agent is None:
            raise RuntimeError("Verifier is disabled for this pipeline variant")
        return self._run_stage(
            "verifier",
            attempt,
            self.verifier_agent,
            self.verifier_retry_tracking,
            json.dumps(
                {
                    "question": question,
                    "research": research.model_dump(),
                    "math": math.model_dump(),
                }
            ),
        )

    def run_question(self, question: str, *, persist: bool = True) -> RunRecord:
        """Run one question through the configured decomposed stages."""
        self.preflight()
        question_record = self._questions_by_text.get(question)
        if question_record is None:
            raise ValueError("Question is not part of the frozen benchmark data")
        self._reset_for_question()
        timer = perf_counter()
        started_at = datetime.now(UTC)
        run_id = (
            f"{question_record.question_id}-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}"
        )
        trace_id, trace_url = create_trace(run_id)
        stages: list[_StageResult] = []
        failure_category: str | None = None
        failure_message: str | None = None
        answer: MonolithicAnswer | None = None
        accepted_math_attempt: int | None = None
        verifier: _StageResult | None = None
        with observe_run(
            trace_id=trace_id,
            question=question_record,
            provider=self.provider,
            model_id=self.model_id,
            reasoning_mode=(
                f"research={self.research_reasoning_mode}, "
                f"math={self.math_reasoning_mode}, "
                f"verifier={self.verifier_reasoning_mode}"
            ),
            architecture="decomposed",
            pipeline_variant=self.pipeline_variant,
        ) as root_span:
            with propagate_run_attributes(question_record, "decomposed"):
                research = self._run_research(
                    question_record.question,
                    question_record.company,
                    question_record.ticker,
                    attempt=1,
                )
                stages.append(research)
                if research.error is None and isinstance(
                    research.output, ResearchSubmission
                ):
                    math = self._run_math(
                        question_record.question, research.output, attempt=1
                    )
                    stages.append(math)
                else:
                    math = None
                if (
                    self.enable_verifier
                    and math
                    and math.error is None
                    and isinstance(math.output, MathSubmission)
                ):
                    verifier = self._run_verifier(
                        question_record.question,
                        research.output,
                        math.output,
                        attempt=1,
                    )
                    stages.append(verifier)
                if (
                    self.enable_math_repair
                    and self.enable_verifier
                    and verifier is not None
                    and verifier.error is None
                    and isinstance(verifier.output, VerifierSubmission)
                    and not verifier.output.approved
                    and verifier.output.retry_stage == "math"
                ):
                    feedback = (
                        verifier.output.feedback or verifier.output.rejection_reason
                    )
                    if isinstance(research.output, ResearchSubmission):
                        math = self._run_math(
                            question_record.question,
                            research.output,
                            attempt=2,
                            feedback=feedback or "",
                        )
                        stages.append(math)

                    if (
                        math
                        and math.error is None
                        and isinstance(math.output, MathSubmission)
                        and isinstance(research.output, ResearchSubmission)
                    ):
                        verifier = self._run_verifier(
                            question_record.question,
                            research.output,
                            math.output,
                            attempt=2,
                        )
                        stages.append(verifier)
            failed_stage = next((stage for stage in stages if stage.error), None)
            if failed_stage is not None:
                failure_category = (
                    f"{failed_stage.stage}_{categorize_failure(failed_stage.error)}"
                )
                failure_message = str(failed_stage.error)
            elif (
                not self.enable_verifier
                and math
                and isinstance(math.output, MonolithicAnswer)
            ):
                answer = math.output
                accepted_math_attempt = math.attempt
                root_span.update(output=answer.model_dump())
            elif not verifier or not isinstance(verifier.output, VerifierSubmission):
                failure_category = "pipeline_incomplete"
                failure_message = "A required pipeline stage did not return output"
            elif not verifier.output.approved:
                failure_category = "verifier_rejected"
                failure_message = verifier.output.rejection_reason
            else:
                answer = verifier.output.answer
                accepted_math_attempt = math.attempt if math is not None else None
                root_span.update(output=answer.model_dump() if answer else {})
            if failure_category:
                root_span.update(
                    level="ERROR",
                    status_message=failure_category,
                    output={"error": failure_message},
                )
        self._record_model_usage(stages)
        token_usage = _merge_token_usage([stage.token_usage for stage in stages])
        model_calls = [
            call.model_copy(update={"sequence": sequence})
            for sequence, call in enumerate(
                (call for stage in stages for call in stage.model_calls), start=1
            )
        ]
        tool_events = [
            event.model_copy(update={"sequence": sequence})
            for sequence, event in enumerate(
                [*self.research_tools.get_events(), *self.math_tools.get_events()],
                start=1,
            )
        ]
        timeline = [event for stage in stages for event in stage.timeline]
        retry_events = [event for stage in stages for event in stage.retry_events]
        pricing, cost = model_cost(
            token_usage, *MODEL_TOKEN_PRICES.get(self.model_id, (None, None))
        )
        record = RunRecord(
            run_id=run_id,
            started_at=started_at.isoformat(),
            question=question_record,
            architecture="decomposed",
            pipeline_variant=self.pipeline_variant,
            provider=self.provider,
            model=self.model_id,
            reasoning_mode=(
                f"research={self.research_reasoning_mode}, "
                f"math={self.math_reasoning_mode}, "
                f"verifier={self.verifier_reasoning_mode}"
            ),
            status="success" if answer is not None else "failure",
            answer=answer,
            accepted_math_attempt=accepted_math_attempt,
            model_calls=model_calls,
            tool_events=tool_events,
            observable_timeline=timeline,
            retry_events=retry_events,
            failure_category=failure_category,
            failure_message=failure_message,
            token_usage=token_usage,
            pricing_usd_per_million_tokens=pricing,
            cost_usd=cost,
            latency_ms=round((perf_counter() - timer) * 1000, 2),
            retries=sum(stage.retries for stage in stages),
            structured_output_repairs=sum(
                count_structured_output_repairs(stage.timeline) for stage in stages
            ),
            langfuse_trace_id=trace_id,
            langfuse_trace_url=trace_url,
        )
        if persist:
            self.run_records_path.parent.mkdir(parents=True, exist_ok=True)
            with self.run_records_path.open("a", encoding="utf-8") as output:
                output.write(record.model_dump_json() + "\n")
        return record

    def load_records(self) -> list[RunRecord]:
        """Load saved records for this harness's provider."""
        if not self.run_records_path.exists():
            return []
        return [
            RunRecord.model_validate(record)
            for record in read_jsonl(self.run_records_path)
        ]

    def run_batch(self, ticker: str) -> list[RunRecord]:
        """Run one company's frozen questions sequentially."""
        records: list[RunRecord] = []
        for question in load_questions(ticker):
            record = self.run_question(question.question)
            records.append(record)
            if record.failure_category in {
                "research_provider_authentication",
                "research_provider_rate_limit",
            }:
                break
        return records

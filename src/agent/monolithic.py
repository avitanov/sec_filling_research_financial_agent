"""Run the Phase 1 monolithic Strands agent with local audit records."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any

from strands import Agent, ModelRetryStrategy
from strands.models import Model

from agent.data import load_questions
from agent.observation import (
    build_observable_timeline,
    check_langfuse,
    create_trace,
    extract_model_metrics,
    extract_observable_messages,
    observe_monolithic_run,
    propagate_monolithic_attributes,
)
from agent.plugins import RetryTrackingPlugin
from agent.providers import ModelProvider, build_model
from agent.tools import ProjectTools
from config import (
    AGENT_MAX_OUTPUT_TOKENS,
    AGENT_MAX_TOTAL_TOKENS,
    AGENT_MAX_TURNS,
    AWS_REGION,
    BEDROCK_MANTLE_TIMEOUT_SECONDS,
    BEDROCK_NOVA_LITE_MODEL_ID,
    BEDROCK_REASONING_MODE,
    MODEL_MAX_RETRIES,
    MODEL_MAX_TOKENS,
    NOVA_LITE_INPUT_USD_PER_MILLION_TOKENS,
    NOVA_LITE_OUTPUT_USD_PER_MILLION_TOKENS,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_PROVIDER,
    OPENROUTER_REASONING_EFFORT,
    OPENROUTER_TIMEOUT_SECONDS,
    QDRANT_COLLECTION,
    RUNS_DIR,
)
from models import MonolithicAnswer, RunRecord
from retrieval import check_qdrant
from utils import read_jsonl

SYSTEM_PROMPT = """You are a read-only SEC filing research agent. You specialize in
finding and interpreting evidence from frozen 10-K filings, selecting normalized
company facts, and answering factual financial questions with exact calculations.

Your role is research only. Never provide financial advice, investment
recommendations, stock-price predictions, or assistance with trades or purchases.

Operating rules:
- Use only the supplied tools and frozen local data. Do not use outside knowledge,
  access external sources, or modify data.
- Use evidence only for the company in the question. Never combine companies.
- Follow the tool descriptions and use the calculator for every arithmetic step,
  including intermediate calculations.
- For normalized consolidated revenue, operating income, net income, total assets,
  and operating cash flow, use lookup_fact first. Use search_filing for values or
  context that are not available as normalized facts, such as segment disclosures.
- Select evidence that matches the requested period and unit. Do not silently choose
  between ambiguous or conflicting facts.
- Ground every reported number in retrieved evidence or a calculator result. Include
  only evidence IDs actually returned by the tools.
- Preserve exact values, periods, units, evidence IDs, and calculator operations,
  inputs, and results in the structured output.
- If the available evidence cannot support an answer, say so instead of guessing or
  introducing unsupported numbers.
- Keep the answer concise. Return only the requested structured output and never
  include hidden reasoning.
"""


def _configured_model(provider: ModelProvider, model_id: str) -> Model:
    if provider == "bedrock":
        return build_model(
            provider=provider,
            model_id=model_id,
            region_name=AWS_REGION,
            timeout_seconds=BEDROCK_MANTLE_TIMEOUT_SECONDS,
            max_tokens=MODEL_MAX_TOKENS,
        )
    return build_model(
        provider=provider,
        model_id=model_id,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout_seconds=OPENROUTER_TIMEOUT_SECONDS,
        max_tokens=MODEL_MAX_TOKENS,
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
            },
        },
    )


def _failure_category(error: Exception) -> str:
    name = type(error).__name__.lower()
    message = str(error).lower()
    if (
        "rate" in name
        or "rate limit" in message
        or "throttl" in message
        or "429" in message
    ):
        return "provider_rate_limit"
    if (
        "auth" in name
        or "api key" in message
        or "accessdenied" in message
        or "access denied" in message
        or "credential" in message
        or "expiredtoken" in message
        or "unauthorized" in message
        or "401" in message
        or "403" in message
    ):
        return "provider_authentication"
    if "timeout" in name or "timed out" in message:
        return "provider_timeout"
    if any(
        phrase in message
        for phrase in (
            "overloaded",
            "upstream error",
            "service unavailable",
            "502",
            "503",
        )
    ):
        return "provider_unavailable"
    if "structured" in message or "validation" in name:
        return "invalid_structured_output"
    return "agent_error"


def _nova_lite_cost(
    model_id: str, token_usage: dict[str, int]
) -> tuple[dict[str, str], dict[str, str]]:
    """Calculate Nova Lite on-demand input and output charges when applicable."""
    if model_id != BEDROCK_NOVA_LITE_MODEL_ID:
        return {}, {}
    input_tokens = Decimal(token_usage.get("inputTokens", 0))
    output_tokens = Decimal(token_usage.get("outputTokens", 0))
    input_cost = (
        input_tokens * NOVA_LITE_INPUT_USD_PER_MILLION_TOKENS / Decimal("1000000")
    )
    output_cost = (
        output_tokens * NOVA_LITE_OUTPUT_USD_PER_MILLION_TOKENS / Decimal("1000000")
    )
    return (
        {
            "input": str(NOVA_LITE_INPUT_USD_PER_MILLION_TOKENS),
            "output": str(NOVA_LITE_OUTPUT_USD_PER_MILLION_TOKENS),
        },
        {
            "input": str(input_cost),
            "output": str(output_cost),
            "total": str(input_cost + output_cost),
        },
    )


class MonolithicAgentHarness:
    """Own one configured monolithic agent and run independent questions.

    Args:
        provider: Model provider used by the harness.
        model_id: Optional provider model identifier.
        reasoning_mode: Optional reasoning setting recorded in run traces.
    """

    def __init__(
        self,
        provider: ModelProvider = "bedrock",
        model_id: str | None = None,
        reasoning_mode: str | None = None,
    ) -> None:
        self.provider = provider
        self.model_id = model_id or (
            BEDROCK_NOVA_LITE_MODEL_ID if provider == "bedrock" else OPENROUTER_MODEL
        )
        self.reasoning_mode = reasoning_mode or (
            BEDROCK_REASONING_MODE
            if provider == "bedrock"
            else OPENROUTER_REASONING_EFFORT
        )
        self.run_records_path = RUNS_DIR / f"monolithic_{provider}.jsonl"
        self._questions_by_text = {
            question.question: question for question in load_questions()
        }
        self.tools = ProjectTools()
        self.retry_tracking = RetryTrackingPlugin()
        self._ready = False
        self.agent = Agent(
            model=_configured_model(provider, self.model_id),
            system_prompt=SYSTEM_PROMPT,
            tools=[
                self.tools.search_filing,
                self.tools.lookup_fact,
                self.tools.calculate,
            ],
            callback_handler=None,
            name="monolithic-sec-agent",
            retry_strategy=ModelRetryStrategy(max_attempts=MODEL_MAX_RETRIES + 1),
            plugins=[self.retry_tracking],
            structured_output_model=MonolithicAnswer,
            trace_attributes={"architecture": "monolithic"},
        )

    def _reset_for_question(self) -> None:
        self.agent.messages.clear()
        self.tools.reset_events()
        self.retry_tracking.reset()

    def preflight(self) -> dict[str, Any]:
        """Verify provider configuration, Langfuse, and retrieval once.

        Returns:
            Safe readiness details without secret values.

        Raises:
            RuntimeError: If a required service or safety check fails.
            FilingIndexError: If the frozen filing collection is unavailable.
        """
        if not self._ready:
            check_langfuse()
            check_qdrant()
            self._ready = True
        return {
            "ready": True,
            "provider": self.provider,
            "model": self.model_id,
            "reasoning_mode": self.reasoning_mode,
            "langfuse": "authenticated",
            "qdrant_collection": QDRANT_COLLECTION,
        }

    def run_question(self, question: str, *, persist: bool = True) -> RunRecord:
        """Run one benchmark question with the configured agent.

        Args:
            question: Exact question text from the frozen benchmark data.
            persist: Whether to append the result to the official local JSONL file.

        Returns:
            Complete observable run record.

        Raises:
            ValueError: If the question is not part of the frozen benchmark data.
        """
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
        answer: MonolithicAnswer | None = None
        failure_category: str | None = None
        failure_message: str | None = None
        with observe_monolithic_run(
            trace_id=trace_id,
            question=question_record,
            provider=self.provider,
            model_id=self.model_id,
            reasoning_mode=self.reasoning_mode,
        ) as root_span:
            try:
                with propagate_monolithic_attributes(question_record):
                    result = self.agent(
                        (
                            f"Company: {question_record.company}\n"
                            f"Ticker: {question_record.ticker}\n"
                            f"Question: {question_record.question}"
                        ),
                        limits={
                            "turns": AGENT_MAX_TURNS,
                            "output_tokens": AGENT_MAX_OUTPUT_TOKENS,
                            "total_tokens": AGENT_MAX_TOTAL_TOKENS,
                        },
                    )
                if result.stop_reason.startswith("limit_"):
                    raise RuntimeError(f"Agent stopped at {result.stop_reason}")
                if not isinstance(result.structured_output, MonolithicAnswer):
                    raise RuntimeError(
                        "Agent did not return the required structured output"
                    )
                answer = result.structured_output
                root_span.update(output=answer.model_dump())
            except Exception as error:
                failure_category = _failure_category(error)
                failure_message = str(error)
                root_span.update(
                    level="ERROR",
                    status_message=failure_category,
                    output={"error": failure_message},
                )

        token_usage, model_calls = extract_model_metrics(
            self.agent.event_loop_metrics.get_summary(), self.model_id
        )
        pricing, cost = _nova_lite_cost(self.model_id, token_usage)
        record = RunRecord(
            run_id=run_id,
            started_at=started_at.isoformat(),
            question=question_record,
            provider=self.provider,
            model=self.model_id,
            reasoning_mode=self.reasoning_mode,
            status="success" if answer is not None else "failure",
            answer=answer,
            model_calls=model_calls,
            tool_events=self.tools.get_events(),
            observable_timeline=build_observable_timeline(
                SYSTEM_PROMPT,
                self.agent.tool_registry.get_all_tools_config(),
                extract_observable_messages(self.agent.messages),
                model_calls,
                self.retry_tracking.events,
            ),
            retry_events=list(self.retry_tracking.events),
            failure_category=failure_category,
            failure_message=failure_message,
            token_usage=token_usage,
            pricing_usd_per_million_tokens=pricing,
            cost_usd=cost,
            latency_ms=round((perf_counter() - timer) * 1000, 2),
            retries=self.retry_tracking.count,
            langfuse_trace_id=trace_id,
            langfuse_trace_url=trace_url,
        )
        if persist:
            self.run_records_path.parent.mkdir(parents=True, exist_ok=True)
            with self.run_records_path.open("a", encoding="utf-8") as output:
                output.write(record.model_dump_json() + "\n")
        return record

    def load_records(self) -> list[RunRecord]:
        """Load saved records for this harness's provider.

        Returns:
            Saved records, or an empty list when the file does not exist.
        """
        if not self.run_records_path.exists():
            return []
        return [
            RunRecord.model_validate(record)
            for record in read_jsonl(self.run_records_path)
        ]

    def run_batch(
        self,
        ticker: str,
        *,
        skip_completed: bool = True,
    ) -> list[RunRecord]:
        """Run one company's questions sequentially with the configured agent.

        Args:
            ticker: Frozen benchmark company ticker.
            skip_completed: Skip question IDs with a saved successful run.

        Returns:
            New run records created by this call.
        """
        completed = {
            record.question.question_id
            for record in self.load_records()
            if record.status == "success"
        }
        records: list[RunRecord] = []
        for question in load_questions(ticker):
            if skip_completed and question.question_id in completed:
                continue
            record = self.run_question(question.question)
            records.append(record)
            if record.failure_category in {
                "provider_authentication",
                "provider_rate_limit",
            }:
                break
        return records

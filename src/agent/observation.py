"""Send and retrieve Langfuse observations for agent runs."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

from langfuse import get_client, propagate_attributes

from config import (
    LANGFUSE_BASE_URL,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_TRACING_ENVIRONMENT,
)
from models import ModelCallEvent, QuestionInput, RunRecord

HIDDEN_REASONING_PATTERN = re.compile(
    r"<(?:thinking|analysis)>.*?(</(?:thinking|analysis)>|$)",
    re.DOTALL | re.IGNORECASE,
)


def extract_model_metrics(
    summary: dict[str, Any], model_id: str
) -> tuple[dict[str, int], list[ModelCallEvent]]:
    """Extract per-run token usage and model calls from Strands metrics.

    Args:
        summary: Strands event-loop metrics summary.
        model_id: Model identifier recorded for each call.

    Returns:
        Token totals and individual model-call records for the latest invocation.
    """

    def integer_usage(usage: dict[str, Any]) -> dict[str, int]:
        return {
            key: int(value)
            for key, value in usage.items()
            if isinstance(value, (int, float))
        }

    invocations = summary.get("agent_invocations", [])
    if not invocations:
        return integer_usage(summary.get("accumulated_usage", {})), []

    invocation = invocations[-1]
    calls = [
        ModelCallEvent(
            sequence=sequence,
            model=model_id,
            token_usage=integer_usage(cycle.get("usage", {})),
        )
        for sequence, cycle in enumerate(invocation.get("cycles", []), start=1)
    ]
    return integer_usage(invocation.get("usage", {})), calls


def extract_observable_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the observable conversation content from a Strands message history.

    Reasoning blocks are intentionally excluded from persisted run records.

    Args:
        messages: Strands conversation messages after one agent run.

    Returns:
        Text, tool-use, and tool-result blocks in conversation order.
    """
    trace: list[dict[str, Any]] = []
    for message in messages:
        content: list[dict[str, Any]] = []
        for block in message.get("content", []):
            if "text" in block:
                text = HIDDEN_REASONING_PATTERN.sub("", str(block["text"])).strip()
                if text:
                    content.append({"text": text})
            elif "toolUse" in block or "toolResult" in block:
                content.append(block)
        if content:
            trace.append({"role": message["role"], "content": content})
    return trace


def build_observable_timeline(
    system_prompt: str,
    tool_definitions: dict[str, Any],
    messages: list[dict[str, Any]],
    model_calls: list[ModelCallEvent],
    retry_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create the ordered, persistable record of observable agent activity.

    Args:
        system_prompt: Static system prompt sent to the model.
        tool_definitions: Tool schemas available on every model call.
        messages: Observable conversation messages in order.
        model_calls: Token usage reported for model responses in this run.
        retry_events: Retry details captured from the Strands retry strategy.

    Returns:
        Chronological event dictionaries for local JSONL persistence.
    """
    timeline: list[dict[str, Any]] = [
        {"type": "system_prompt", "content": system_prompt},
        {"type": "tool_definitions", "content": tool_definitions},
    ]
    conversation: list[dict[str, Any]] = []
    model_call_index = 0
    remaining_retries = iter(retry_events)
    next_retry = next(remaining_retries, None)
    for message in messages:
        content = message["content"]
        if message["role"] == "assistant":
            model_call = None
            if model_call_index < len(model_calls):
                model_call = model_calls[model_call_index]
            model_call_index += 1
            sequence = model_call.sequence if model_call else model_call_index
            while next_retry and int(next_retry["attempt"]) <= sequence:
                timeline.append({"type": "retry", **next_retry})
                next_retry = next(remaining_retries, None)
            timeline.append(
                {
                    "type": "model_call",
                    "sequence": sequence,
                    "input_messages": list(conversation),
                    "output": content,
                    "token_usage": model_call.token_usage if model_call else {},
                }
            )
            for block in content:
                if "toolUse" in block:
                    timeline.append({"type": "tool_call", "content": block["toolUse"]})
            conversation.append(message)
            continue
        if message["role"] == "user":
            event_type = (
                "tool_result"
                if any("toolResult" in block for block in content)
                else "user_input"
            )
            timeline.append({"type": event_type, "content": content})
        else:
            timeline.append(
                {"type": "message", "role": message["role"], "content": content}
            )
        conversation.append(message)
    while next_retry:
        timeline.append({"type": "retry", **next_retry})
        next_retry = next(remaining_retries, None)
    return timeline


def format_observable_timeline(timeline: list[dict[str, Any]]) -> str:
    """Format the persisted observable timeline for notebook inspection.

    Args:
        timeline: Chronological observable run events.

    Returns:
        Sectioned JSON suitable for a notebook audit cell.
    """
    tool_calls = {
        str(event["content"].get("toolUseId")): event["content"]
        for event in timeline
        if event["type"] == "tool_call"
    }
    sections: list[str] = []
    for position, event in enumerate(timeline, start=1):
        event_type = str(event["type"]).replace("_", " ").upper()
        sequence = event.get("sequence")
        label = f"{position:02d} {event_type}"
        if sequence is not None:
            label += f" {sequence}"
        sections.extend(
            [f"===== {label} =====", _format_timeline_event(event, tool_calls)]
        )
    return "\n\n".join(sections)


def count_structured_output_repairs(timeline: list[dict[str, Any]]) -> int:
    """Count structured-output validation failures that caused another model call.

    Args:
        timeline: Persisted observable run events.

    Returns:
        Number of schema-repair cycles.
    """
    return sum(
        "Validation failed for MonolithicAnswer" in json.dumps(event)
        for event in timeline
        if event.get("type") == "tool_result"
    )


def _format_timeline_event(
    event: dict[str, Any], tool_calls: dict[str, dict[str, Any]]
) -> str:
    """Render one timeline event as a compact human-readable transcript."""
    event_type = event["type"]
    if event_type == "system_prompt":
        return str(event["content"])
    if event_type == "tool_definitions":
        return "\n".join(
            f"- {name}: {definition.get('description', '').splitlines()[0]}"
            for name, definition in event["content"].items()
        )
    if event_type == "user_input":
        return _format_message("USER", event["content"], tool_calls)
    if event_type == "model_call":
        input_lines = [
            _format_message(message["role"].upper(), message["content"], tool_calls)
            for message in event["input_messages"]
        ]
        output_lines = _format_message("ASSISTANT", event["output"], tool_calls)
        return (
            "MODEL INPUT:\n"
            + "\n".join(input_lines)
            + "\n\nMODEL OUTPUT:\n"
            + output_lines
            + "\n\nTOKEN USAGE: "
            + _format_token_usage(event["token_usage"])
        )
    if event_type == "tool_call":
        tool_call = event["content"]
        return _format_tool_call(tool_call)
    if event_type == "tool_result":
        return _format_message("TOOL RESULT", event["content"], tool_calls)
    if event_type == "retry":
        return (
            f"Retry attempt {event['attempt']}. "
            f"Reason: {event.get('exception') or event.get('stop_reason') or 'unknown'}"
        )
    return str({key: value for key, value in event.items() if key != "type"})


def _format_message(
    role: str,
    content: list[dict[str, Any]],
    tool_calls: dict[str, dict[str, Any]],
) -> str:
    """Render visible message blocks without printing raw tool JSON."""
    lines: list[str] = []
    for block in content:
        if "text" in block:
            lines.append(f"{role}: {block['text']}")
        elif "toolUse" in block:
            lines.append(f"{role}: {_format_tool_call(block['toolUse'])}")
        elif "toolResult" in block:
            lines.append(_format_tool_result(block["toolResult"], tool_calls))
    return "\n".join(lines) or f"{role}: No visible content"


def _format_tool_call(tool_call: dict[str, Any]) -> str:
    """Render a tool name and arguments as one readable line."""
    if tool_call.get("name") == "MonolithicAnswer":
        return _format_structured_answer(tool_call.get("input", {}))
    arguments = ", ".join(
        f"{name}={_format_value(value)}"
        for name, value in tool_call.get("input", {}).items()
    )
    return f"TOOL CALL: {tool_call.get('name')}({arguments})"


def _format_structured_answer(answer: dict[str, Any]) -> str:
    """Render the framework's structured-output tool call as an answer preview."""
    values = [
        "- "
        + " | ".join(
            str(value.get(field, "-")) for field in ("label", "value", "unit", "period")
        )
        + f" | sources: {', '.join(value.get('source_evidence_ids', [])) or '-'}"
        for value in answer.get("values", [])
    ]
    evidence_ids = ", ".join(str(value) for value in answer.get("evidence_ids", []))
    return "\n".join(
        [
            "STRUCTURED ANSWER:",
            str(answer.get("answer_text", "No answer text")),
            "Values:",
            *values,
            f"Evidence: {evidence_ids or '-'}",
            f"Conclusions: {', '.join(answer.get('conclusions', [])) or '-'}",
        ]
    )


def _format_value(value: Any) -> str:
    """Render nested tool arguments without JSON syntax."""
    if isinstance(value, list):
        return ", ".join(_format_value(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_format_value(item)}" for key, item in value.items())
    return repr(value)


def _format_token_usage(token_usage: dict[str, Any]) -> str:
    """Render provider token usage as labeled values rather than a dictionary."""
    return (
        ", ".join(f"{name}={value}" for name, value in token_usage.items())
        or "not reported"
    )


def _format_tool_result(
    tool_result: dict[str, Any], tool_calls: dict[str, dict[str, Any]]
) -> str:
    """Render meaningful tool output while retaining raw output only in JSONL."""
    tool_call = tool_calls.get(str(tool_result.get("toolUseId")), {})
    tool_name = str(tool_call.get("name", "unknown tool"))
    if tool_name == "search_filing":
        return "TOOL RESULT: search_filing completed; ranked records are saved locally."
    payload = _parse_tool_payload(tool_result)
    if tool_name == "lookup_fact" and isinstance(payload.get("facts"), list):
        facts = [
            f"- {fact.get('period')}: {fact.get('value')} {fact.get('unit')} "
            f"({fact.get('evidence_id')})"
            for fact in payload["facts"]
        ]
        return "TOOL RESULT: lookup_fact\n" + "\n".join(facts)
    if tool_name == "calculate" and "result" in payload:
        return f"TOOL RESULT: calculate = {payload['result']}"
    return f"TOOL RESULT: {tool_name} returned {tool_result.get('status', 'unknown')}"


def _parse_tool_payload(tool_result: dict[str, Any]) -> dict[str, Any]:
    """Read the first JSON tool-result text block when available."""
    for block in tool_result.get("content", []):
        text = block.get("text")
        if isinstance(text, str):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return {}


def format_run_summary(record: RunRecord, gold_answer: dict[str, Any]) -> str:
    """Format the compact comparison and runtime summary for one run.

    Args:
        record: Saved local result for one agent run.
        gold_answer: Development-only gold answer for the same question.

    Returns:
        Readable comparison, tool, and runtime summary.
    """
    gold_text = gold_answer.get(
        "value", "; ".join(gold_answer.get("required_claims", []))
    )
    gold_values = ", ".join(
        f"{value['label']}: {value['value']} {value['unit']}"
        for value in gold_answer.get("required_values", [])
    )
    tools = ", ".join(
        event.name + (" (ok)" if event.success else " (failed)")
        for event in record.tool_events
    )
    evidence_ids = record.answer.evidence_ids if record.answer else []
    answer_text = record.answer.answer_text if record.answer else "No answer"
    return (
        f"Question: {record.question.question}\n"
        f"Model answer: {answer_text}\n"
        f"Gold answer: {gold_text}\n"
        f"Gold values: {gold_values or '-'}\n"
        f"Tools: {tools or '-'}\n"
        f"Evidence: {', '.join(evidence_ids) or '-'}\n"
        f"Latency: {record.latency_ms} ms | Tokens: {record.token_usage} | "
        f"Retries: {record.retries}\n"
        f"Structured-output repairs: {record.structured_output_repairs}\n"
        f"Cost: {record.cost_usd or 'not configured'}\n"
        f"Trace: {record.langfuse_trace_url}"
    )


@lru_cache(maxsize=1)
def check_langfuse() -> None:
    """Verify that required Langfuse settings and authentication are available.

    Raises:
        RuntimeError: If settings are missing or authentication fails.
    """
    settings = {
        "LANGFUSE_PUBLIC_KEY": LANGFUSE_PUBLIC_KEY,
        "LANGFUSE_SECRET_KEY": LANGFUSE_SECRET_KEY,
        "LANGFUSE_BASE_URL": LANGFUSE_BASE_URL,
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required Langfuse settings: {', '.join(missing)}")
    if not get_client().auth_check():
        raise RuntimeError("Langfuse authentication failed")


def create_trace(run_id: str) -> tuple[str, str]:
    """Create a stable trace ID and its Langfuse URL.

    Args:
        run_id: Local run identifier.

    Returns:
        The trace ID and trace URL.
    """
    langfuse = get_client()
    trace_id = langfuse.create_trace_id(seed=run_id)
    return trace_id, langfuse.get_trace_url(trace_id=trace_id)


@contextmanager
def observe_monolithic_run(
    trace_id: str,
    question: QuestionInput,
    provider: str,
    model_id: str,
    reasoning_mode: str,
) -> Iterator[Any]:
    """Create the root Langfuse observation for one monolithic run."""
    with get_client().start_as_current_observation(
        trace_context={"trace_id": trace_id},
        name="monolithic-sec-research",
        as_type="agent",
        input={
            "question_id": question.question_id,
            "company": question.ticker,
            "question": question.question,
        },
        metadata={
            "architecture": "monolithic",
            "provider": provider,
            "model": model_id,
            "reasoning_mode": reasoning_mode,
        },
        model=model_id,
        model_parameters={
            "temperature": 0,
            "reasoning": reasoning_mode,
        },
    ) as observation:
        yield observation
    get_client().flush()


@contextmanager
def propagate_monolithic_attributes(question: QuestionInput) -> Iterator[None]:
    """Attach run metadata to child Langfuse observations."""
    with propagate_attributes(
        trace_name="monolithic-sec-research",
        tags=["phase-1", "monolithic", question.split, question.category],
        metadata={"question_id": question.question_id},
        environment=LANGFUSE_TRACING_ENVIRONMENT,
    ):
        yield

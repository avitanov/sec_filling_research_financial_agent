"""Run the selected architecture live and stream its observable progress."""

from __future__ import annotations

import queue
import threading
from typing import Any, Iterator

from agent.decomposed import DecomposedAgentHarness
from config import BEDROCK_SONNET_MODEL_ID
from demo import routing, sessions
from demo.presentation import (
    STAGE_AGENTS,
    STRUCTURED_OUTPUT_NAMES,
    answer_payload,
    describe_tool_payload,
    model_label,
    parse_tool_payload,
    pipeline_label,
    story,
)

_STREAM_END = object()
_run_lock = threading.Lock()
_harness: DecomposedAgentHarness | None = None
_health: dict[str, Any] | None = None


def _selected_harness() -> DecomposedAgentHarness:
    """Build the harness for the architecture the study selected, once."""
    global _harness
    if _harness is None:
        selected = story()["selected_architecture"]
        _harness = DecomposedAgentHarness(
            provider="bedrock",
            model_id=BEDROCK_SONNET_MODEL_ID,
            enable_verifier=selected["pipeline_variant"] != "no_verification",
            enable_math_repair=selected["pipeline_variant"]
            == "verification_with_repair",
        )
    return _harness


def _timeline_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one harness progress event into a browser timeline event."""
    kind = event["type"]
    if kind == "stage_started":
        return {
            "kind": "stage_started",
            "stage": event["stage"],
            "attempt": event["attempt"],
        }
    if kind == "stage_finished":
        return {
            "kind": "stage_finished",
            "stage": event["stage"],
            "attempt": event["attempt"],
            "duration_ms": event["duration_ms"],
            "summary": event["error"] or "",
        }
    if kind == "tool_call":
        # The framework submits a stage's structured output as a tool call.
        # Show it as what it is: the agent handing its result on.
        if event["tool"] in STRUCTURED_OUTPUT_NAMES:
            agent = STAGE_AGENTS.get(event["stage"], "Agent")
            return {
                "kind": "model_action",
                "stage": event["stage"],
                "title": f"{agent} {STRUCTURED_OUTPUT_NAMES[event['tool']]}",
            }
        return {
            "kind": "tool_call",
            "stage": event["stage"],
            "title": f"Calls {event['tool']}",
            "calls": [{"tool": event["tool"], "arguments": event["arguments"]}],
        }
    if kind == "tool_result":
        if event["tool"] in STRUCTURED_OUTPUT_NAMES:
            return None
        payload = parse_tool_payload({"content": event["content"]})
        return {
            "kind": "tool_result",
            "stage": event["stage"],
            "title": f"{event['tool']} returned {event['status']}",
            **describe_tool_payload(payload),
        }
    if kind == "verification":
        return {
            "kind": "verification",
            "stage": "verifier",
            "title": "Verifier decision",
            "approved": event["approved"],
            "summary": event["rejection_reason"] or "",
            "retry_stage": event["retry_stage"],
        }
    return None


def _readable_reason(error: Exception) -> str:
    """Explain a preflight failure without leaking a stack-trace style message."""
    message = str(error).strip() or type(error).__name__
    if "qdrant" in message.lower() or "collection" in message.lower():
        return f"The local retrieval index is not ready. {message}"
    if "langfuse" in message.lower():
        return f"Tracing is not configured. {message}"
    return message


def _record(session: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Keep one event in the saved conversation and pass it to the browser."""
    session["events"].append(event)
    return event


def _capture_completion(session: dict[str, Any], result: dict[str, Any]) -> None:
    """Log the final outcome when the client left before it was streamed.

    The worker keeps running after a disconnect, so its answer and cost would
    otherwise be lost from the saved session. This backfills them without
    re-sending anything to a browser that is already gone.
    """
    already_final = any(
        event["type"] in {"answer", "run_failed"} for event in session["events"]
    )
    if already_final:
        return
    if "error" in result:
        error = result["error"]
        session["events"].append(
            {
                "type": "run_failed",
                "failure_category": type(error).__name__,
                "failure_message": str(error),
            }
        )
        return
    record = result.get("record")
    if record is None:
        return
    if record.answer is not None:
        session["events"].append({"type": "answer", **answer_payload(record)})
    else:
        session["events"].append(
            {
                "type": "run_failed",
                "failure_category": record.failure_category,
                "failure_message": record.failure_message,
            }
        )
    session["events"].append(
        {
            "type": "run_cost",
            "latency_ms": record.latency_ms,
            "token_usage": record.token_usage,
            "cost_usd": record.cost_usd,
            "retries": record.retries,
            "structured_output_repairs": record.structured_output_repairs,
        }
    )


def health() -> dict[str, Any]:
    """Report whether a live run is possible in this environment.

    Returns:
        Availability, the failure reason when unavailable, and the model in use.
    """
    global _health
    if _health is not None:
        return _health
    selected = story()["selected_architecture"]
    labels = {
        "model_label": model_label(BEDROCK_SONNET_MODEL_ID),
        "pipeline_label": pipeline_label(selected["pipeline_variant"]),
    }
    try:
        _selected_harness().preflight()
    except Exception as error:
        _health = {
            "live_available": False,
            "reason": _readable_reason(error),
            **labels,
        }
        return _health
    _health = {"live_available": True, "reason": "", **labels}
    return _health


def reset_health() -> None:
    """Forget a cached preflight result so the next check runs again."""
    global _health
    _health = None


def run_stream(
    question: str,
    *,
    company: str | None = None,
    history: list[dict[str, str]] | None = None,
    conversation_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Answer one typed question and yield observable events as they happen.

    The Ask page is one generic chat, so every turn starts by routing: the model
    reads the question, calls ``list_available_data`` to see which frozen filings
    exist, and names the company to answer about. Routing runs on every turn, so a
    thread can re-scope mid-conversation - a message naming another company moves
    to it, one naming none stays where it is - and it resolves a company by brand
    or description, not only by name. When it resolves none, the same call carries
    a conversational reply and no filing pipeline runs.

    Args:
        question: Question text typed or picked on the Ask page.
        company: Ticker the conversation is currently about, given to the router
            as context it may override.
        history: Earlier answered turns in this conversation, oldest first, each
            a ``{"question", "answer"}`` pair, passed to the router and pipeline
            as context.

    Yields:
        Browser events, ending with an answer or failure and then done.
    """
    history = history or []
    # Routing is one short blocking call, so collect its progress and replay it
    # into the timeline once the company is known and a session exists to hold it.
    routing_events: list[dict[str, Any]] = []
    try:
        ticker, reply = routing.route(
            question,
            history=history,
            current_ticker=company,
            on_event=routing_events.append,
        )
    except routing.RouterUnavailable:
        # The router's own model call failed, so every stage after it would too:
        # say so plainly rather than the "which company?" reply, which would
        # misrepresent a connection problem as an ambiguous question.
        yield {"type": "connection_error", "message": "No AWS Bedrock connection."}
        yield {"type": "done"}
        return
    if ticker is None:
        # No company to scope a filing lookup: emit the reply the router already
        # produced. It never invents financial numbers and asks for a company when
        # one is needed.
        yield {"type": "reply", "text": reply}
        # Persist it like any answered turn so the chat survives a reload.
        sessions.record_reply(conversation_id, question, reply)
        yield {"type": "done"}
        return
    if not _run_lock.acquire(blocking=False):
        yield {
            "type": "busy",
            "message": "A question is already being answered. The pipeline "
            "answers one question at a time.",
        }
        yield {"type": "done"}
        return
    harness = _selected_harness()
    session = sessions.start(question, ticker, conversation_id)
    events: queue.Queue[Any] = queue.Queue()
    result: dict[str, Any] = {}

    def worker() -> None:
        try:
            # persist=False without exception: the demo is not a scored
            # experiment, so nothing it runs may reach data/runs/. The harness
            # compacts `history` itself, so a long chat cannot grow the prompt
            # without bound.
            result["record"] = harness.run_question(
                question,
                ticker=ticker,
                on_event=events.put,
                persist=False,
                history=history,
            )
        except Exception as error:
            result["error"] = error
        finally:
            events.put(_STREAM_END)

    thread = threading.Thread(target=worker, name="demo-live-run", daemon=True)
    try:
        thread.start()
        yield _record(
            session,
            {
                "type": "run_started",
                "mode": "live",
                "question": question,
                "ticker": ticker,
                "company": routing.short_names()[ticker],
                "category_label": "Custom question",
                "model_label": model_label(harness.model_id),
                "pipeline_label": pipeline_label(harness.pipeline_variant),
            },
        )
        step = 0
        # The routing decision is part of the audit trail: show how the company
        # was chosen before the stages that relied on it.
        for event in routing_events:
            timeline = _timeline_event(event)
            if timeline is None:
                continue
            step += 1
            yield _record(session, {"type": "timeline", "step": step, **timeline})
        while True:
            event = events.get()
            if event is _STREAM_END:
                break
            timeline = _timeline_event(event)
            if timeline is None:
                continue
            step += 1
            yield _record(session, {"type": "timeline", "step": step, **timeline})
        thread.join()
        if "error" in result:
            error = result["error"]
            yield _record(
                session,
                {
                    "type": "run_failed",
                    "failure_category": type(error).__name__,
                    "failure_message": str(error),
                },
            )
        else:
            record = result["record"]
            if record.answer is not None:
                yield _record(session, {"type": "answer", **answer_payload(record)})
            else:
                yield _record(
                    session,
                    {
                        "type": "run_failed",
                        "failure_category": record.failure_category,
                        "failure_message": record.failure_message,
                    },
                )
            yield _record(
                session,
                {
                    "type": "run_cost",
                    "latency_ms": record.latency_ms,
                    "token_usage": record.token_usage,
                    "cost_usd": record.cost_usd,
                    "retries": record.retries,
                    "structured_output_repairs": record.structured_output_repairs,
                },
            )
        yield {"type": "done"}
    finally:
        # The worker mutates the shared harness, so the lock must outlive it.
        # If the client disconnected mid-run, GeneratorExit lands here while the
        # Bedrock call is still in flight; wait for it to finish before freeing
        # the harness, otherwise the next request would clear its messages
        # underneath a running stage.
        thread.join()
        _capture_completion(session, result)
        sessions.finish(session)
        _run_lock.release()

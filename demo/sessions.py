"""Turn one Ask-page conversation turn into a stored AgentCore memory record."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from demo import agentcore_memory


def start(
    question: str, ticker: str | None, conversation_id: str | None = None
) -> dict[str, Any]:
    """Open one turn record.

    Args:
        question: Question typed or picked on the Ask page.
        ticker: Company the question was resolved to, when one was found.
        conversation_id: Conversation (session) this turn belongs to, used to save
            the chat so it can be reopened. Absent turns are not saved as a chat.

    Returns:
        A mutable record to pass to `finish` when the turn ends.
    """
    return {
        "conversation_id": conversation_id,
        "started_at": datetime.now(UTC).isoformat(),
        "question": question,
        "ticker": ticker,
        "status": "started",
        "events": [],
    }


def finish(record: dict[str, Any]) -> None:
    """Complete one conversation turn and persist it to AgentCore memory.

    Only the observable events already sent to the browser are used, so a saved
    turn never contains hidden model reasoning.

    Args:
        record: Record returned by `start`, with its events appended.
    """
    answer = next(
        (event for event in record["events"] if event["type"] == "answer"), None
    )
    failure = next(
        (event for event in record["events"] if event["type"] == "run_failed"), None
    )
    cost = next(
        (event for event in record["events"] if event["type"] == "run_cost"), {}
    )
    started = next(
        (event for event in record["events"] if event["type"] == "run_started"), {}
    )
    record["finished_at"] = datetime.now(UTC).isoformat()
    record["status"] = "answered" if answer else "failed" if failure else "incomplete"
    record["step_count"] = sum(
        1 for event in record["events"] if event["type"] == "timeline"
    )
    _save_turn(record, answer, failure, cost, started)


def _save_turn(
    record: dict[str, Any],
    answer: dict[str, Any] | None,
    failure: dict[str, Any] | None,
    cost: dict[str, Any],
    started: dict[str, Any],
) -> None:
    """Append this turn to its conversation so the chat can be reopened.

    Only turns that actually ran (answered or failed) are saved; a question that
    named no company never started a run.
    """
    conversation_id = record.get("conversation_id")
    if not conversation_id or record["status"] not in {"answered", "failed"}:
        return
    turn = {
        "kind": "answer",
        "question": record["question"],
        "answer": answer["text"] if answer else None,
        "status": record["status"],
        "ticker": record["ticker"],
        "company": started.get("company"),
        "answered_at": record["finished_at"],
        "answer_payload": (
            {
                "text": answer["text"],
                "values": answer.get("values", []),
                "evidence_ids": answer.get("evidence_ids", []),
            }
            if answer
            else None
        ),
        "failure": (
            {
                "failure_category": failure.get("failure_category"),
                "failure_message": failure.get("failure_message"),
            }
            if failure
            else None
        ),
        "cost": {
            "latency_ms": cost.get("latency_ms"),
            "cost_usd": cost.get("cost_usd"),
            "token_usage": cost.get("token_usage"),
        },
        "step_count": record["step_count"],
    }
    agentcore_memory.record_turn(conversation_id, turn)


def record_reply(conversation_id: str | None, question: str, answer: str) -> None:
    """Persist one conversational (non-filing) turn so it survives a reload.

    A reply has no company, evidence, or cost - it is a plain assistant message -
    so it is stored with ``kind='reply'`` and the page rebuilds it as such. Skipped
    when there is no conversation to attach it to. Best-effort, like every write.
    """
    if not conversation_id:
        return
    agentcore_memory.record_turn(
        conversation_id,
        {
            "kind": "reply",
            "question": question,
            "answer": answer,
            "status": "answered",
            "ticker": None,
            "company": None,
            "answered_at": datetime.now(UTC).isoformat(),
            "answer_payload": None,
            "failure": None,
            "cost": {},
            "step_count": 0,
        },
    )

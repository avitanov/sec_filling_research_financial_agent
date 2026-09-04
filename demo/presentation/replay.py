"""Assemble the complete replay event stream for one saved question."""

from __future__ import annotations

from typing import Any

from .evaluation import answer_payload, evaluation_panel
from .labels import category_label, model_label, pipeline_label
from .store import _curated_by_id, _saved_batch
from .timeline import _events_from_candidate, _events_from_record


def replay_stream(question_id: str) -> list[dict[str, Any]]:
    """Build the complete replay event stream for one saved question.

    Args:
        question_id: Benchmark question ID with a saved run.

    Returns:
        Ordered events, ending with the answer, evaluation, and done events.

    Raises:
        KeyError: If the question has no saved run in the demo batch.
    """
    entry = _saved_batch()[question_id]
    record = entry["record"]
    curated = _curated_by_id().get(question_id)
    events: list[dict[str, Any]] = [
        {
            "type": "run_started",
            "mode": "replay",
            "question_id": question_id,
            "question": record.question.question,
            "company": record.question.company,
            "category": record.question.category,
            "category_label": category_label(record.question.category),
            "model_label": model_label(record.model),
            "pipeline_label": pipeline_label(record.pipeline_variant),
            "title": (curated or {}).get("title"),
            "why_selected": (curated or {}).get("why_selected"),
        }
    ]
    timeline = (
        _events_from_candidate(curated)
        if curated is not None
        else _events_from_record(record)
    )
    for step, event in enumerate(timeline, start=1):
        events.append({"type": "timeline", "step": step, **event})
    if record.answer is not None:
        events.append({"type": "answer", **answer_payload(record)})
    else:
        events.append(
            {
                "type": "run_failed",
                "failure_category": record.failure_category,
                "failure_message": record.failure_message,
            }
        )
    events.append({"type": "evaluation", **evaluation_panel(question_id)})
    events.append({"type": "done"})
    return events

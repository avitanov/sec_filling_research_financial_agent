"""Serve the story and chat pages for the SEC filing research harness."""

from __future__ import annotations

import json
from pathlib import Path
from time import sleep
from typing import Any, Iterator

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from demo import agentcore_memory, live, presentation

STATIC_DIR = Path(__file__).resolve().parent / "static"
REPLAY_PAUSE_SECONDS = {
    "run_started": 0.4,
    "timeline": 0.85,
    "answer": 1.2,
    "run_failed": 1.0,
    "evaluation": 0.6,
    "done": 0.0,
}


def _sse(events: Iterator[dict[str, Any]]) -> Iterator[dict[str, str]]:
    """Wrap demo events as server-sent event payloads."""
    for event in events:
        yield {"event": event["type"], "data": json.dumps(event)}


def _paced(events: list[dict[str, Any]], pace: bool) -> Iterator[dict[str, Any]]:
    """Emit saved events with a small delay so a replay reads as live."""
    for event in events:
        yield event
        if pace:
            sleep(REPLAY_PAUSE_SECONDS.get(event["type"], 0.5))


async def story_page(request: Request) -> FileResponse:
    """Serve the chronological story page."""
    return FileResponse(STATIC_DIR / "index.html")


async def chat_page(request: Request) -> FileResponse:
    """Serve the streaming chat page."""
    return FileResponse(STATIC_DIR / "chat.html")


async def api_story(request: Request) -> JSONResponse:
    """Return the curated candidates with their replay events and metrics."""
    document = presentation.story()
    selected = document["selected_architecture"]
    return JSONResponse(
        {
            "model_label": presentation.model_label(selected["model"]),
            "pipeline_label": presentation.pipeline_label(selected["pipeline_variant"]),
            "selection_reason": selected["selection_reason"],
            "content_scope": document["content_scope"],
            "metrics_scope": document["metrics_scope"],
            "candidates": [
                {
                    "question_id": candidate["question_id"],
                    "category": candidate["category"],
                    "category_label": presentation.category_label(
                        candidate["category"]
                    ),
                    "title": candidate["title"],
                    "why_selected": candidate["why_selected"],
                    "question": candidate["question"],
                    "events": presentation.replay_stream(candidate["question_id"]),
                }
                for candidate in document["candidates"]
            ],
        }
    )


async def api_recommendations(request: Request) -> JSONResponse:
    """Return the Ask page's recommended questions and the companies covered."""
    return JSONResponse(
        {
            "companies": presentation.company_choices(),
            "recommendations": presentation.recommendations(),
        }
    )


async def api_health(request: Request) -> JSONResponse:
    """Report whether live runs are possible right now."""
    if request.query_params.get("refresh") == "1":
        live.reset_health()
    return JSONResponse(live.health())


async def api_replay(request: Request) -> EventSourceResponse | JSONResponse:
    """Stream one saved run as paced events."""
    question_id = request.path_params["question_id"]
    try:
        events = presentation.replay_stream(question_id)
    except KeyError:
        return JSONResponse(
            {"error": f"No saved run for question {question_id!r}"}, status_code=404
        )
    pace = request.query_params.get("pace", "1") != "0"
    return EventSourceResponse(_sse(_paced(events, pace)))


async def api_ask(request: Request) -> EventSourceResponse | JSONResponse:
    """Answer one typed question, streaming the pipeline's progress.

    A follow-up in the same conversation passes ``company`` (the ticker the
    conversation is scoped to) and ``history`` (earlier answered turns as a JSON
    array of ``{"question", "answer"}``) so it need not re-name the company and
    can resolve references back to the earlier answers.
    """
    question = (request.query_params.get("q") or "").strip()
    if not question:
        return JSONResponse({"error": "A question is required"}, status_code=400)
    company = (request.query_params.get("company") or "").strip() or None
    conversation_id = (
        request.query_params.get("conversation_id") or ""
    ).strip() or None
    try:
        turns = json.loads(request.query_params.get("history") or "[]")
    except json.JSONDecodeError:
        return JSONResponse({"error": "Malformed history"}, status_code=400)
    return EventSourceResponse(
        _sse(
            live.run_stream(
                question,
                company=company,
                history=turns,
                conversation_id=conversation_id,
            )
        )
    )


async def api_conversations(request: Request) -> JSONResponse:
    """List saved conversations, or delete them all on DELETE."""
    if request.method == "DELETE":
        return JSONResponse(
            {"deleted": agentcore_memory.delete_all_conversations()}
        )
    return JSONResponse({"conversations": agentcore_memory.list_conversations()})


async def api_conversation(request: Request) -> JSONResponse:
    """Return one saved conversation's turns, or delete it on DELETE."""
    conversation_id = request.path_params["conversation_id"]
    if request.method == "DELETE":
        return JSONResponse(
            {"deleted": agentcore_memory.delete_conversation(conversation_id)}
        )
    return JSONResponse({"turns": agentcore_memory.load_conversation(conversation_id)})


app = Starlette(
    routes=[
        Route("/", story_page),
        Route("/chat", chat_page),
        Route("/api/story", api_story),
        Route("/api/recommendations", api_recommendations),
        Route("/api/health", api_health),
        Route("/api/replay/{question_id}", api_replay),
        Route("/api/ask", api_ask),
        Route("/api/conversations", api_conversations, methods=["GET", "DELETE"]),
        Route(
            "/api/conversation/{conversation_id}",
            api_conversation,
            methods=["GET", "DELETE"],
        ),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
)

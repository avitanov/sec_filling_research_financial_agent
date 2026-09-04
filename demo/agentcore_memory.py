"""Store Ask-page conversations in Amazon Bedrock AgentCore Memory.

Persistence goes through the ``bedrock-agentcore`` SDK's ``MemoryClient`` rather
than hand-written boto3 calls. One AgentCore *session* holds one conversation;
one *event* (a ``blob`` document written with ``create_blob_event``) holds one
answered turn, oldest first. The memory resource is created on first use if it
does not already exist — creation is gated on a ``project`` tag, so the resource
is always created with ``project=<AGENTCORE_PROJECT_TAG>`` (any non-empty value
works). It is short-term only (no extraction strategies): the demo stores and
replays raw turns, nothing is summarised by a model.

Persistence is best-effort and never raises: if AgentCore is unreachable the Ask
page still answers, it just keeps history for the current session only. The
research harness and scored experiment never touch this store.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Any

from bedrock_agentcore.memory import MemoryClient

from config import AWS_REGION

# The tag the resource is created with. Creation is only permitted when a
# ``project`` tag is present, so any non-empty value satisfies the requirement.
PROJECT_TAG = os.getenv("AGENTCORE_PROJECT_TAG", "financial-agent-demo").strip()
# One shared memory resource, looked up by name (or by an explicit id). AgentCore
# derives the id as ``<name>-<suffix>``, so an existing one is matched by prefix.
MEMORY_NAME = os.getenv("AGENTCORE_MEMORY_NAME", "financial_agent_demo").strip()
MEMORY_ID = os.getenv("AGENTCORE_MEMORY_ID", "").strip()
# Raw turns expire after this many days; the demo needs only recent history.
EVENT_EXPIRY_DAYS = int(os.getenv("AGENTCORE_EVENT_EXPIRY_DAYS", "30"))
# All demo conversations belong to one actor; a conversation is one session.
ACTOR_ID = os.getenv("AGENTCORE_ACTOR_ID", "demo-user").strip() or "demo-user"

_ACTIVE_TIMEOUT_SECONDS = 90
_ACTIVE_POLL_SECONDS = 3
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")

_lock = threading.Lock()
_memory_client: MemoryClient | None = None
_memory_id: str | None = None


def _safe_session(conversation_id: str) -> str:
    """Return a session id AgentCore accepts, from a page conversation id."""
    cleaned = _SAFE_ID_RE.sub("-", (conversation_id or "").strip())
    return cleaned[:100] or "conversation"


def _client() -> MemoryClient:
    """Build the AgentCore memory SDK client once."""
    global _memory_client
    if _memory_client is None:
        _memory_client = MemoryClient(region_name=AWS_REGION)
    return _memory_client


def _wait_active(client: MemoryClient, memory_id: str) -> None:
    """Block until a freshly created memory resource is usable."""
    deadline = time.monotonic() + _ACTIVE_TIMEOUT_SECONDS
    while True:
        status = client.get_memory_status(memory_id)
        if status == "ACTIVE":
            return
        if status == "FAILED" or time.monotonic() > deadline:
            raise RuntimeError(f"Memory {memory_id} is {status}, not ACTIVE")
        time.sleep(_ACTIVE_POLL_SECONDS)


def _resolve_memory() -> str:
    """Get-or-create the shared memory resource, tagged with the project.

    ``MemoryClient.create_memory`` does not forward tags, so the tagged create
    goes through its control-plane client (``gmcp_client``) directly. Everything
    else uses the SDK.
    """
    global _memory_id
    if _memory_id is not None:
        return _memory_id
    with _lock:
        if _memory_id is not None:
            return _memory_id
        client = _client()
        memory_id = MEMORY_ID
        if not memory_id:
            existing = next(
                (
                    memory
                    for memory in client.list_memories()
                    if str(memory.get("id", "")).startswith(MEMORY_NAME)
                ),
                None,
            )
            if existing is not None:
                memory_id = existing["id"]
            else:
                created = client.gmcp_client.create_memory(
                    name=MEMORY_NAME,
                    description="Ask-page conversation history for the demo.",
                    eventExpiryDuration=EVENT_EXPIRY_DAYS,
                    tags={"project": PROJECT_TAG},
                    clientToken=str(uuid.uuid4()),
                )
                memory_id = created["memory"]["id"]
        _wait_active(client, memory_id)
        _memory_id = memory_id
        return _memory_id


def _turns_for_session(
    client: MemoryClient, memory_id: str, session_id: str
) -> list[dict[str, Any]]:
    """Read every turn stored for one conversation, oldest first."""
    events = client.list_events(
        memory_id, ACTOR_ID, session_id, include_payload=True
    )
    events = sorted(
        events, key=lambda event: (event.get("eventTimestamp"), event.get("eventId"))
    )
    turns: list[dict[str, Any]] = []
    for event in events:
        for item in event.get("payload", []):
            blob = item.get("blob")
            # AgentCore returns a blob as the JSON string it was stored as, not a
            # dict, so parse it back. A blob that is not JSON (e.g. an older,
            # differently-serialized turn) is skipped rather than crashing the list.
            if isinstance(blob, str):
                try:
                    blob = json.loads(blob)
                except (json.JSONDecodeError, ValueError):
                    continue
            if isinstance(blob, dict):
                turns.append(blob)
    return turns


def record_turn(conversation_id: str, turn: dict[str, Any]) -> None:
    """Append one answered turn to its conversation. Never raises."""
    try:
        # Store the turn as a JSON string: AgentCore serializes a dict blob into a
        # non-JSON form that cannot be read back, so serialize it ourselves and
        # parse it on read (see _turns_for_session).
        _client().create_blob_event(
            _resolve_memory(),
            ACTOR_ID,
            _safe_session(conversation_id),
            blob_data=json.dumps(turn),
        )
    except Exception:  # best-effort: a failed save never breaks the Ask page
        return


def load_conversation(conversation_id: str) -> list[dict[str, Any]]:
    """Return one conversation's turns, oldest first. Empty when unavailable."""
    try:
        client = _client()
        return _turns_for_session(
            client, _resolve_memory(), _safe_session(conversation_id)
        )
    except Exception:  # best-effort: history is optional
        return []


def list_conversations() -> list[dict[str, Any]]:
    """Summarise stored conversations, most recently updated first."""
    try:
        client = _client()
        memory_id = _resolve_memory()
        summaries: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            response = (
                client.gmdp_client.list_sessions(
                    memoryId=memory_id,
                    actorId=ACTOR_ID,
                    maxResults=100,
                    nextToken=token,
                )
                if token
                else client.gmdp_client.list_sessions(
                    memoryId=memory_id, actorId=ACTOR_ID, maxResults=100
                )
            )
            summaries.extend(response.get("sessionSummaries", []))
            token = response.get("nextToken")
            if not token:
                break
    except Exception:  # best-effort: history is optional
        return []

    conversations: list[dict[str, Any]] = []
    for summary in summaries:
        session_id = summary.get("sessionId")
        try:
            turns = _turns_for_session(client, memory_id, session_id)
        except Exception:
            continue
        if not turns:
            continue
        last = turns[-1]
        conversations.append(
            {
                "id": session_id,
                "title": turns[0].get("question") or "Conversation",
                "ticker": last.get("ticker"),
                "company": last.get("company"),
                "turn_count": len(turns),
                "updated_at": last.get("answered_at") or "",
            }
        )
    conversations.sort(key=lambda item: item["updated_at"], reverse=True)
    return conversations


def _delete_session_events(
    client: MemoryClient, memory_id: str, session_id: str
) -> None:
    """Delete every stored event of one session. A session has no separate
    delete, so its turns are removed one event at a time."""
    events = client.list_events(
        memory_id, ACTOR_ID, session_id, include_payload=False
    )
    for event in events:
        client.gmdp_client.delete_event(
            memoryId=memory_id,
            actorId=ACTOR_ID,
            sessionId=session_id,
            eventId=event["eventId"],
        )


def delete_conversation(conversation_id: str) -> bool:
    """Delete one conversation and all its turns. Best-effort; never raises."""
    try:
        _delete_session_events(
            _client(), _resolve_memory(), _safe_session(conversation_id)
        )
        return True
    except Exception:  # best-effort: a failed delete never breaks the page
        return False


def delete_all_conversations() -> int:
    """Delete every stored conversation. Returns how many were removed."""
    removed = 0
    try:
        client = _client()
        memory_id = _resolve_memory()
        token: str | None = None
        session_ids: list[str] = []
        while True:
            response = (
                client.gmdp_client.list_sessions(
                    memoryId=memory_id,
                    actorId=ACTOR_ID,
                    maxResults=100,
                    nextToken=token,
                )
                if token
                else client.gmdp_client.list_sessions(
                    memoryId=memory_id, actorId=ACTOR_ID, maxResults=100
                )
            )
            session_ids.extend(
                summary.get("sessionId")
                for summary in response.get("sessionSummaries", [])
            )
            token = response.get("nextToken")
            if not token:
                break
        for session_id in session_ids:
            try:
                _delete_session_events(client, memory_id, session_id)
                removed += 1
            except Exception:  # skip a session that will not delete, keep going
                continue
    except Exception:  # best-effort: return whatever was removed
        return removed
    return removed

"""Derive the browser's step-by-step timeline from a saved run or curated candidate."""

from __future__ import annotations

import json
from typing import Any

from models import RunRecord

from .labels import STAGE_AGENTS, STRUCTURED_OUTPUT_NAMES


def parse_tool_payload(block: dict[str, Any]) -> dict[str, Any]:
    """Read the first JSON object from a tool-result content block list."""
    for item in block.get("content", []):
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def describe_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Describe one tool payload with the fields the timeline cards render."""
    if "companies" in payload:
        companies = payload["companies"]
        return {
            "summary": f"{len(companies)} company filing(s) available.",
            "results": [
                {
                    "label": company.get("common_name"),
                    "value": company.get("ticker"),
                }
                for company in companies
            ],
        }
    if "facts" in payload:
        return {
            "summary": f"{len(payload['facts'])} normalized fact(s) returned.",
            "results": [
                {
                    "evidence_id": fact.get("evidence_id"),
                    "label": fact.get("concept"),
                    "value": fact.get("value"),
                    "unit": fact.get("unit"),
                    "period": fact.get("period"),
                }
                for fact in payload["facts"]
            ],
        }
    if "records" in payload:
        return {
            "summary": f"Top {len(payload['records'])} filing chunk(s) returned.",
            "selected_evidence": [
                item.get("evidence_id") for item in payload["records"]
            ],
        }
    if "result" in payload:
        return {
            "summary": "Calculator returned an exact value.",
            "result": payload["result"],
        }
    if payload.get("error"):
        return {"summary": f"Tool error: {payload['error']}"}
    return {"summary": "Tool returned no readable payload."}


def _model_action_title(stage: str, output: list[dict[str, Any]]) -> str:
    """Name what one model response did, using observable content only."""
    agent = STAGE_AGENTS.get(stage, "Agent")
    tool_names = [
        block["toolUse"].get("name") for block in output if "toolUse" in block
    ]
    for name in tool_names:
        if name in STRUCTURED_OUTPUT_NAMES:
            return f"{agent} {STRUCTURED_OUTPUT_NAMES[name]}"
    if tool_names:
        unique = sorted(set(name for name in tool_names if name))
        return f"{agent} requests {len(tool_names)} call(s) to {', '.join(unique)}"
    return f"{agent} responds without calling a tool"


def _verifier_decision(output: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Read the verifier verdict from a structured-output tool call."""
    for block in output:
        tool_use = block.get("toolUse")
        if tool_use is None or tool_use.get("name") != "VerifierSubmission":
            continue
        submission = tool_use.get("input", {})
        return {
            "approved": bool(submission.get("approved")),
            "retry_stage": submission.get("retry_stage"),
            "summary": submission.get("rejection_reason") or "",
        }
    return None


def _events_from_record(record: RunRecord) -> list[dict[str, Any]]:
    """Derive timeline events from one saved run record.

    Args:
        record: Saved run whose observable timeline is replayed.

    Returns:
        Timeline events in the shape the browser renders.
    """
    events: list[dict[str, Any]] = []
    stage_key: tuple[str | None, int | None] | None = None
    for entry in record.observable_timeline:
        stage = entry.get("agent_stage")
        attempt = entry.get("agent_attempt")
        if entry["type"] in {"system_prompt", "tool_definitions", "user_input"}:
            continue
        if (stage, attempt) != stage_key:
            stage_key = (stage, attempt)
            events.append({"kind": "stage_started", "stage": stage, "attempt": attempt})
        if entry["type"] == "model_call":
            event = {
                "kind": "model_action",
                "stage": stage,
                "model_call": entry.get("sequence"),
                "title": _model_action_title(stage or "", entry["output"]),
                "summary": " ".join(
                    block["text"] for block in entry["output"] if "text" in block
                ).strip(),
            }
            decision = _verifier_decision(entry["output"])
            if decision is not None:
                event.update(kind="verification", **decision)
            events.append(event)
            continue
        if entry["type"] == "tool_call":
            call = entry["content"]
            name = call.get("name")
            if name in STRUCTURED_OUTPUT_NAMES:
                continue
            if events and events[-1]["kind"] == "tool_call":
                grouped = events[-1]["calls"]
                grouped.append({"tool": name, "arguments": call.get("input", {})})
                tools = sorted({str(item["tool"]) for item in grouped})
                events[-1]["title"] = (
                    f"Calls {', '.join(tools)} {len(grouped)} times in one turn"
                )
                continue
            events.append(
                {
                    "kind": "tool_call",
                    "stage": stage,
                    "title": f"Calls {name}",
                    "calls": [{"tool": name, "arguments": call.get("input", {})}],
                }
            )
            continue
        if entry["type"] == "tool_result":
            for block in entry["content"]:
                result = block.get("toolResult")
                if result is None:
                    continue
                payload = parse_tool_payload(result)
                if not payload:
                    continue
                events.append(
                    {
                        "kind": "tool_result",
                        "stage": stage,
                        "title": "Tool result returned",
                        **describe_tool_payload(payload),
                    }
                )
            continue
        if entry["type"] == "retry":
            events.append(
                {
                    "kind": "retry",
                    "stage": stage,
                    "title": f"Provider retry {entry.get('attempt')}",
                    "summary": str(
                        entry.get("exception") or entry.get("stop_reason") or "unknown"
                    ),
                }
            )
    return events


def _events_from_candidate(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one curated candidate timeline into browser events.

    Args:
        candidate: Curated entry from `candidates.json`.

    Returns:
        Timeline events in the shape the browser renders.
    """
    events: list[dict[str, Any]] = []
    for step in candidate["timeline"]:
        if step["kind"] == "assistant_answer":
            continue
        event = {key: value for key, value in step.items() if key != "kind"}
        event["kind"] = step["kind"]
        if step["kind"] == "tool_call" and "calls" not in step:
            event["calls"] = [
                {"tool": step["tool"], "arguments": step.get("arguments", {})}
            ]
        events.append(event)
    return events

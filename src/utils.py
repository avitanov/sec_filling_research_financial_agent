"""Small reusable utilities."""

import json
import re
from pathlib import Path
from typing import Any

WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Collapse whitespace and strip surrounding space."""
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def unique_strings(values: list[str | None]) -> list[str]:
    """Return non-empty strings once, preserving their first-seen order."""
    return list(dict.fromkeys(value for value in values if value))


def is_empty_value(value: Any) -> bool:
    """Return whether a value should be omitted from stored metadata."""
    return value is None or value == "" or value == [] or value == {}


def compact_metadata(values: dict[str, Any]) -> dict[str, Any]:
    """Remove empty optional values from a metadata mapping."""
    return {key: value for key, value in values.items() if not is_empty_value(value)}


def write_json(data: dict[str, Any], destination: Path) -> None:
    """Write JSON data in an indented, UTF-8 format.

    Args:
        data: JSON-compatible mapping to write.
        destination: Local file path where the JSON should be stored.
    """
    destination.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

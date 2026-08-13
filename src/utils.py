"""Small reusable utilities."""

import json
from pathlib import Path
from typing import Any


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

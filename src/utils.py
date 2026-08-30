"""Small reusable utilities."""

import csv
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

WHITESPACE_PATTERN = re.compile(r"\s+")
LABEL_NOISE_PATTERN = re.compile(r"[$%()\d,._—–-]+")


def normalize_text(text: str) -> str:
    """Collapse whitespace and strip surrounding space."""
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def normalize_label(label: str) -> str:
    """Normalize a table label for text comparison."""
    return normalize_text(LABEL_NOISE_PATTERN.sub(" ", label).casefold())


def unique_strings(values: list[str | None]) -> list[str]:
    """Return non-empty strings once, preserving their first-seen order."""
    return list(dict.fromkeys(value for value in values if value))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read non-empty JSON Lines records from one local file."""
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write mappings to a UTF-8 CSV file, serializing nested values as JSON."""
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(
            {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict))
                else value
                for key, value in row.items()
            }
            for row in rows
        )


def format_decimal_result_to_string(value: Decimal) -> str:
    """Format a Decimal result without unnecessary trailing zeroes."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def normalize_ticker(ticker: str) -> str:
    """Return a ticker in its standard uppercase form."""
    return ticker.upper().strip()


def ticker_data_directory(data_directory: Path, ticker: str) -> tuple[str, Path]:
    """Return the existing frozen-data directory for a company ticker.

    Args:
        data_directory: Directory containing one subdirectory per ticker.
        ticker: Company ticker to normalize and validate.

    Returns:
        Normalized ticker and its frozen-data directory.

    Raises:
        ValueError: If the ticker has no frozen-data directory.
    """
    normalized_ticker = normalize_ticker(ticker)
    ticker_directory = data_directory / normalized_ticker
    if not ticker_directory.is_dir():
        raise ValueError(f"Unknown frozen company: {ticker}")
    return normalized_ticker, ticker_directory


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

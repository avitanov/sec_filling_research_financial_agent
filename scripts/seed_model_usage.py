"""Initialize centralized model-usage totals from saved local run records."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config import MODEL_USAGE_DIR, RUNS_DIR  # noqa: E402
from model_usage import handle_model_invocation  # noqa: E402
from utils import read_jsonl  # noqa: E402


def _run_paths() -> list[Path]:
    return [
        *sorted(RUNS_DIR.glob("*.jsonl")),
        *sorted((PROJECT_ROOT / "results").glob("*/runs.jsonl")),
    ]


def _recorded_run_usages() -> dict[str, list[dict[str, Any]]]:
    usages: dict[str, list[dict[str, Any]]] = {}
    seen_run_ids: set[str] = set()
    for path in _run_paths():
        for record in read_jsonl(path):
            run_id = str(record.get("run_id", ""))
            if not run_id or run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)
            model_id = str(record.get("model", ""))
            calls = record.get("model_calls", [])
            if isinstance(calls, list) and calls:
                usages.setdefault(model_id, []).extend(
                    call.get("token_usage", {})
                    for call in calls
                    if isinstance(call, dict)
                )
            else:
                usages.setdefault(model_id, []).append(record.get("token_usage", {}))
    return usages


def _recorded_judge_usages() -> dict[str, list[dict[str, Any]]]:
    usages: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((PROJECT_ROOT / "results").glob("*/judge_records.jsonl")):
        for record in read_jsonl(path):
            model_id = record.get("judge_model")
            token_usage = record.get("token_usage")
            if isinstance(model_id, str) and isinstance(token_usage, dict):
                usages.setdefault(model_id, []).append(token_usage)
    return usages


def main() -> None:
    """Write initial usage totals from the saved monolithic and judge runs."""
    if MODEL_USAGE_DIR.exists():
        for path in MODEL_USAGE_DIR.glob("*/usage.json"):
            path.unlink()
    usages = _recorded_run_usages()
    for model_id, token_usages in _recorded_judge_usages().items():
        usages.setdefault(model_id, []).extend(token_usages)
    for model_id, token_usages in sorted(usages.items()):
        for token_usage in token_usages:
            handle_model_invocation(model_id, token_usage)


if __name__ == "__main__":
    main()

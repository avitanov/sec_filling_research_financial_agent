"""Load the curated candidates and the saved batch behind them, once each."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from agent.data import load_gold_questions
from config import PROJECT_ROOT
from evaluation.metrics import evaluate_saved_results, load_run

CANDIDATES_PATH = PROJECT_ROOT / "data" / "presentation-candidates" / "candidates.json"


@lru_cache(maxsize=1)
def story() -> dict[str, Any]:
    """Load the curated presentation candidates.

    Returns:
        The parsed `candidates.json` document.
    """
    return json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))


def _curated_by_id() -> dict[str, dict[str, Any]]:
    return {candidate["question_id"]: candidate for candidate in story()["candidates"]}


@lru_cache(maxsize=1)
def _saved_batch() -> dict[str, dict[str, Any]]:
    """Evaluate the saved batch behind the curated candidates, once.

    Returns:
        One entry per question ID with its run record, metrics, and gold answer.
    """
    document = story()
    results_directory = PROJECT_ROOT / document["source_batch"]
    evaluation = evaluate_saved_results(
        results_directory=results_directory, rerun_judge=False
    )
    # The demo never calls the judge model; it shows the scores saved with the batch.
    saved_judge_rows = load_run(results_directory=results_directory)["judge_rows"]
    ticker = evaluation["records"][0].question.ticker
    gold_by_id = {
        record["question_id"]: record for record in load_gold_questions(ticker)
    }
    rows_by_id = {row["question_id"]: row for row in evaluation["question_rows"]}
    judge_by_id = {row["question_id"]: row for row in saved_judge_rows}
    retrieval_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in evaluation["retrieval_rows"]:
        retrieval_by_id.setdefault(row["question_id"], []).append(row)
    batch: dict[str, dict[str, Any]] = {}
    for record in evaluation["records"]:
        question_id = record.question.question_id
        batch[question_id] = {
            "record": record,
            "question_row": rows_by_id.get(question_id, {}),
            "judge_row": judge_by_id.get(question_id),
            "retrieval_rows": retrieval_by_id.get(question_id, []),
            "gold": gold_by_id.get(question_id, {}),
        }
    return batch

"""Load frozen benchmark questions and company facts."""

from __future__ import annotations

from typing import Any

from config import EVALUATION_DATA_DIR
from models import QuestionInput
from utils import normalize_ticker, read_jsonl, ticker_data_directory

QUESTION_CACHE: dict[str, tuple[QuestionInput, ...]] = {}
COMPANY_FACTS_CACHE: dict[str, list[dict[str, Any]]] = {}


def load_frozen_records(ticker: str, filename: str) -> list[dict[str, Any]]:
    """Load records from one frozen JSONL file for a benchmark company.

    Args:
        ticker: Frozen company ticker.
        filename: JSONL filename inside the company's data directory.

    Returns:
        Records in their frozen file order.
    """
    _, ticker_directory = ticker_data_directory(EVALUATION_DATA_DIR, ticker)
    return read_jsonl(ticker_directory / filename)


def load_gold_questions(ticker: str) -> list[dict[str, Any]]:
    """Load complete evaluation records, including gold answers.

    Args:
        ticker: Frozen company ticker.

    Returns:
        Question records in question-ID order.
    """
    return sorted(
        load_frozen_records(ticker, "questions.jsonl"),
        key=lambda record: record["question_id"],
    )


def load_questions(ticker: str | None = None) -> list[QuestionInput]:
    """Load questions without gold answers or scoring metadata.

    Args:
        ticker: Optional company ticker used to limit the result.

    Returns:
        Runtime-safe questions in ticker and question-ID order.
    """
    normalized_ticker = normalize_ticker(ticker) if ticker is not None else "all"
    cached_questions = QUESTION_CACHE.get(normalized_ticker)
    if cached_questions is not None:
        return list(cached_questions)
    if ticker is not None:
        normalized_ticker, ticker_directory = ticker_data_directory(
            EVALUATION_DATA_DIR, ticker
        )
        paths = [ticker_directory / "questions.jsonl"]
    else:
        paths = sorted(EVALUATION_DATA_DIR.glob("*/questions.jsonl"))
    records = [record for path in paths if path.exists() for record in read_jsonl(path)]
    questions = tuple(
        sorted(
            (QuestionInput.model_validate(record) for record in records),
            key=lambda question: (question.ticker, question.question_id),
        )
    )
    QUESTION_CACHE[normalized_ticker] = questions
    return list(questions)


def load_question(question_id: str) -> QuestionInput:
    """Load one runtime-safe benchmark question.

    Args:
        question_id: Stable benchmark question ID.

    Returns:
        Matching question.

    Raises:
        ValueError: If the question ID is unknown or duplicated.
    """
    normalized_question_id = question_id.strip().lower()
    ticker = normalized_question_id.partition("-")[0]
    matches = [
        question
        for question in load_questions(ticker)
        if question.question_id == normalized_question_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one question for ID {question_id!r}; found {len(matches)}"
        )
    return matches[0]


def load_company_facts(ticker: str) -> list[dict[str, Any]]:
    """Load the frozen normalized facts for one company.

    Args:
        ticker: Frozen company ticker.

    Returns:
        Fact records in their frozen file order.

    Raises:
        ValueError: If the company is not part of the benchmark.
    """
    normalized_ticker = normalize_ticker(ticker)
    cached_facts = COMPANY_FACTS_CACHE.get(normalized_ticker)
    if cached_facts is not None:
        return [dict(fact) for fact in cached_facts]

    facts = load_frozen_records(ticker, "company_facts.jsonl")
    COMPANY_FACTS_CACHE[normalized_ticker] = facts
    return [dict(fact) for fact in facts]

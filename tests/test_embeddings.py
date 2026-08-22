"""Check exact token accounting and long-context embedding configuration."""

from pytest import MonkeyPatch

import embeddings
from config import QDRANT_DENSE_MAX_TOKENS
from embeddings import build_chunk_token_report


def test_all_filing_chunks_fit_without_truncation() -> None:
    records, summaries = build_chunk_token_report()

    assert len(records) == 569
    assert max(record["token_count"] for record in records) > 512
    assert max(record["token_count"] for record in records) <= QDRANT_DENSE_MAX_TOKENS
    assert all(summary["within_model_limit"] for summary in summaries)
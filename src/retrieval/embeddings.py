"""Generate and audit long-context dense embeddings for filing chunks."""

from functools import lru_cache
from statistics import mean
from typing import Any

from fastembed import TextEmbedding
from tokenizers import Tokenizer

from config import (
    PROJECT_ROOT,
    QDRANT_DENSE_MAX_TOKENS,
    QDRANT_DENSE_MODEL,
)
from exceptions import ChunkValidationError
from models import FilingChunk
from retrieval.chunking import build_filing_chunks, load_filing_sources

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MODEL_CACHE_DIR = PROJECT_ROOT / ".cache" / "fastembed"


@lru_cache(maxsize=1)
def load_embedding_model() -> TextEmbedding:
    """Load the fixed 2,048-token embedding model from the local cache."""
    model = TextEmbedding(
        model_name=QDRANT_DENSE_MODEL,
        cache_dir=str(MODEL_CACHE_DIR),
        lazy_load=False,
    )
    tokenizer = model.model.tokenizer
    if tokenizer is None:
        raise ChunkValidationError("Embedding tokenizer did not load")
    # FastEmbed's artifact defaults to 512 despite this model's trained 2,048
    # positions. Set the measured and validated runtime limit explicitly.
    tokenizer.enable_truncation(max_length=QDRANT_DENSE_MAX_TOKENS)
    return model


def count_embedding_tokens(texts: list[str], prefix: str = "") -> list[int]:
    """Count exact model tokens before the embedding model can truncate them.

    Args:
        texts: Texts to tokenize.
        prefix: retrieval task prefix included in the count.

    Returns:
        One untruncated token count per input text.
    """
    model_tokenizer = load_embedding_model().model.tokenizer
    if model_tokenizer is None:
        raise ChunkValidationError("Embedding tokenizer did not load")

    tokenizer = Tokenizer.from_str(model_tokenizer.to_str())
    tokenizer.no_truncation()
    tokenizer.no_padding()
    encodings = tokenizer.encode_batch([prefix + text for text in texts])
    return [len(encoding.ids) for encoding in encodings]


def validate_embedding_lengths(chunks: list[FilingChunk]) -> list[int]:
    """Reject chunks that would be truncated by the dense embedding model.

    Args:
        chunks: Filing chunks prepared for indexing.

    Returns:
        Exact token counts aligned with the input chunks.

    Raises:
        ChunkValidationError: If any chunk exceeds the fixed model context.
    """
    counts = count_embedding_tokens([chunk.indexed_text for chunk in chunks])
    too_long = [
        (chunk.evidence_id, count)
        for chunk, count in zip(chunks, counts, strict=True)
        if count > QDRANT_DENSE_MAX_TOKENS
    ]
    if too_long:
        evidence_id, count = max(too_long, key=lambda item: item[1])
        raise ChunkValidationError(
            f"{evidence_id} has {count} tokens; model limit is "
            f"{QDRANT_DENSE_MAX_TOKENS}"
        )
    return counts


def embed_chunks(chunks: list[FilingChunk]) -> tuple[list[list[float]], list[int]]:
    """Validate and embed filing chunks without silent truncation.

    Args:
        chunks: Filing chunks to embed.

    Returns:
        Dense vectors and token counts aligned with the input chunks.
    """
    token_counts = validate_embedding_lengths(chunks)
    texts = [chunk.indexed_text for chunk in chunks]
    vectors = load_embedding_model().embed(texts, batch_size=8)
    return [vector.tolist() for vector in vectors], token_counts


def embed_query(query: str) -> tuple[list[float], str | None]:
    """Embed a search query and report whether the model truncated it.

    Args:
        query: Natural-language filing query.

    Returns:
        One query vector and a truncation message, when applicable.
    """
    max_tokens = QDRANT_DENSE_MAX_TOKENS
    token_count = count_embedding_tokens([query], prefix=QUERY_PREFIX)[0]
    truncation_message = None
    if token_count > max_tokens:
        truncation_message = f"Query was truncated to {max_tokens} tokens."
    vector = next(load_embedding_model().embed(f"{QUERY_PREFIX}{query}"))
    return vector.tolist(), truncation_message


def build_chunk_token_report() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Chunk all filings and summarize exact token counts by company.

    Returns:
        Per-chunk records and per-company plus overall summaries.
    """
    records: list[dict[str, Any]] = []
    for source in load_filing_sources():
        chunks = build_filing_chunks(source)
        counts = validate_embedding_lengths(chunks)
        for chunk, token_count in zip(chunks, counts, strict=True):
            records.append(
                {
                    "ticker": chunk.ticker,
                    "evidence_id": chunk.evidence_id,
                    "evidence_type": chunk.evidence_type,
                    "supporting_page_sequences": chunk.supporting_page_sequences,
                    "word_count": (
                        chunk.core_word_count
                        + chunk.overlap_before_word_count
                        + chunk.overlap_after_word_count
                    ),
                    "token_count": token_count,
                }
            )

    summaries: list[dict[str, Any]] = []
    for ticker in sorted({record["ticker"] for record in records}) + ["ALL"]:
        selected = (
            records
            if ticker == "ALL"
            else [record for record in records if record["ticker"] == ticker]
        )
        largest = max(selected, key=lambda record: record["token_count"])
        summaries.append(
            {
                "ticker": ticker,
                "chunk_count": len(selected),
                "average_tokens": round(
                    mean(record["token_count"] for record in selected), 1
                ),
                "max_tokens": largest["token_count"],
                "largest_chunk": largest["evidence_id"],
                "model_limit": QDRANT_DENSE_MAX_TOKENS,
                "within_model_limit": largest["token_count"] <= QDRANT_DENSE_MAX_TOKENS,
            }
        )
    return records, summaries

"""Persist and search filing chunks with local Qdrant hybrid retrieval."""

from __future__ import annotations

import uuid
from typing import Any

import requests
from qdrant_client import QdrantClient, models

from chunking import build_filing_chunks, load_filing_sources
from config import (
    FILING_CHUNK_PARSER_VERSION,
    QDRANT_COLLECTION,
    QDRANT_DENSE_MAX_TOKENS,
    QDRANT_DENSE_MODEL,
    QDRANT_DENSE_VECTOR_SIZE,
    QDRANT_TIMEOUT_SECONDS,
    QDRANT_URL,
    RETRIEVAL_CANDIDATE_LIMIT,
)
from embeddings import embed_chunks, embed_query, validate_embedding_lengths
from exceptions import FilingIndexError
from models import FilingChunk, FilingSearchResult

DENSE_VECTOR_NAME = "dense"
BM25_VECTOR_NAME = "bm25"
BM25_MODEL = "qdrant/bm25"
POINT_NAMESPACE = uuid.UUID("da771b9a-6215-44b3-bc4b-38c92ed6af8a")


def _client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT_SECONDS)


def _point_id(evidence_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, evidence_id))


def build_all_chunks() -> list[FilingChunk]:
    """Build chunks from all frozen filing sources.

    Returns:
        Complete chunks in ticker and source order.
    """
    chunks: list[FilingChunk] = []
    for source in load_filing_sources():
        chunks.extend(build_filing_chunks(source))
    return chunks


def _index_payload(chunk: FilingChunk, token_count: int) -> dict[str, Any]:
    payload = chunk.payload()
    payload.update(
        {
            "point_id": _point_id(chunk.evidence_id),
            "dense_model": QDRANT_DENSE_MODEL,
            "dense_vector_size": QDRANT_DENSE_VECTOR_SIZE,
            "embedding_token_count": token_count,
            "embedding_max_tokens": QDRANT_DENSE_MAX_TOKENS,
            "embedding_was_truncated": False,
            "sparse_model": BM25_MODEL,
        }
    )
    return payload


def _server_version() -> str:
    try:
        response = requests.get(QDRANT_URL, timeout=QDRANT_TIMEOUT_SECONDS)
        response.raise_for_status()
        return str(response.json().get("version", "unknown"))
    except requests.RequestException as error:
        raise FilingIndexError(f"Qdrant is unavailable at {QDRANT_URL}") from error


def rebuild_filing_index() -> dict[str, Any]:
    """Replace the scoped filing collection with reproducible hybrid vectors.

    Returns:
        Small build summary with collection and chunk counts.
    """
    server_version = _server_version()
    chunks = build_all_chunks()
    dense_vectors, token_counts = embed_chunks(chunks)
    client = _client()
    if client.collection_exists(QDRANT_COLLECTION):
        client.delete_collection(QDRANT_COLLECTION)
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config={
            DENSE_VECTOR_NAME: models.VectorParams(
                size=QDRANT_DENSE_VECTOR_SIZE,
                distance=models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            BM25_VECTOR_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="ticker",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="evidence_type",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )

    points: list[models.PointStruct] = []
    for chunk, token_count, dense_vector in zip(
        chunks, token_counts, dense_vectors, strict=True
    ):
        payload = _index_payload(chunk, token_count)
        points.append(
            models.PointStruct(
                id=payload["point_id"],
                vector={
                    DENSE_VECTOR_NAME: dense_vector,
                    BM25_VECTOR_NAME: models.Document(
                        text=chunk.indexed_text, model=BM25_MODEL
                    ),
                },
                payload=payload,
            )
        )
    for start in range(0, len(points), 16):
        client.upsert(
            collection_name=QDRANT_COLLECTION,
            points=points[start : start + 16],
            wait=True,
        )

    result: dict[str, Any] = {
        "collection": QDRANT_COLLECTION,
        "qdrant_server_version": server_version,
        "parser_version": FILING_CHUNK_PARSER_VERSION,
        "chunk_count": len(chunks),
        "page_chunk_count": sum(
            chunk.evidence_type == "filing_page" for chunk in chunks
        ),
        "filing_table_chunk_count": sum(
            chunk.evidence_type == "filing_table" for chunk in chunks
        ),
        "cross_page_table_chunk_count": sum(
            chunk.evidence_type == "cross_page_table" for chunk in chunks
        ),
        "max_embedding_tokens": max(token_counts),
    }
    validate_filing_index()
    return result


def _scroll_payloads(client: QdrantClient) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    offset: str | int | None = None
    while True:
        points, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        payloads.extend(dict(point.payload or {}) for point in points)
        if next_offset is None:
            return payloads
        offset = next_offset


def validate_filing_index() -> dict[str, Any]:
    """Validate Qdrant content against a fresh deterministic rebuild in memory.

    Returns:
        Validation status and point count.

    Raises:
        FilingIndexError: If persisted points do not match a fresh chunk build.
    """
    chunks = build_all_chunks()
    token_counts = validate_embedding_lengths(chunks)
    expected_payloads = {
        chunk.evidence_id: _index_payload(chunk, token_count)
        for chunk, token_count in zip(chunks, token_counts, strict=True)
    }
    client = _client()
    if not client.collection_exists(QDRANT_COLLECTION):
        raise FilingIndexError(f"Qdrant collection {QDRANT_COLLECTION} is missing")
    payloads = _scroll_payloads(client)
    actual_payloads = {str(payload.get("evidence_id")): payload for payload in payloads}
    if expected_payloads.keys() != actual_payloads.keys():
        raise FilingIndexError(
            "Persisted Qdrant evidence IDs do not match rebuilt chunks"
        )
    for evidence_id, expected in expected_payloads.items():
        if actual_payloads[evidence_id] != expected:
            raise FilingIndexError(
                f"Persisted Qdrant payload does not match rebuilt chunk: {evidence_id}"
            )
    return {
        "valid": True,
        "collection": QDRANT_COLLECTION,
        "point_count": len(payloads),
        "max_embedding_tokens": max(token_counts),
    }


def search_filing(query: str, company: str, limit: int = 5) -> list[FilingSearchResult]:
    """Search one company's filing with fused dense and BM25 rankings.

    Args:
        query: Natural-language or keyword query.
        company: Company ticker from the frozen dataset.
        limit: Maximum evidence records to return.

    Returns:
        Ranked filing evidence with full audit metadata.
    """
    ticker = company.upper().strip()
    if ticker not in {source.ticker for source in load_filing_sources()}:
        raise FilingIndexError(f"Unknown frozen company: {company}")
    company_filter = models.Filter(
        must=[
            models.FieldCondition(key="ticker", match=models.MatchValue(value=ticker))
        ]
    )
    candidate_limit = max(limit * 2, RETRIEVAL_CANDIDATE_LIMIT)
    dense_query, query_truncation_message = embed_query(query)
    response = _client().query_points(
        collection_name=QDRANT_COLLECTION,
        prefetch=[
            models.Prefetch(
                query=dense_query,
                using=DENSE_VECTOR_NAME,
                filter=company_filter,
                limit=candidate_limit,
            ),
            models.Prefetch(
                query=models.Document(text=query, model=BM25_MODEL),
                using=BM25_VECTOR_NAME,
                filter=company_filter,
                limit=candidate_limit,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=candidate_limit,
        with_payload=True,
        with_vectors=False,
    )
    points = list(response.points)
    page_scores = {
        str(point.payload["evidence_id"]): float(point.score)
        for point in points
        if point.payload and point.payload.get("evidence_type") == "filing_page"
    }
    ranked_points = []
    for point in points:
        payload = dict(point.payload or {})
        linked_page_score = 0.0
        if payload.get("evidence_type") != "filing_page":
            linked_page_score = max(
                (
                    page_scores.get(page_id, 0.0)
                    for page_id in payload.get("supporting_page_ids", [])
                ),
                default=0.0,
            )
        ranked_points.append(
            (point, float(point.score) + linked_page_score, linked_page_score)
        )
    ranked_points.sort(key=lambda row: row[1], reverse=True)

    results: list[FilingSearchResult] = []
    for point, score, linked_page_score in ranked_points[:limit]:
        payload = dict(point.payload or {})
        payload["fusion_score"] = float(point.score)
        payload["linked_page_score"] = linked_page_score
        if query_truncation_message is not None:
            payload["query_truncation_message"] = query_truncation_message
        results.append(
            FilingSearchResult(
                evidence_id=str(payload["evidence_id"]),
                score=score,
                text=str(payload["indexed_text"]),
                metadata=payload,
            )
        )
    return results

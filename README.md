# Auditable SEC Filing Research

This MVP compares a monolithic filing-research agent with a verification-first
pipeline using the same frozen SEC evidence. This PR provides the reproducible
parsing, chunking, embedding, and retrieval foundation for that comparison.

## Setup

The project requires Python 3.11 or newer and
[uv](https://docs.astral.sh/uv/). Docker is required only for the local Qdrant
retrieval index.

Install the locked Python dependencies and create the local environment file:

```bash
uv sync
cp .env.example .env
```

Run all commands below from the repository root. Project Python commands use
`uv run`; no manual `PYTHONPATH` configuration is required.

## SEC data collection

The downloader fetches a filing HTML document and SEC Company Facts JSON for
each selected company. It saves local source metadata in `data/manifest.csv`.

Set the required SEC user agent in `.env`:

```dotenv
SEC_USER_AGENT="Your Name your-email@example.com"
```

Download the project's three default companies for fiscal 2025:

```bash
uv run python scripts/download_data.py
```

Optional inputs are:

- `--companies`: one or more SEC-listed ticker symbols;
- `--year`: one or more requested fiscal/report years;
- `--document-type` (or `--form`): the SEC filing form to download, such as
  `10-K` or `10-Q`.

For example:

```bash
uv run python scripts/download_data.py \
  --companies KO AMZN \
  --year 2025 \
  --document-type 10-Q
```

Downloaded files in `data/raw/` and the cached ticker map are local and ignored
by Git. The manifest records their source URLs and SHA-256 checksums. The
checksum integrity test confirms that local source files match the recorded
frozen inputs; it does not affect retrieval or model behavior.

## Filing chunking and retrieval

The retrieval index is built from the frozen 10-K HTML files listed in
`data/manifest.csv`. It does not call the live SEC API during chunking,
embedding, indexing, or search.

In short, the project verifies each frozen source, preserves its physical pages
and tables, uses `sec-parser` to classify SEC content page by page, creates page
and structured-table evidence, and stores dense plus BM25 representations in
local Qdrant:

```text
Frozen 10-K HTML
    -> checksum verification
    -> physical pages and physical HTML tables
    -> per-page sec-parser semantic classification
    -> page, structured-table, and cross-page-table chunks
    -> dense embeddings and BM25 sparse documents
    -> company-filtered hybrid search in local Qdrant
```

The output contains `filing_page`, `filing_table`, and size-safe
`cross_page_table` evidence. Tables are additional structured evidence; they do
not replace complete page chunks. The detailed algorithm, diagrams, metadata,
validation rules, strategy history, previous-parser problems, and parser
decision are documented in the
[`chunking parser migration guide`](docs/chunking_parser_migration.md).

### Reproduce the local index

Before building, the frozen HTML files must exist in `data/raw/` and match
`data/manifest.csv`. If they are not present, follow the SEC data collection
steps above first.

After deleting an unwanted 10-K HTML file from `data/raw/`, synchronize the
manifest before rebuilding:

```bash
uv run python scripts/download_data.py --sync-manifest
```

This removes stale 10-K rows and verifies the checksums of the files that remain.
Add new filings through the downloader so their SEC metadata is recorded.

Start the local Qdrant container:

```bash
docker compose up -d qdrant
```

Build all chunks, validate their token lengths, create embeddings, and replace
the configured Qdrant collection:

```bash
uv run python scripts/index_filings.py rebuild
```

The rebuild checks source hashes and embedding lengths, recreates only the
configured filing collection, prints a build summary, and validates stored
payloads against a fresh deterministic in-memory chunk build. It does not
modify the frozen source files.

Repeat validation independently:

```bash
uv run python scripts/index_filings.py validate
```

Search by company ticker and query:

```bash
uv run python scripts/index_filings.py search \
  KO "operating income and operating margin"
```

Use `--limit` to change the number of returned records:

```bash
uv run python scripts/index_filings.py search \
  AMZN "net sales 2024" --limit 3
```

### Inspect the Qdrant database

With the container running, open the local Qdrant dashboard at
<http://localhost:6333/dashboard> and select the `sec_filing_chunks_v1`
collection.

Inspect the collection's vector configuration, point count, and payload indexes:

```bash
curl -s http://localhost:6333/collections/sec_filing_chunks_v1 | jq
```

Inspect one point's audit payload without returning its large vectors:

```bash
curl -s -X POST \
  http://localhost:6333/collections/sec_filing_chunks_v1/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{"limit":1,"with_payload":true,"with_vector":false}' | jq
```

### Inspect and test the implementation

The main files are:

- `src/chunking.py`: source validation, HTML parsing, overlap, and synthetic
  table chunks;
- `src/embeddings.py`: exact token counting and dense embedding;
- `src/retrieval.py`: Qdrant indexing, validation, and hybrid search;
- `scripts/index_filings.py`: command-line entry point.

The chunk token audit notebook at
[`notebooks/01_chunk_token_audit.ipynb`](notebooks/01_chunk_token_audit.ipynb)
reports chunk sizes, metadata coverage, and—when the local index is
available—retrieval Evidence Recall@5 and Irrelevant Retrieval Rate@5.

Run the focused deterministic checks with:

```bash
uv run pytest tests/test_embeddings.py
```

Search requires a running Qdrant container and a previously built collection.
Checksum mismatch, an oversized filing chunk, unknown company, missing
collection, or unavailable Qdrant produces an explicit error instead of falling
back to live data.

## Evaluation dataset

The completed frozen gold dataset is stored in `data/evaluation/` and documented
in [`data/evaluation/README.md`](data/evaluation/README.md).

It contains 43 verified fact observations and 48 questions split into
company-specific train, validation, and held-out test directories. Every
selected observation is limited to the frozen 10-K accession and cross-checked
against inline XBRL in the corresponding filing.

## Bedrock smoke tests

The smoke test calls Haiku through the Bedrock Runtime Converse API and Luna
through the Bedrock Mantle Responses API. Configure an AWS profile with access
to the required models, then add its name and region to `.env`:

```dotenv
AWS_PROFILE="<your-aws-profile>"
AWS_REGION="<your-aws-region>"
```

Authenticate using the method configured for that profile. For example, an AWS
IAM Identity Center profile uses:

```bash
aws sso login --profile <your-aws-profile>
```

An active cached session can be reused until it expires. Run the selected model
check directly with uv:

```bash
uv run python tests/smoke_test_model.py --model haiku
uv run python tests/smoke_test_model.py --model luna
```

The smoke test sends one short `2 + 2` request to the selected model and prints
the response and token usage. The default Haiku model ID is a US Bedrock Runtime
inference profile. Luna uses Bedrock Mantle and explicitly sets
`reasoning.effort=none`.

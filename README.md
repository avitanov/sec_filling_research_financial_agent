# Auditable SEC Filing Research

## SEC data collection

The downloader fetches a filing HTML document and SEC Company Facts JSON for
each selected company. It saves local source metadata in `data/manifest.csv`.

Create `.env` from `.env.example` and set the required SEC user agent:

```dotenv
SEC_USER_AGENT="Your Name your-email@example.com"
```

Download the project's three default companies for fiscal 2025:

```bash
uv run python scripts/download_data.py
```

Optional inputs are:

- `--companies`: one or more SEC-listed ticker symbols;
- `--year`: one or more requested fiscal/report years.
- `--document-type` (or `--form`): the SEC filing form to download, such as `10-K` or `10-Q`.

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

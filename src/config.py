"""Application configuration for SEC data collection."""

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
MANIFEST_PATH = PROJECT_ROOT / "data" / "manifest.csv"
EVALUATION_DATA_DIR = PROJECT_ROOT / "data" / "evaluation"
RUNS_DIR = PROJECT_ROOT / "data" / "runs"
COMPANY_TICKERS_PATH = PROJECT_ROOT / "data" / "company_tickers.json"
DEFAULT_COMPANIES: tuple[str, ...] = ("KO", "GOOGL", "AMZN")
DEFAULT_REPORT_YEAR = 2025
DEFAULT_DOCUMENT_TYPE = "10-K"

SEC_TICKER_MAPPING_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
SEC_DATA_URL = "https://data.sec.gov"
SEC_REQUEST_TIMEOUT_SECONDS = 30
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1").strip()
BEDROCK_NOVA_LITE_MODEL_ID = os.getenv(
    "BEDROCK_NOVA_LITE_MODEL_ID", "amazon.nova-lite-v1:0"
).strip()
# Amazon Bedrock on-demand text pricing in us-east-1, verified 2026-08-24.
NOVA_LITE_INPUT_USD_PER_MILLION_TOKENS = Decimal("0.06")
NOVA_LITE_OUTPUT_USD_PER_MILLION_TOKENS = Decimal("0.24")
BEDROCK_HAIKU_MODEL_ID = os.getenv(
    "BEDROCK_HAIKU_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
).strip()
BEDROCK_LUNA_MODEL_ID = os.getenv(
    "BEDROCK_LUNA_MODEL_ID", "openai.gpt-5.6-luna"
).strip()
BEDROCK_REASONING_MODE = os.getenv("BEDROCK_REASONING_MODE", "none").strip()
BEDROCK_MANTLE_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_MANTLE_TIMEOUT_SECONDS", "30"))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b").strip()
OPENROUTER_PROVIDER = os.getenv("OPENROUTER_PROVIDER", "coreweave").strip()
OPENROUTER_REASONING_EFFORT = os.getenv("OPENROUTER_REASONING_EFFORT", "low").strip()
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).strip()
OPENROUTER_TIMEOUT_SECONDS = 60
MODEL_MAX_RETRIES = 2
MODEL_MAX_TOKENS = 4096
AGENT_MAX_TURNS = 12
AGENT_MAX_OUTPUT_TOKENS = 12_000
AGENT_MAX_TOTAL_TOKENS = 80_000

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_BASE_URL", "").strip()
LANGFUSE_TRACING_ENVIRONMENT = os.getenv(
    "LANGFUSE_TRACING_ENVIRONMENT", "development"
).strip()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").strip()
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "sec_filing_chunks_v1").strip()
QDRANT_TIMEOUT_SECONDS = int(os.getenv("QDRANT_TIMEOUT_SECONDS", "30"))
QDRANT_DENSE_MODEL = "snowflake/snowflake-arctic-embed-m-long"
QDRANT_DENSE_VECTOR_SIZE = 768
QDRANT_DENSE_MAX_TOKENS = 2048
RETRIEVAL_CANDIDATE_LIMIT = 30
FILING_CHUNK_OVERLAP_WORDS = 100
FILING_CHUNK_PARSER_VERSION = "3"

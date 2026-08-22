"""Application configuration for SEC data collection."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
MANIFEST_PATH = PROJECT_ROOT / "data" / "manifest.csv"
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
BEDROCK_HAIKU_MODEL_ID = os.getenv(
    "BEDROCK_HAIKU_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
).strip()
BEDROCK_LUNA_MODEL_ID = os.getenv(
    "BEDROCK_LUNA_MODEL_ID", "openai.gpt-5.6-luna"
).strip()
BEDROCK_MANTLE_TIMEOUT_SECONDS = int(os.getenv("BEDROCK_MANTLE_TIMEOUT_SECONDS", "30"))

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").strip()
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "sec_filing_chunks_v1").strip()
QDRANT_TIMEOUT_SECONDS = int(os.getenv("QDRANT_TIMEOUT_SECONDS", "30"))
QDRANT_DENSE_MODEL = "snowflake/snowflake-arctic-embed-m-long"
QDRANT_DENSE_VECTOR_SIZE = 768
QDRANT_DENSE_MAX_TOKENS = 2048
RETRIEVAL_CANDIDATE_LIMIT = 30
FILING_CHUNK_OVERLAP_WORDS = 100
FILING_CHUNK_PARSER_VERSION = "3"

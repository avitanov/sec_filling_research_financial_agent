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

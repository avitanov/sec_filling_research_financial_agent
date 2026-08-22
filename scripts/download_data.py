"""Discover and download selected SEC filing data."""

# Provide tickers, one or more report years, and an optional SEC document form.
# The script discovers the CIK from
# SEC's company_tickers.json mapping, then discovers the filing metadata from
# the company's SEC submissions JSON. The submissions data provides the
# company name, report date, filing date, accession number, and primary file.
#
# The SEC ticker mapping is cached at data/company_tickers.json after its first
# download. The two files for each selected company-year-form are saved under
# data/raw/<TICKER>/.
# If no CLI arguments are provided, the default companies and report year from
# src/config.py are downloaded.

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from config import (  # noqa: E402
    COMPANY_TICKERS_PATH,
    DEFAULT_COMPANIES,
    DEFAULT_DOCUMENT_TYPE,
    DEFAULT_REPORT_YEAR,
    MANIFEST_PATH,
    PROJECT_ROOT,
    RAW_DATA_DIR,
    SEC_ARCHIVES_URL,
    SEC_DATA_URL,
    SEC_REQUEST_TIMEOUT_SECONDS,
    SEC_TICKER_MAPPING_URL,
    SEC_USER_AGENT,
)
from exceptions import FilingNotFoundError  # noqa: E402
from models import FilingMetadata  # noqa: E402
from utils import write_json  # noqa: E402

MANIFEST_FIELDS = [
    "ticker",
    "document_type",
    "company_name",
    "cik",
    "report_year",
    "report_date",
    "filing_date",
    "accession_number",
    "primary_document",
    "source_type",
    "source_url",
    "local_path",
    "sha256",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_arguments() -> argparse.Namespace:
    """Parse the ticker and report-year arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sync-manifest",
        action="store_true",
        help=(
            "remove 10-K manifest rows whose local HTML file no longer exists; "
            "does not call the SEC"
        ),
    )
    parser.add_argument(
        "--companies",
        nargs="+",
        default=None,
        metavar="TICKER",
        help="one or more SEC-listed company tickers (default: project companies)",
    )
    parser.add_argument(
        "--year",
        nargs="+",
        type=int,
        default=None,
        help=(
            "one or more fiscal/report years of the requested filings "
            f"(default: {DEFAULT_REPORT_YEAR})"
        ),
    )
    parser.add_argument(
        "--document-type",
        "--form",
        dest="document_type",
        default=DEFAULT_DOCUMENT_TYPE,
        metavar="FORM",
        help=(
            "SEC filing form to download, such as 10-K or 10-Q "
            f"(default: {DEFAULT_DOCUMENT_TYPE})"
        ),
    )
    return parser.parse_args()


def _get_user_agent() -> str:
    """Return the required SEC user-agent or stop with setup instructions."""
    if not SEC_USER_AGENT:
        raise SystemExit(
            "SEC_USER_AGENT is required. Set it with: "
            'SEC_USER_AGENT="Your Name your-email@example.com" in .env.'
        )
    return SEC_USER_AGENT


def _fetch_json(url: str, user_agent: str) -> dict[str, Any]:
    """Fetch one SEC JSON response and return its parsed data."""
    response = requests.get(
        url,
        headers={"User-Agent": user_agent},
        timeout=SEC_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise SystemExit(f"Unexpected JSON object from SEC endpoint: {url}")
    return data


def _get_company_tickers(user_agent: str) -> dict[str, str]:
    """Load the SEC ticker mapping from disk or fetch it once and cache it."""
    if COMPANY_TICKERS_PATH.exists():
        mapping_data = json.loads(
            COMPANY_TICKERS_PATH.read_text(encoding="utf-8")
        )
    else:
        mapping_data = _fetch_json(
            SEC_TICKER_MAPPING_URL,
            user_agent,
        )
        COMPANY_TICKERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_json(mapping_data, COMPANY_TICKERS_PATH)

    ticker_mapping: dict[str, str] = {}
    for record in mapping_data.values():
        if not isinstance(record, dict):
            continue
        ticker = record.get("ticker")
        cik = record.get("cik_str")
        if ticker is not None and cik is not None:
            ticker_mapping[str(ticker).upper()] = str(cik).zfill(10)
    return ticker_mapping


def _find_filing(
    ticker: str,
    cik: str,
    year: int,
    document_type: str,
    submissions: dict[str, Any],
) -> FilingMetadata:
    """Find the requested SEC filing whose report date belongs to the year."""
    recent_filings = submissions.get("filings", {}).get("recent", {})
    forms = recent_filings.get("form", [])
    report_dates = recent_filings.get("reportDate", [])
    filing_dates = recent_filings.get("filingDate", [])
    accessions = recent_filings.get("accessionNumber", [])
    primary_documents = recent_filings.get("primaryDocument", [])

    for index, form in enumerate(forms):
        if form != document_type:
            continue
        if index >= len(report_dates):
            continue
        report_date = report_dates[index]
        if not report_date or str(report_date)[:4] != str(year):
            continue
        if index >= len(filing_dates):
            continue
        if index >= len(accessions):
            continue
        if index >= len(primary_documents):
            continue

        company_name = str(submissions.get("name", "")).strip()
        accession_number = str(accessions[index]).strip()
        primary_document = str(primary_documents[index]).strip()
        if not company_name or not accession_number or not primary_document:
            continue
        return FilingMetadata(
            ticker=ticker.upper(),
            document_type=document_type,
            company_name=company_name,
            cik=cik,
            report_year=year,
            report_date=str(report_date),
            filing_date=str(filing_dates[index]),
            accession_number=accession_number,
            primary_document=primary_document,
        )

    raise FilingNotFoundError(
        f"No {document_type} found for {ticker.upper()} with report year {year}."
    )


def _build_source_urls(metadata: FilingMetadata) -> dict[str, str]:
    """Build official SEC URLs from discovered filing metadata."""
    archive_cik = metadata.cik.lstrip("0")
    accession_path = metadata.accession_number.replace("-", "")
    filing_url = (
        f"{SEC_ARCHIVES_URL}/{archive_cik}/{accession_path}/"
        f"{metadata.primary_document}"
    )
    html_source_type = f"{metadata.document_type} HTML"
    return {
        html_source_type: filing_url,
        "Submissions": (
            f"{SEC_DATA_URL}/submissions/CIK{metadata.cik.zfill(10)}.json"
        ),
        "Company Facts": (
            f"{SEC_DATA_URL}/api/xbrl/companyfacts/"
            f"CIK{metadata.cik.zfill(10)}.json"
        ),
    }


def _download_file(url: str, destination: Path, user_agent: str) -> None:
    """Download one SEC file to a local path."""
    response = requests.get(
        url,
        headers={"User-Agent": user_agent},
        timeout=SEC_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    destination.write_bytes(response.content)


def _download_json(url: str, destination: Path, user_agent: str) -> None:
    """Download and write one SEC JSON response in an indented format."""
    data = _fetch_json(url, user_agent)
    write_json(data, destination)


def _build_local_paths(metadata: FilingMetadata) -> dict[str, Path]:
    """Build year-specific local paths for one filing's retrieval files."""
    company_dir = RAW_DATA_DIR / metadata.ticker
    year = metadata.report_year
    document_type = metadata.document_type.replace("/", "-")
    return {
        f"{metadata.document_type} HTML": company_dir / f"{document_type}-{year}.html",
        "Company Facts": company_dir / f"companyfacts-{year}.json",
    }


def _download_company(
    ticker: str,
    cik: str,
    year: int,
    document_type: str,
    user_agent: str,
) -> list[dict[str, str]]:
    """Discover one filing and download its HTML and Company Facts files."""
    url = f"{SEC_DATA_URL}/submissions/CIK{cik.zfill(10)}.json"
    submissions = _fetch_json(url, user_agent)
    metadata = _find_filing(ticker, cik, year, document_type, submissions)
    source_urls = _build_source_urls(metadata)
    local_paths = _build_local_paths(metadata)
    html_source_type = f"{metadata.document_type} HTML"
    company_dir = local_paths[html_source_type].parent
    company_dir.mkdir(parents=True, exist_ok=True)

    _download_file(
        source_urls[html_source_type],
        local_paths[html_source_type],
        user_agent,
    )
    _download_json(
        source_urls["Company Facts"],
        local_paths["Company Facts"],
        user_agent,
    )
    _remove_previous_filing_files(
        metadata.ticker,
        metadata.report_year,
        metadata.document_type,
        local_paths,
    )
    files = [
        (source_type, local_paths[source_type])
        for source_type in (html_source_type, "Company Facts")
    ]
    return [
        {
            "ticker": metadata.ticker,
            "document_type": metadata.document_type,
            "company_name": metadata.company_name,
            "cik": metadata.cik,
            "report_year": str(metadata.report_year),
            "report_date": metadata.report_date,
            "filing_date": metadata.filing_date,
            "accession_number": metadata.accession_number,
            "primary_document": metadata.primary_document,
            "source_type": source_type,
            "source_url": source_urls[source_type],
            "local_path": path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": _sha256(path),
        }
        for source_type, path in files
    ]


def _remove_previous_filing_files(
    ticker: str,
    year: int,
    document_type: str,
    current_paths: dict[str, Path],
) -> None:
    """Remove prior files for the same ticker, year, and document form."""
    current_path_set = set(current_paths.values())
    html_source_type = f"{document_type} HTML"
    for row in _read_manifest_rows():
        if row["ticker"] != ticker or row["report_year"] != str(year):
            continue
        if row["source_type"] not in {html_source_type, "Company Facts"}:
            continue
        previous_path = PROJECT_ROOT / row["local_path"]
        if previous_path not in current_path_set and previous_path.is_file():
            previous_path.unlink()


def _read_manifest_rows() -> list[dict[str, str]]:
    """Read and normalize rows from the existing manifest."""
    if not MANIFEST_PATH.exists():
        return []
    with MANIFEST_PATH.open(newline="", encoding="utf-8") as manifest_file:
        rows: list[dict[str, str]] = []
        for old_row in csv.DictReader(manifest_file):
            local_path = old_row.get("local_path") or old_row.get(
                "local_file_path", ""
            )
            source_url = old_row.get("source_url") or old_row.get(
                "sec_source_url", ""
            )
            row = {field: old_row.get(field, "") for field in MANIFEST_FIELDS}
            row["local_path"] = local_path
            row["source_url"] = source_url
            if not row["document_type"] and row["source_type"].endswith(" HTML"):
                row["document_type"] = row["source_type"][: -len(" HTML")]
            if not row["primary_document"] and row["source_type"].endswith(
                " HTML"
            ):
                row["primary_document"] = Path(local_path).name
            rows.append(row)
        return rows


def _write_manifest(rows: list[dict[str, str]]) -> None:
    """Write all source rows to the single CSV manifest."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.DictWriter(
            manifest_file,
            fieldnames=MANIFEST_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _update_manifest(downloaded_rows: list[dict[str, str]]) -> None:
    """Replace manifest rows for downloaded ticker-year-source pairs."""
    downloaded_keys = {
        (row["ticker"], row["report_year"], row["source_type"])
        for row in downloaded_rows
    }
    existing_rows = [
        row
        for row in _read_manifest_rows()
        if (
            row["ticker"],
            row["report_year"],
            row["source_type"],
        ) not in downloaded_keys
    ]
    _write_manifest(existing_rows + downloaded_rows)


def _sync_10k_manifest() -> None:
    """Synchronize tracked 10-K rows with the HTML files currently in data/raw.

    Raises:
        SystemExit: If an existing file changed or an untracked 10-K is present.
    """
    rows = _read_manifest_rows()
    tracked_10k_rows = [row for row in rows if row["source_type"] == "10-K HTML"]
    tracked_paths = {
        (PROJECT_ROOT / row["local_path"]).resolve(): row for row in tracked_10k_rows
    }
    raw_10k_paths = {path.resolve() for path in RAW_DATA_DIR.rglob("10-K-*.html")}
    untracked_paths = sorted(raw_10k_paths - tracked_paths.keys())
    if untracked_paths:
        formatted_paths = ", ".join(
            path.relative_to(PROJECT_ROOT).as_posix() for path in untracked_paths
        )
        raise SystemExit(
            "Untracked 10-K HTML files cannot be added automatically because "
            "their SEC provenance is missing from the manifest: "
            f"{formatted_paths}. Add them with scripts/download_data.py."
        )

    retained_rows: list[dict[str, str]] = []
    removed_rows: list[dict[str, str]] = []
    for row in rows:
        if row["source_type"] != "10-K HTML":
            retained_rows.append(row)
            continue
        path = (PROJECT_ROOT / row["local_path"]).resolve()
        if not path.is_file():
            removed_rows.append(row)
            continue
        actual_checksum = _sha256(path)
        if actual_checksum != row["sha256"]:
            raise SystemExit(
                f"Checksum mismatch for {row['local_path']}. The manifest was not "
                "changed; download the filing again or review the source change."
            )
        retained_rows.append(row)

    _write_manifest(retained_rows)
    print("10-K manifest synchronization complete.")
    print(f"Tracked 10-K files present: {len(tracked_10k_rows) - len(removed_rows)}")
    print(f"Missing 10-K rows removed: {len(removed_rows)}")
    for row in removed_rows:
        print(f"  Removed: {row['ticker']} {row['report_year']} {row['local_path']}")


def main() -> None:
    """Discover and download data for each requested ticker and report year."""
    arguments = _parse_arguments()
    if arguments.sync_manifest:
        _sync_10k_manifest()
        return
    companies = arguments.companies or list(DEFAULT_COMPANIES)
    years = arguments.year or [DEFAULT_REPORT_YEAR]
    document_type = arguments.document_type.upper()
    user_agent = _get_user_agent()
    ticker_mapping = _get_company_tickers(user_agent)
    downloaded_rows: list[dict[str, str]] = []
    downloaded_selections = 0
    skipped_downloads: list[str] = []

    for year in years:
        for ticker in companies:
            normalized_ticker = ticker.upper()
            print(
                f"Downloading {normalized_ticker} ({year}, {document_type})..."
            )
            cik = ticker_mapping.get(normalized_ticker)
            if cik is None:
                skipped_downloads.append(f"{normalized_ticker} ({year})")
                print(
                    "  Skipping: Ticker not found in SEC company mapping: "
                    f"{normalized_ticker}"
                )
                print()
                continue

            try:
                rows = _download_company(
                    normalized_ticker,
                    cik,
                    year,
                    document_type,
                    user_agent,
                )
            except FilingNotFoundError as error:
                skipped_downloads.append(f"{normalized_ticker} ({year})")
                print(f"  Skipping: {error}")
                print()
                continue

            downloaded_rows.extend(rows)
            downloaded_selections += 1
            print(f"  {document_type} HTML: saved")
            print("  Company Facts: saved")
            print()

    _update_manifest(downloaded_rows)
    print(
        f"Done. Downloaded {downloaded_selections} "
        f"company-year selection(s) ({document_type})."
    )
    if skipped_downloads:
        print("Skipped selections: " + ", ".join(skipped_downloads))


if __name__ == "__main__":
    main()

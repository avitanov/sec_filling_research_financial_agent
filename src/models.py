"""Typed records shared by the SEC data-collection code."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FilingMetadata:
    """Store filing metadata discovered from SEC submissions data."""

    ticker: str
    document_type: str
    company_name: str
    cik: str
    report_year: int
    report_date: str
    filing_date: str
    accession_number: str
    primary_document: str

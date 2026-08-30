"""Build deterministic, auditable chunks from frozen SEC 10-K HTML."""

import csv
import hashlib
import re
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import sec_parser as sp
from bs4.element import Tag

from config import (
    FILING_CHUNK_OVERLAP_WORDS,
    FILING_CHUNK_PARSER_VERSION,
    MANIFEST_PATH,
    PROJECT_ROOT,
)
from exceptions import ChunkValidationError
from models import (
    FilingChunk,
    FilingHTMLParser,
    FilingPage,
    FilingSource,
    TableFragment,
)
from utils import normalize_label, normalize_text, unique_strings

ITEM_PATTERN = re.compile(r"\bItem\s+(\d+[A-Z]?)\b", re.IGNORECASE)
NOTE_PATTERN = re.compile(r"\bNote\s+(\d+)\b", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
PERIOD_HEADER_PATTERN = re.compile(
    r"\b(?:year|period)\s+ended\b|\bas\s+of\b|\b(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
PAGE_BREAK_PATTERN = re.compile(
    r"<hr\b[^>]*style=[\"'][^\"']*page-break-after\s*:\s*always"
    r"[^\"']*[\"'][^>]*?/?>",
    re.IGNORECASE,
)
UNIT_QUALIFIER_PATTERN = re.compile(
    r"(?:amounts\s+)?(?:in\s+)?(millions?|thousands?|billions?)"
    r"(?:\s*,?\s*except\s+[^():;]{1,80})?",
    re.IGNORECASE,
)
TABLE_CONTEXT_WORDS = 60
TABLE_EDGE_WORDS = 40
PAGE_BOUNDARY_WORDS = 6
ROW_LABEL_SIMILARITY = 0.60
COMBINED_TABLE_MAX_WORDS = 900


@dataclass
class _SemanticState:
    item: str | None = None
    note: str | None = None
    section_heading: str | None = None


def _page_id(ticker: str, report_year: int, sequence: int) -> str:
    return f"filing-{ticker.lower()}-{report_year}-page-{sequence:04d}"


@lru_cache(maxsize=None)
def load_filing_sources(
    manifest_path: Path = MANIFEST_PATH,
) -> tuple[FilingSource, ...]:
    """Load frozen 10-K sources from the repository manifest.

    Args:
        manifest_path: CSV manifest containing frozen source metadata.

    Returns:
        The three 10-K HTML sources sorted by ticker.
    """
    sources: list[FilingSource] = []
    with manifest_path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            if row["source_type"] != "10-K HTML":
                continue
            sources.append(
                FilingSource(
                    ticker=row["ticker"],
                    company_name=row["company_name"],
                    cik=row["cik"],
                    report_year=int(row["report_year"]),
                    report_date=row["report_date"],
                    filing_date=row["filing_date"],
                    accession_number=row["accession_number"],
                    primary_document=row["primary_document"],
                    source_url=row["source_url"],
                    local_path=row["local_path"],
                    sha256=row["sha256"],
                )
            )
    return tuple(sorted(sources, key=lambda source: source.ticker))


def parse_filing_pages(source: FilingSource) -> list[FilingPage]:
    """Parse one frozen filing into physical pages and positioned tables.

    Args:
        source: Frozen filing metadata.

    Returns:
        Physical pages in source order.

    Raises:
        ChunkValidationError: If the source or semantic mapping is invalid.
    """
    path = PROJECT_ROOT / source.local_path
    raw = path.read_bytes()
    actual_checksum = hashlib.sha256(raw).hexdigest()
    if actual_checksum != source.sha256:
        raise ChunkValidationError(
            f"Source checksum mismatch for {source.ticker}: {actual_checksum}"
        )
    html = raw.decode("utf-8", errors="replace")
    parser = FilingHTMLParser(source.ticker, source.report_year)
    parser.feed(html)
    parser.close()
    raw_pages = PAGE_BREAK_PATTERN.split(html)
    ## Checking if splitting with html parser is the same as using regex pattern
    if len(raw_pages) != len(parser.pages):
        raise ChunkValidationError(
            f"Physical page split mismatch for {source.ticker}: "
            f"{len(raw_pages)} HTML fragments and {len(parser.pages)} parsed pages"
        )

    state = _SemanticState()
    for page, raw_page in zip(parser.pages, raw_pages, strict=True):
        _add_semantic_page_data(page, raw_page, state)
    _assign_continuation_groups(source, parser.pages)
    return parser.pages


def _table_match_key(text: str) -> str:
    without_markers = text.replace("[ROW]", "").replace("|", "")
    return re.sub(r"\s+", "", without_markers).casefold()


def _useful_heading(text: str) -> str | None:
    heading = normalize_text(text)
    if not heading or heading.casefold() == "table of contents":
        return None
    if re.fullmatch(r"\d{1,3}\.?", heading) or len(heading.split()) > 30:
        return None
    return heading


def _apply_heading(state: _SemanticState, heading: str) -> None:
    item_match = ITEM_PATTERN.match(heading)
    note_match = NOTE_PATTERN.match(heading)
    if item_match:
        state.item = f"Item {item_match.group(1).upper()}"
        state.note = None
    if note_match:
        state.note = f"Note {note_match.group(1)}"
    state.section_heading = heading


def _record_page_context(page: FilingPage, state: _SemanticState) -> None:
    if state.item and state.item not in page.items:
        page.items.append(state.item)
    if state.note and state.note not in page.notes:
        page.notes.append(state.note)


def _table_context(page: FilingPage, table: TableFragment) -> str | None:
    before = page.words[
        max(0, table.start_word - TABLE_CONTEXT_WORDS) : table.start_word
    ]
    context = " ".join(before)
    return context or None


def _unit_metadata(
    table: TableFragment,
) -> tuple[str | None, list[str]]:
    context = table.context_text or ""
    matches = list(UNIT_QUALIFIER_PATTERN.finditer(context))
    qualifier = normalize_text(matches[-1].group(0)) if matches else None
    units: list[str] = []
    if "$" in table.text:
        units.append("USD")
    if "%" in table.text:
        units.append("percent")
    if qualifier and "per share" in qualifier.casefold():
        units.append("USD/share")
    return qualifier, units


def _periods(text: str) -> list[str]:
    return unique_strings(YEAR_PATTERN.findall(text))


def _match_table_fragment(
    element: sp.TableElement,
    tables: list[TableFragment],
    used_fragment_ids: set[str],
) -> TableFragment:
    match_key = _table_match_key(element.text)
    matches = [
        table
        for table in tables
        if table.fragment_id not in used_fragment_ids
        and _table_match_key(table.text) == match_key
    ]
    if len(matches) != 1:
        raise ChunkValidationError(
            f"Expected one physical table match, found {len(matches)} for "
            f"{element.text[:80]!r}"
        )
    return matches[0]


def _logical_grid(container: Tag, fragment: TableFragment) -> list[list[str]]:
    tables = [container] if container.name == "table" else container.find_all("table")
    expected_key = _table_match_key(fragment.text)
    table_tag: Tag | None = None
    for candidate in tables:
        candidate_key = _table_match_key(candidate.get_text(" ", strip=True))
        if candidate_key == expected_key or expected_key in candidate_key:
            table_tag = candidate
            break
    if table_tag is None:
        return fragment.rows

    rows: list[list[str]] = []
    occupied: set[tuple[int, int]] = set()
    for row_index, row_tag in enumerate(table_tag.find_all("tr")):
        if row_tag.find_parent("table") is not table_tag:
            continue
        row: list[str] = []
        column = 0
        cells = row_tag.find_all(["td", "th"], recursive=False)
        for cell in cells:
            while (row_index, column) in occupied:
                row.append("")
                column += 1
            text = normalize_text(cell.get_text(" ", strip=True))
            colspan = max(int(cell.get("colspan", 1)), 1)
            rowspan = max(int(cell.get("rowspan", 1)), 1)
            row.append(text)
            for offset in range(1, colspan):
                row.append("")
                occupied.add((row_index, column + offset))
            for down in range(1, rowspan):
                for offset in range(colspan):
                    occupied.add((row_index + down, column + offset))
            column += colspan
        while (row_index, column) in occupied:
            row.append("")
            column += 1
        if any(row):
            rows.append(row)
    if not rows:
        return fragment.rows

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    used_columns = [
        column for column in range(width) if any(row[column] for row in rows)
    ]
    return [[row[column] for column in used_columns] for row in rows]


def _is_substantial_table(table: TableFragment) -> bool:
    cells = sum(len(row) for row in table.rows)
    return len(table.rows) >= 2 and cells >= 4 and table.numeric_cell_count >= 2


def _is_fallback_numeric_table(table: TableFragment) -> bool:
    if not _is_substantial_table(table):
        return False
    first_cell = next((cell for row in table.rows for cell in row if cell), "")
    if first_cell.casefold() == "page":
        return False
    has_unit = bool(UNIT_QUALIFIER_PATTERN.search(table.context_text or ""))
    has_financial_symbol = "$" in table.text or "%" in table.text
    return has_unit or has_financial_symbol


def _set_table_metadata(
    page: FilingPage,
    table: TableFragment,
    state: _SemanticState,
    container: Tag | None,
    parser_source: str,
) -> None:
    if container is not None:
        table.rows = _logical_grid(container, table)
    table.title = state.section_heading
    table.context_text = _table_context(page, table)
    table.item = state.item
    table.note = state.note
    table.parser_source = parser_source
    qualifier, units = _unit_metadata(table)
    table.unit_qualifier = qualifier
    table.units = units
    table.periods = _periods(table.text)


def _fallback_tables_in_element(
    element: sp.AbstractSemanticElement,
    page: FilingPage,
    state: _SemanticState,
    used_fragment_ids: set[str],
) -> list[TableFragment]:
    element_key = _table_match_key(element.text)
    matches: list[TableFragment] = []
    for table in page.tables:
        if table.fragment_id in used_fragment_ids:
            continue
        if _table_match_key(table.text) not in element_key:
            continue
        table.context_text = _table_context(page, table)
        if not _is_fallback_numeric_table(table):
            continue
        container = getattr(element.html_tag, "_bs4", None)
        _set_table_metadata(page, table, state, container, "numeric-text-fallback")
        used_fragment_ids.add(table.fragment_id)
        matches.append(table)
    return matches


def _add_semantic_page_data(
    page: FilingPage, raw_page: str, state: _SemanticState
) -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Invalid section type.*",
            category=UserWarning,
        )
        elements = sp.Edgar10KParser().parse(raw_page)

    raw_tables = list(page.tables)
    meaningful_tables: list[TableFragment] = []
    used_fragment_ids: set[str] = set()
    page.items = []
    page.notes = []
    context_recorded = False
    for element in elements:
        if isinstance(element, (sp.TopSectionTitle, sp.TitleElement)):
            heading = _useful_heading(element.text)
            if heading:
                _apply_heading(state, heading)
                _record_page_context(page, state)
                context_recorded = True
            continue
        if isinstance(element, sp.TableElement):
            if type(element).__name__ == "TableOfContentsElement":
                continue
            table = _match_table_fragment(element, raw_tables, used_fragment_ids)
            container = getattr(element.html_tag, "_bs4", None)
            _set_table_metadata(page, table, state, container, "sec-parser")
            meaningful_tables.append(table)
            used_fragment_ids.add(table.fragment_id)
            if not context_recorded:
                _record_page_context(page, state)
                context_recorded = True
            continue
        fallback_tables = _fallback_tables_in_element(
            element, page, state, used_fragment_ids
        )
        meaningful_tables.extend(fallback_tables)
        if not context_recorded and normalize_text(element.text):
            _record_page_context(page, state)
            context_recorded = True

    page.tables = sorted(meaningful_tables, key=lambda table: table.start_word)


def _row_labels(table: TableFragment) -> set[str]:
    labels: set[str] = set()
    for row in table.rows:
        for cell in row:
            label = normalize_label(cell)
            if len(label) >= 3:
                labels.add(label)
                break
    return labels


def _label_similarity(left: TableFragment, right: TableFragment) -> float:
    left_labels = _row_labels(left)
    right_labels = _row_labels(right)
    shared = left_labels & right_labels
    if len(shared) < 2:
        return 0.0
    union = left_labels | right_labels
    return len(shared) / len(union) if union else 0.0


def _first_row_key(table: TableFragment) -> str:
    if not table.rows:
        return ""
    return normalize_text(" ".join(cell for cell in table.rows[0] if cell)).casefold()


def _continuation_reason(left: TableFragment, right: TableFragment) -> str | None:
    if _label_similarity(left, right) >= ROW_LABEL_SIMILARITY:
        return "matching_row_labels"
    first_row = _first_row_key(left)
    if (
        first_row
        and first_row == _first_row_key(right)
        and not PERIOD_HEADER_PATTERN.search(first_row)
    ):
        return "repeated_descriptive_header"
    return None


def _split_at_page_boundary(
    left_page: FilingPage,
    left: TableFragment,
    right: TableFragment,
) -> bool:
    return (
        left.end_word >= len(left_page.words)
        and right.start_word <= PAGE_BOUNDARY_WORDS
    )


def _edge_tables(page: FilingPage, side: str) -> list[TableFragment]:
    if side == "left":
        return [
            table
            for table in page.tables
            if len(page.words) - table.end_word <= TABLE_EDGE_WORDS
        ]
    return [table for table in page.tables if table.start_word <= TABLE_EDGE_WORDS]


def _assign_continuation_groups(source: FilingSource, pages: list[FilingPage]) -> None:
    parent: dict[str, str] = {
        table.fragment_id: table.fragment_id for page in pages for table in page.tables
    }

    def find(fragment_id: str) -> str:
        while parent[fragment_id] != fragment_id:
            parent[fragment_id] = parent[parent[fragment_id]]
            fragment_id = parent[fragment_id]
        return fragment_id

    def union(left: TableFragment, right: TableFragment) -> None:
        left_root = find(left.fragment_id)
        right_root = find(right.fragment_id)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_page, right_page in zip(pages, pages[1:], strict=False):
        left_edge = _edge_tables(left_page, "left")
        right_edge = _edge_tables(right_page, "right")
        if not left_edge or not right_edge:
            continue
        left = left_edge[-1]
        right = right_edge[0]
        if not _continuation_reason(left, right) and not _split_at_page_boundary(
            left_page, left, right
        ):
            continue
        union(left, right)
        left_index = left_page.tables.index(left)
        for candidate in reversed(left_page.tables[:left_index]):
            if _continuation_reason(candidate, right):
                union(candidate, right)
            else:
                break
        right_index = right_page.tables.index(right)
        for candidate in right_page.tables[right_index + 1 :]:
            if _continuation_reason(left, candidate):
                union(left, candidate)
            else:
                break

    groups: dict[str, list[TableFragment]] = {}
    for page in pages:
        for table in page.tables:
            groups.setdefault(find(table.fragment_id), []).append(table)
    continued_groups = [
        sorted(group, key=lambda table: (table.page_sequence, table.start_word))
        for group in groups.values()
        if len({table.page_sequence for table in group}) > 1
    ]
    continued_groups.sort(
        key=lambda group: (group[0].page_sequence, group[0].start_word)
    )
    for number, group in enumerate(continued_groups, 1):
        group_id = (
            f"filing-{source.ticker.lower()}-{source.report_year}-"
            f"table-group-{number:03d}"
        )
        title = next((table.title for table in group if table.title), None)
        context = next(
            (table.context_text for table in group if table.context_text), None
        )
        qualifier = next(
            (table.unit_qualifier for table in group if table.unit_qualifier), None
        )
        units = unique_strings([unit for table in group for unit in table.units])
        for table in group:
            table.continuation_group_id = group_id
            table.title = table.title or title
            table.context_text = table.context_text or context
            table.unit_qualifier = table.unit_qualifier or qualifier
            table.units = unique_strings(table.units + units)


def _build_continuation_groups(pages: list[FilingPage]) -> list[dict[str, Any]]:
    groups: dict[str, list[TableFragment]] = {}
    for page in pages:
        for table in page.tables:
            if table.continuation_group_id:
                groups.setdefault(table.continuation_group_id, []).append(table)
    records: list[dict[str, Any]] = []
    for group_id, fragments in sorted(groups.items()):
        fragments.sort(key=lambda table: (table.page_sequence, table.start_word))
        combined_words = sum(len(table.text.split()) for table in fragments)
        records.append(
            {
                "continuation_group_id": group_id,
                "supporting_page_sequences": list(
                    dict.fromkeys(table.page_sequence for table in fragments)
                ),
                "table_fragment_ids": [table.fragment_id for table in fragments],
                "combined_chunk_created": combined_words <= COMBINED_TABLE_MAX_WORDS,
            }
        )
    return records


def _base_chunk_fields(source: FilingSource) -> dict[str, Any]:
    return {
        "chunk_schema_version": "3",
        "document_type": "10-K",
        "company": source.company_name,
        "ticker": source.ticker,
        "cik": source.cik,
        "accession_number": source.accession_number,
        "report_year": source.report_year,
        "report_date": source.report_date,
        "filing_date": source.filing_date,
        "source_url": source.source_url,
        "source_path": source.local_path,
        "primary_document": source.primary_document,
        "source_sha256": source.sha256,
        "parser_version": FILING_CHUNK_PARSER_VERSION,
    }


def _build_page_chunks(
    source: FilingSource, pages: list[FilingPage], overlap_words: int
) -> list[FilingChunk]:
    chunks: list[FilingChunk] = []
    for index, page in enumerate(pages):
        before_page = pages[index - 1] if index else None
        after_page = pages[index + 1] if index + 1 < len(pages) else None
        before_words = before_page.words[-overlap_words:] if before_page else []
        after_words = after_page.words[:overlap_words] if after_page else []
        before_text = " ".join(before_words)
        after_text = " ".join(after_words)
        indexed_text = normalize_text(" ".join((before_text, page.text, after_text)))
        page_id = _page_id(source.ticker, source.report_year, page.sequence)
        chunks.append(
            FilingChunk(
                evidence_id=page_id,
                evidence_type="filing_page",
                printed_page=page.printed_page,
                supporting_page_ids=[page_id],
                supporting_page_sequences=[page.sequence],
                section_title=None,
                table_fragment_ids=[table.fragment_id for table in page.tables],
                continuation_group_id=None,
                core_text=page.text,
                overlap_before_text=before_text,
                overlap_after_text=after_text,
                indexed_text=indexed_text,
                overlap_before_page_id=(
                    _page_id(source.ticker, source.report_year, before_page.sequence)
                    if before_page
                    else None
                ),
                overlap_after_page_id=(
                    _page_id(source.ticker, source.report_year, after_page.sequence)
                    if after_page
                    else None
                ),
                core_word_count=len(page.words),
                overlap_before_word_count=len(before_words),
                overlap_after_word_count=len(after_words),
                content_sha256=hashlib.sha256(indexed_text.encode("utf-8")).hexdigest(),
                items=page.items,
                notes=page.notes,
                **_base_chunk_fields(source),
            )
        )
    return chunks


def _table_core_text(table: TableFragment) -> str:
    parts = [table.title or ""]
    if table.context_text:
        parts.append(f"[CONTEXT] {table.context_text}")
    if table.unit_qualifier:
        parts.append(f"[UNIT] {table.unit_qualifier}")
    parts.append(f"[TABLE] {table.text}")
    return normalize_text(" ".join(parts))


def _build_table_chunk(
    source: FilingSource,
    evidence_id: str,
    evidence_type: str,
    tables: list[TableFragment],
    page: FilingPage | None,
) -> FilingChunk:
    first = tables[0]
    core_text = normalize_text(
        " [PAGE BREAK] ".join(_table_core_text(table) for table in tables)
    )
    page_sequences = list(dict.fromkeys(table.page_sequence for table in tables))
    page_ids = [
        _page_id(source.ticker, source.report_year, sequence)
        for sequence in page_sequences
    ]
    parser_sources = {table.parser_source for table in tables}
    return FilingChunk(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        printed_page=page.printed_page if page else None,
        supporting_page_ids=page_ids,
        supporting_page_sequences=page_sequences,
        section_title=first.title,
        table_fragment_ids=[table.fragment_id for table in tables],
        continuation_group_id=first.continuation_group_id,
        core_text=core_text,
        overlap_before_text="",
        overlap_after_text="",
        indexed_text=core_text,
        overlap_before_page_id=None,
        overlap_after_page_id=None,
        core_word_count=len(core_text.split()),
        overlap_before_word_count=0,
        overlap_after_word_count=0,
        content_sha256=hashlib.sha256(core_text.encode("utf-8")).hexdigest(),
        items=[first.item] if first.item else [],
        notes=[first.note] if first.note else [],
        table_units=unique_strings([unit for table in tables for unit in table.units]),
        table_periods=unique_strings(
            [period for table in tables for period in table.periods]
        ),
        table_row_count=sum(len(table.rows) for table in tables),
        table_column_count=max(table.column_count for table in tables),
        table_parser_source=(
            first.parser_source if len(parser_sources) == 1 else "mixed"
        ),
        **_base_chunk_fields(source),
    )


def _build_single_page_table_chunks(
    source: FilingSource, pages: list[FilingPage]
) -> list[FilingChunk]:
    chunks: list[FilingChunk] = []
    for page in pages:
        for table in page.tables:
            chunks.append(
                _build_table_chunk(
                    source,
                    table.fragment_id,
                    "filing_table",
                    [table],
                    page,
                )
            )
    return chunks


def _build_cross_page_table_chunks(
    source: FilingSource,
    pages: list[FilingPage],
    groups: list[dict[str, Any]],
) -> list[FilingChunk]:
    fragments = {table.fragment_id: table for page in pages for table in page.tables}
    chunks: list[FilingChunk] = []
    for record in groups:
        group_id = record["continuation_group_id"]
        if not isinstance(group_id, str):
            raise ChunkValidationError("Continued table is missing its group ID")
        if not record["combined_chunk_created"]:
            continue
        group = [fragments[value] for value in record["table_fragment_ids"]]
        chunks.append(
            _build_table_chunk(
                source,
                group_id,
                "cross_page_table",
                group,
                None,
            )
        )
    return chunks


def build_filing_chunks(
    source: FilingSource,
    overlap_words: int = FILING_CHUNK_OVERLAP_WORDS,
) -> list[FilingChunk]:
    """Build validated page, table, and continuation-group chunks.

    Args:
        source: Frozen filing metadata.
        overlap_words: Words copied from each neighboring page.

    Returns:
        Validated filing chunks.
    """
    pages = parse_filing_pages(source)
    groups = _build_continuation_groups(pages)
    chunks = _build_page_chunks(source, pages, overlap_words)
    chunks.extend(_build_single_page_table_chunks(source, pages))
    chunks.extend(_build_cross_page_table_chunks(source, pages, groups))
    validate_chunks(source, pages, chunks, groups, overlap_words)
    return chunks


def validate_chunks(
    source: FilingSource,
    pages: list[FilingPage],
    chunks: list[FilingChunk],
    groups: list[dict[str, Any]],
    overlap_words: int,
) -> None:
    """Check content, table, continuation, and provenance invariants.

    Args:
        source: Frozen filing metadata.
        pages: Parsed physical pages.
        chunks: Generated evidence chunks.
        groups: Detected continuation groups.
        overlap_words: Configured neighboring-page overlap.

    Raises:
        ChunkValidationError: If any invariant fails.
    """
    page_chunks = [chunk for chunk in chunks if chunk.evidence_type == "filing_page"]
    table_chunks = [chunk for chunk in chunks if chunk.evidence_type == "filing_table"]
    if len(page_chunks) != len(pages):
        raise ChunkValidationError(f"Missing page chunks for {source.ticker}")
    if len(table_chunks) != sum(len(page.tables) for page in pages):
        raise ChunkValidationError(f"Missing table chunks for {source.ticker}")
    evidence_ids = [chunk.evidence_id for chunk in chunks]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ChunkValidationError(f"Duplicate evidence IDs for {source.ticker}")

    for index, chunk in enumerate(page_chunks):
        page = pages[index]
        expected_before = (
            min(overlap_words, len(pages[index - 1].words)) if index else 0
        )
        expected_after = (
            min(overlap_words, len(pages[index + 1].words))
            if index + 1 < len(pages)
            else 0
        )
        if (
            chunk.supporting_page_sequences != [page.sequence]
            or chunk.core_text != page.text
        ):
            raise ChunkValidationError(f"Page lineage mismatch: {chunk.evidence_id}")
        if chunk.overlap_before_word_count != expected_before:
            raise ChunkValidationError(f"Before-overlap mismatch: {chunk.evidence_id}")
        if chunk.overlap_after_word_count != expected_after:
            raise ChunkValidationError(f"After-overlap mismatch: {chunk.evidence_id}")
        if not chunk.core_text:
            raise ChunkValidationError(f"Empty page chunk: {chunk.evidence_id}")

    table_chunk_by_id = {chunk.evidence_id: chunk for chunk in table_chunks}
    for page in pages:
        for table in page.tables:
            chunk = table_chunk_by_id[table.fragment_id]
            if not table.rows or any(
                len(row) != table.column_count for row in table.rows
            ):
                raise ChunkValidationError(f"Invalid table grid: {table.fragment_id}")
            expected_items = [table.item] if table.item else []
            expected_notes = [table.note] if table.note else []
            if chunk.items != expected_items or chunk.notes != expected_notes:
                raise ChunkValidationError(
                    f"Table context mismatch: {table.fragment_id}"
                )
            if chunk.table_periods != table.periods:
                raise ChunkValidationError(
                    f"Table period mismatch: {table.fragment_id}"
                )
    valid_page_ids = {
        page_id for chunk in page_chunks for page_id in chunk.supporting_page_ids
    }
    valid_fragment_ids = set(table_chunk_by_id)
    for group in groups:
        if len(group["supporting_page_sequences"]) < 2:
            raise ChunkValidationError("Continuation group must span multiple pages")
        if not set(group["table_fragment_ids"]).issubset(valid_fragment_ids):
            raise ChunkValidationError("Continuation group references an invalid table")
    for chunk in chunks:
        if not set(chunk.supporting_page_ids).issubset(valid_page_ids):
            raise ChunkValidationError(f"Invalid supporting page: {chunk.evidence_id}")
        checksum = hashlib.sha256(chunk.indexed_text.encode("utf-8")).hexdigest()
        if chunk.content_sha256 != checksum:
            raise ChunkValidationError(
                f"Content checksum mismatch: {chunk.evidence_id}"
            )
        if chunk.source_sha256 != source.sha256:
            raise ChunkValidationError(f"Source checksum mismatch: {chunk.evidence_id}")

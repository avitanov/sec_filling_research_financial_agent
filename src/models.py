"""Define shared typed records for frozen filing parsing and retrieval."""

import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from utils import compact_metadata, normalize_text

FINANCIAL_NUMBER_PATTERN = re.compile(r"(?:\$|\()?[-+]?\d[\d,.]*(?:%|\))?")
PRINTED_PAGE_PATTERN = re.compile(r"(?:^|\s)(\d{1,3})(?:\.|\s|$)")


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


@dataclass(frozen=True)
class FilingSource:
    """Describe one frozen 10-K HTML source."""

    ticker: str
    company_name: str
    cik: str
    report_year: int
    report_date: str
    filing_date: str
    accession_number: str
    primary_document: str
    source_url: str
    local_path: str
    sha256: str


@dataclass
class TableFragment:
    """Store one HTML table fragment found on one physical filing page."""

    fragment_id: str
    page_sequence: int
    # Zero-based word offset where this table begins in its physical page.
    start_word: int
    # Word count after the table ends; used with start_word for page-edge checks.
    end_word: int = 0
    # Reconstructed logical cell grid; empty strings preserve colspan/rowspan gaps.
    rows: list[list[str]] = field(default_factory=list)
    # Most recent useful section heading before the table.
    title: str | None = None
    # Up to 60 same-page words immediately preceding the table.
    context_text: str | None = None
    # SEC Item heading active when this table appears, for example "Item 8".
    item: str | None = None
    # Financial-statement Note heading active when this table appears.
    note: str | None = None
    # Scale wording found near the table, for example "in millions".
    unit_qualifier: str | None = None
    # Explicit units inferred from the table, such as USD or percent.
    units: list[str] = field(default_factory=list)
    # Calendar years found in the table text.
    periods: list[str] = field(default_factory=list)
    # Whether sec-parser or the numeric-text fallback retained this table.
    parser_source: str = "sec-parser"
    # Shared ID for physical table fragments detected as one continued table.
    continuation_group_id: str | None = None

    @property
    def text(self) -> str:
        """Return rows with explicit cell and row separators."""
        return " [ROW] ".join(
            " | ".join(cell or "[BLANK]" for cell in row) for row in self.rows
        )

    @property
    def column_count(self) -> int:
        """Return the width of the reconstructed logical table grid."""
        return max((len(row) for row in self.rows), default=0)

    @property
    def numeric_cell_count(self) -> int:
        """Return the number of cells containing a financial number."""
        return sum(
            bool(FINANCIAL_NUMBER_PATTERN.search(cell))
            for row in self.rows
            for cell in row
        )


@dataclass
class FilingPage:
    """Store normalized content and structure from one physical filing page."""

    sequence: int
    text: str
    words: list[str]
    printed_page: int | None
    tables: list[TableFragment]
    items: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class FilingHTMLParser(HTMLParser):
    """Parse SEC HTML into physical pages and table fragments.

    Args:
        ticker: Ticker used in stable table-fragment IDs.
        report_year: Report year used in stable table-fragment IDs.
    """

    def __init__(self, ticker: str, report_year: int) -> None:
        super().__init__(convert_charrefs=True)
        self.ticker = ticker
        self.report_year = report_year
        self.pages: list[FilingPage] = []
        self._sequence = 1
        self._pieces: list[str] = []
        self._footer_pieces: list[str] = []
        self._tables: list[TableFragment] = []
        self._skip_depth = 0
        self._footer_depth = 0
        self._table_depth = 0
        self._current_table: TableFragment | None = None
        self._current_row: list[str] | None = None
        self._cell_depth = 0
        self._cell_pieces: list[str] = []

    def _word_count(self) -> int:
        return len(normalize_text(" ".join(self._pieces)).split())

    def _finish_cell(self) -> None:
        if self._current_row is None:
            return
        cell = normalize_text(" ".join(self._cell_pieces))
        if cell:
            self._current_row.append(cell)
        self._cell_pieces = []

    def _finish_row(self) -> None:
        if self._current_table is None or self._current_row is None:
            return
        if self._current_row:
            self._current_table.rows.append(self._current_row)
        self._current_row = None

    def _finish_table(self) -> None:
        table = self._current_table
        if table is None:
            return
        if table.rows:
            table.end_word = self._word_count()
            self._tables.append(table)
        self._current_table = None

    def _finish_page(self) -> None:
        if self._current_table is not None:
            self._finish_cell()
            self._finish_row()
            self._finish_table()

        text = normalize_text(" ".join(self._pieces))
        text = re.sub(r"^Table of Contents\s+", "", text, flags=re.IGNORECASE)
        footer = normalize_text(" ".join(self._footer_pieces))
        printed_numbers = PRINTED_PAGE_PATTERN.findall(footer)
        printed_page = int(printed_numbers[-1]) if printed_numbers else None
        self.pages.append(
            FilingPage(
                sequence=self._sequence,
                text=text,
                words=text.split(),
                printed_page=printed_page,
                tables=self._tables,
            )
        )
        self._sequence += 1
        self._pieces = []
        self._footer_pieces = []
        self._tables = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value or "" for name, value in attrs}
        style = attributes.get("style", "").lower().replace(" ", "")

        if self._skip_depth:
            self._skip_depth += 1
            return
        if tag in {"ix:header", "script", "style"} or "display:none" in style:
            self._skip_depth = 1
            return
        if self._footer_depth:
            self._footer_depth += 1
            return
        if tag == "div" and "bottom:0" in style and "position:absolute" in style:
            self._footer_depth = 1
            return
        if tag == "hr" and "page-break-after:always" in style:
            self._finish_page()
            return
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                fragment_id = (
                    f"filing-{self.ticker.lower()}-{self.report_year}-page-"
                    f"{self._sequence:04d}-table-{len(self._tables) + 1:02d}"
                )
                self._current_table = TableFragment(
                    fragment_id=fragment_id,
                    page_sequence=self._sequence,
                    start_word=self._word_count(),
                )
        elif tag == "tr" and self._table_depth == 1:
            self._finish_row()
            self._current_row = []
        elif tag in {"td", "th"} and self._table_depth == 1:
            self._cell_depth += 1
            self._cell_pieces = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if self._footer_depth:
            self._footer_depth -= 1
            return
        if tag in {"td", "th"} and self._table_depth == 1 and self._cell_depth:
            self._finish_cell()
            self._cell_depth -= 1
        elif tag == "tr" and self._table_depth == 1:
            self._finish_row()
        elif tag == "table" and self._table_depth:
            if self._table_depth == 1:
                self._finish_row()
                self._finish_table()
            self._table_depth -= 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        text = normalize_text(data)
        if not text or self._skip_depth:
            return
        if self._footer_depth:
            self._footer_pieces.append(text)
            return
        self._pieces.append(text)
        if self._cell_depth and self._table_depth == 1:
            self._cell_pieces.append(text)

    def close(self) -> None:
        super().close()
        if self._pieces or self._tables or self._footer_pieces:
            self._finish_page()


@dataclass(frozen=True)
class FilingChunk:
    """Store indexed text and rich provenance for one filing evidence chunk."""

    evidence_id: str
    evidence_type: str
    chunk_schema_version: str
    document_type: str
    company: str
    ticker: str
    cik: str
    accession_number: str
    report_year: int
    report_date: str
    filing_date: str
    source_url: str
    source_path: str
    primary_document: str
    source_sha256: str
    parser_version: str
    printed_page: int | None
    supporting_page_ids: list[str]
    # Physical filing pages that supplied this chunk's evidence.
    supporting_page_sequences: list[int]
    section_title: str | None
    table_fragment_ids: list[str]
    continuation_group_id: str | None
    core_text: str
    overlap_before_text: str
    overlap_after_text: str
    indexed_text: str
    overlap_before_page_id: str | None
    overlap_after_page_id: str | None
    core_word_count: int
    overlap_before_word_count: int
    overlap_after_word_count: int
    content_sha256: str
    items: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    table_units: list[str] = field(default_factory=list)
    table_periods: list[str] = field(default_factory=list)
    table_row_count: int | None = None
    table_column_count: int | None = None
    table_parser_source: str | None = None

    def payload(self) -> dict[str, Any]:
        """Return the Qdrant payload without empty metadata fields."""
        return compact_metadata(asdict(self))


@dataclass(frozen=True)
class FilingSearchResult:
    """Return one auditable filing retrieval result."""

    evidence_id: str
    score: float
    text: str
    metadata: dict[str, Any]


class QuestionInput(BaseModel):
    """Store only the benchmark fields safe to expose during a run."""

    question_id: str
    split: str
    ticker: str
    company: str
    category: str
    question: str


class AnswerValue(BaseModel):
    """Represent one value stated in the final answer."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Short name of the reported financial value.")
    value: str = Field(
        description=(
            "Plain decimal without commas, currency symbols, percent signs, or "
            "magnitude words. USD uses absolute digits: for example, 15 million "
            "USD must be returned as 15000000, not 15 or 15 million. Percentages "
            "use exactly two decimal places unless the question requests another "
            "precision."
        )
    )
    unit: str | None = Field(description="Required unit: use USD or percent.")
    period: str | None = Field(
        description="Required fiscal period such as FY2025 or FY2024 to FY2025."
    )
    source_evidence_ids: list[str] = Field(
        description=(
            "Only evidence IDs returned by lookup_fact or search_filing. For a "
            "calculated value, list the evidence IDs for its source inputs; never "
            "invent a calculation ID."
        )
    )


class CalculationRecord(BaseModel):
    """Represent one calculation reported by the agent."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["add", "subtract", "divide", "percentage"]
    inputs: list[str] = Field(
        description="Exact plain-decimal inputs sent to the calculate tool."
    )
    result: str = Field(
        description="Exact plain-decimal result returned by the calculate tool."
    )


class ResearchFact(BaseModel):
    """Represent one financial value selected by the research stage."""

    model_config = ConfigDict(extra="forbid")

    label: str
    value: str
    unit: str | None
    period: str | None
    evidence_id: str


class ResearchSubmission(BaseModel):
    """Define the evidence package passed from research to the math stage."""

    model_config = ConfigDict(extra="forbid")

    facts: list[ResearchFact]
    evidence_ids: list[str]
    calculation_request: str = ""


class MathSubmission(BaseModel):
    """Define the calculator output passed from the math stage to the verifier."""

    model_config = ConfigDict(extra="forbid")

    calculations: list[CalculationRecord]


class MonolithicAnswer(BaseModel):
    """Define the structured output returned by the monolithic agent."""

    model_config = ConfigDict(extra="forbid")

    answer_text: str
    values: list[AnswerValue] = Field(
        description=(
            "Only final values requested by the question. Do not repeat raw source "
            "inputs or intermediate calculator values here."
        )
    )
    evidence_ids: list[str] = Field(
        description="All cited evidence IDs actually returned by retrieval tools."
    )
    calculations: list[CalculationRecord]
    conclusions: list[str] = Field(
        default_factory=list,
        description=(
            "Short factual conclusions stated in answer_text, or an empty list."
        ),
    )


class VerifierSubmission(BaseModel):
    """Define the verifier decision and its constrained final answer."""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    rejection_reason: str | None = None
    answer: MonolithicAnswer | None = None
    retry_stage: Literal["math", "none"] = "none"
    feedback: str | None = None

    @model_validator(mode="after")
    def _require_consistent_decision(self) -> "VerifierSubmission":
        if self.approved and self.answer is None:
            raise ValueError("An approved verification requires a final answer")
        if self.approved and self.retry_stage != "none":
            raise ValueError("An approved verification cannot request a repair")
        if not self.approved and not self.rejection_reason:
            raise ValueError("A rejected verification requires a reason")
        if not self.approved and self.answer is not None:
            raise ValueError("A rejected verification cannot contain an answer")
        if not self.approved and self.retry_stage != "none" and not self.feedback:
            raise ValueError("A repair request requires feedback")
        return self


class ToolEvent(BaseModel):
    """Record one canonical project-tool invocation."""

    sequence: int
    name: Literal["search_filing", "lookup_fact", "calculate"]
    arguments: dict[str, Any]
    success: bool
    result: Any = None
    error: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    latency_ms: float
    agent_stage: Literal["research", "math", "verifier"] | None = None
    agent_attempt: int | None = None


class ModelCallEvent(BaseModel):
    """Record token usage reported for one agent model cycle."""

    sequence: int
    model: str
    token_usage: dict[str, int] = Field(default_factory=dict)
    agent_stage: Literal["research", "math", "verifier"] | None = None
    agent_attempt: int | None = None


class JudgeScores(BaseModel):
    """Store qualitative evaluation scores returned by the LLM judge."""

    model_config = ConfigDict(extra="forbid")

    llm_answer_correctness_grade: int = Field(ge=1, le=5)
    workflow_correctness: int = Field(ge=1, le=5)
    chronological_coherence: int = Field(ge=1, le=5)
    workflow_efficiency: int = Field(ge=1, le=5)
    auditability: int = Field(ge=1, le=5)
    explanation: str


class RunRecord(BaseModel):
    """Store the complete observable record for one agent-system run."""

    run_id: str
    started_at: str
    question: QuestionInput
    architecture: Literal["monolithic", "decomposed"] = "monolithic"
    pipeline_variant: (
        Literal[
            "verification_with_repair",
            "verification_without_repair",
            "no_verification",
        ]
        | None
    ) = None
    provider: Literal["openrouter", "bedrock"]
    model: str
    reasoning_mode: str
    status: Literal["success", "failure"]
    answer: MonolithicAnswer | None = None
    accepted_math_attempt: int | None = Field(default=None, ge=1)
    model_calls: list[ModelCallEvent] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    observable_timeline: list[dict[str, Any]] = Field(default_factory=list)
    retry_events: list[dict[str, Any]] = Field(default_factory=list)
    failure_category: str | None = None
    failure_message: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    pricing_usd_per_million_tokens: dict[str, str] = Field(default_factory=dict)
    cost_usd: dict[str, str] = Field(default_factory=dict)
    latency_ms: float
    retries: int = 0
    structured_output_repairs: int = 0
    langfuse_trace_id: str | None = None
    langfuse_trace_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_legacy_answer_fields(cls, data: Any) -> Any:
        """Fill fields absent from persisted runs created before audit upgrades."""
        if not isinstance(data, dict) or not isinstance(data.get("answer"), dict):
            return data
        answer = data["answer"]
        values = answer.get("values")
        if not isinstance(values, list):
            return data
        evidence_ids = answer.get("evidence_ids", [])
        updated_values = []
        for value in values:
            if not isinstance(value, dict):
                updated_values.append(value)
                continue
            updated_values.append(
                {
                    **value,
                    "source_evidence_ids": value.get(
                        "source_evidence_ids", evidence_ids
                    ),
                }
            )
        return {
            **data,
            "answer": {
                **answer,
                "values": updated_values,
                "conclusions": answer.get("conclusions", []),
            },
        }

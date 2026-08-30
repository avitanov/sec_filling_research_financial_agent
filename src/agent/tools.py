"""Implement the three canonical tools used by the monolithic agent."""

from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation
from time import perf_counter
from typing import Any, Literal

from strands import tool

from agent.data import load_company_facts
from models import ToolEvent
from retrieval import search_filing as search_filing_records
from utils import format_decimal_result_to_string

FACT_RESULT_FIELDS = (
    "company",
    "ticker",
    "concept",
    "evidence_id",
    "value",
    "unit",
    "period",
)
SEARCH_METADATA_FIELDS = (
    "evidence_type",
    "report_year",
    "printed_page",
    "section_title",
    "table_periods",
    "table_units",
    "supporting_page_ids",
)


def _selected_fields(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: record[field] for field in fields if record.get(field) is not None}


class ProjectTools:
    """Provide bounded SEC retrieval, fact lookup, and arithmetic tools."""

    def __init__(self) -> None:
        self._events: list[ToolEvent] = []

    def _record(
        self,
        name: Literal["search_filing", "lookup_fact", "calculate"],
        arguments: dict[str, Any],
        started_at: float,
        result: Any = None,
        error: str | None = None,
        evidence_ids: list[str] | None = None,
    ) -> None:
        self._events.append(
            ToolEvent(
                sequence=len(self._events) + 1,
                name=name,
                arguments=arguments,
                success=error is None,
                result=result,
                error=error,
                evidence_ids=evidence_ids or [],
                latency_ms=round((perf_counter() - started_at) * 1000, 2),
            )
        )

    def get_events(self) -> list[ToolEvent]:
        """Return a copy of the canonical tool events.

        Returns:
            Events in invocation order.
        """
        return list(self._events)

    def reset_events(self) -> None:
        """Clear recorded events before a new isolated question run."""
        self._events.clear()

    @tool
    def search_filing(self, query: str, company: str) -> dict[str, Any]:
        """Search the frozen 10-K for relevant evidence.

        Args:
            query: Specific financial topic or phrase to find.
            company: Company ticker. It must match the question company.

        Returns:
            Up to five ranked filing evidence records.
        """
        started_at = perf_counter()
        arguments = {"query": query, "company": company}
        try:
            records = search_filing_records(query=query, company=company, limit=5)
            result = {
                "status": "ok",
                "records": [
                    {
                        "evidence_id": record.evidence_id,
                        "score": record.score,
                        "text": record.text,
                        "metadata": _selected_fields(
                            record.metadata, SEARCH_METADATA_FIELDS
                        ),
                    }
                    for record in records
                ],
            }
            evidence_ids = [record.evidence_id for record in records]
            self._record(
                "search_filing",
                arguments,
                started_at,
                result,
                evidence_ids=evidence_ids,
            )
            return result
        except Exception as error:
            message = str(error)
            result = {"status": "error", "error": message}
            self._record("search_filing", arguments, started_at, result, message)
            return result

    @tool
    def lookup_fact(
        self,
        company: str,
        concept: Literal[
            "revenue",
            "operating_income",
            "net_income",
            "total_assets",
            "operating_cash_flow",
        ],
    ) -> dict[str, Any]:
        """Look up all normalized Company Facts observations for one concept.

        Args:
            company: Company ticker. It must match the question company.
            concept: One normalized concept. Revenue means consolidated net sales.

        Returns:
            All matching frozen facts, or an explicit missing result.
        """
        started_at = perf_counter()
        arguments = {"company": company, "concept": concept}
        try:
            facts = load_company_facts(company)
            matches = [
                fact
                for fact in facts
                if fact["concept"].lower() == concept.lower().strip()
            ]
            if len(matches) == 0:
                raise ValueError("No matching frozen facts")
            result = {
                "status": "ok",
                "facts": [
                    _selected_fields(fact, FACT_RESULT_FIELDS) for fact in matches
                ],
            }
            evidence_ids = [str(fact["evidence_id"]) for fact in matches]
            self._record(
                "lookup_fact", arguments, started_at, result, evidence_ids=evidence_ids
            )
            return result
        except Exception as error:
            message = str(error)
            result = {"status": "error", "error": message}
            self._record("lookup_fact", arguments, started_at, result, message)
            return result

    @tool
    def calculate(
        self,
        operation: Literal["add", "subtract", "divide", "percentage"],
        inputs: list[str],
    ) -> dict[str, Any]:
        """Perform one allowed deterministic Decimal calculation.

        Args:
            operation: Add all inputs, or apply subtract, divide, or percentage
                to two inputs in order. Percentage means first / second * 100.
            inputs: Decimal numbers as strings, in calculation order.

        Returns:
            Exact Decimal result as a string, or an explicit error.
        """
        started_at = perf_counter()
        arguments = {"operation": operation, "inputs": inputs}
        try:
            values = [Decimal(value.replace(",", "")) for value in inputs]
            if operation == "add" and values:
                answer = sum(values, Decimal("0"))
            elif operation == "subtract" and len(values) == 2:
                answer = values[0] - values[1]
            elif operation == "divide" and len(values) == 2:
                answer = values[0] / values[1]
            elif operation == "percentage" and len(values) == 2:
                answer = values[0] / values[1] * Decimal("100")
            else:
                raise ValueError("Invalid operation or number of inputs")
            result = {"status": "ok", "result": format_decimal_result_to_string(answer)}
            self._record("calculate", arguments, started_at, result)
            return result
        except (DivisionByZero, InvalidOperation, ValueError) as error:
            message = str(error) or "Invalid decimal calculation"
            result = {"status": "error", "error": message}
            self._record("calculate", arguments, started_at, result, message)
            return result

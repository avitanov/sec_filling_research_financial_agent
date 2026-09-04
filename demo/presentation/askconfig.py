"""Ask-page configuration: the recommended questions the page offers.

The Ask page is one generic chat, so no company is chosen in the UI and none is
named here: which companies exist comes from the frozen data via
``demo.routing.catalog``, and which one a question is about is resolved by the
router at ask time.
"""

from __future__ import annotations

from agent.data import load_company_facts, load_questions
from demo.routing import short_names

from .labels import category_label

# Recommended questions for the Ask page. Each one deliberately sits in a gap in
# the frozen benchmark - a period, ratio, or aggregate the benchmark never asks
# for - so clicking one always runs the pipeline for real rather than replaying a
# saved run. They cover three companies and three question types, and `requires`
# lists the normalized facts each one needs, because coverage is not uniform: the
# frozen balance sheets carry only two years of total assets.
RECOMMENDED_QUESTIONS = (
    (
        "KO",
        "single_formula",
        "What was Coca-Cola's net income as a percentage of total assets in "
        "fiscal 2025?",
        (("net_income", "FY2025"), ("total_assets", "FY2025")),
    ),
    (
        "GOOGL",
        "period_comparison",
        "How did Alphabet's operating income change between fiscal 2023 and "
        "fiscal 2024 in dollar and percentage terms?",
        (("operating_income", "FY2023"), ("operating_income", "FY2024")),
    ),
    (
        "AMZN",
        "multi_step",
        "What was Amazon's average annual net income for fiscal 2023 through "
        "fiscal 2025, and how did fiscal 2025 compare with that average?",
        (
            ("net_income", "FY2023"),
            ("net_income", "FY2024"),
            ("net_income", "FY2025"),
        ),
    ),
)


def company_choices() -> list[dict[str, str]]:
    """List the companies the frozen data can answer questions about."""
    return [{"ticker": ticker, "name": name} for ticker, name in short_names().items()]


def recommendations() -> list[dict[str, str]]:
    """Build the Ask page's recommended questions.

    Returns:
        One recommendation per company, in presentation order.

    Raises:
        ValueError: If a recommendation matches a frozen benchmark question,
            which would score a demo run as an experiment, or if the frozen data
            cannot answer it.
    """
    frozen = {question.question for question in load_questions()}
    built: list[dict[str, str]] = []
    for ticker, category, question, required in RECOMMENDED_QUESTIONS:
        if question in frozen:
            raise ValueError(
                f"Recommended question is already in the benchmark: {question!r}"
            )
        available = {
            (fact["concept"], fact["period"]) for fact in load_company_facts(ticker)
        }
        missing = [fact for fact in required if fact not in available]
        if missing:
            raise ValueError(
                f"Frozen data for {ticker} cannot answer {question!r}; "
                f"missing {missing}"
            )
        if short_names()[ticker].lower() not in question.lower():
            raise ValueError(
                f"Recommended question does not name its company: {question!r}"
            )
        built.append(
            {
                "category": category,
                "category_label": category_label(category),
                "company": short_names()[ticker],
                "question": question,
                "ticker": ticker,
            }
        )
    return built

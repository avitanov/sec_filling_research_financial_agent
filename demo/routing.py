"""Resolve which company a typed question is about by asking the model.

The Ask page is one generic chat, so every turn has to decide which frozen filing
to open. That decision belongs to the model, not to a keyword list: the router is
a small agent whose only tool, ``list_available_data``, reports the companies the
frozen dataset actually holds. Nothing in this module names a company, a ticker,
or a brand, so adding a filing to the dataset widens what the demo answers
without an edit here.

It runs on every turn, not only the first, so a thread can re-scope: "and how does
the maker of Sprite compare?" moves to Coca-Cola even though that brand appears
nowhere in the data, while "and the year before?" stays on the company the
conversation is already about. When a message names no company at all, the same
call writes the conversational reply, so a greeting costs one round-trip.

A message that names no company and a router whose model call itself failed (no
AWS Bedrock connection, an expired credential, a throttle) look the same from the
caller's side unless kept apart: both leave ``decision`` unset. They are not the
same thing and must not be reported the same way, since the model call failing
means every stage after it will fail too. ``route`` raises ``RouterUnavailable``
for the latter so the caller can show that plainly instead of a "which company?"
reply that would only be misleading.

This is demo-side only: the harness still receives an explicit ticker, and a
frozen benchmark run never reaches this module.
"""

from __future__ import annotations

import re
from functools import lru_cache
from threading import Lock
from time import perf_counter
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field
from strands import Agent, tool

from agent.data import load_company_facts
from agent.plugins import ProgressPlugin
from agent.providers import build_model
from config import (
    AWS_REGION,
    BEDROCK_GUARDRAIL_CONFIG,
    BEDROCK_MANTLE_TIMEOUT_SECONDS,
    BEDROCK_SONNET_MODEL_ID,
)
from retrieval.chunking import load_filing_sources

# The stage the routing step reports under, so its tool call and decision land in
# the same audit trail as the pipeline stages that follow it.
STAGE = "routing"
# Routing is one tool call and one short decision: a greeting must not cost what
# a filing question costs.
ROUTER_MAX_TOKENS = 800
ROUTER_CONTEXT_TURNS = 6

_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(inc|corp|corporation|co|company|ltd|plc|holdings)\.?$", re.IGNORECASE
)
_route_lock = Lock()

ROUTER_PROMPT = """You route questions for a demo that answers questions about
frozen U.S. SEC annual filings (10-K). Every financial figure the demo reports is
read out of those filings and shown with its evidence, so never state, estimate,
or guess a financial number yourself.

Your only decision is which company, if any, the latest message is about. You are
never the one who decides which years, periods, or figures a filing covers - a
10-K's numbers span more years than the one it was filed for, and only the
pipeline that actually reads the filing can tell what it holds. Never write a
reply that claims a period, year, or figure is or is not covered; that is not
information you have.

Call list_available_data first, on every question. It is the only source of truth
for which companies exist here; do not assume a company is covered because it is
well known. It tells you only which companies exist, nothing about what their
filings contain.

Then decide about the latest user message:

- If it is about one of the available companies, set ticker to that company's
  ticker exactly as the tool reported it, and leave reply empty - regardless of
  which period, year, or figure it asks about, even one that looks unlikely to be
  covered. Resolve the company however the message refers to it: a ticker, a
  legal or informal name, a brand, product, subsidiary, or a description such as
  "the search giant" or "the maker of Sprite". If it names more than one, choose
  the one it primarily asks about, because the pipeline answers about one filing
  per turn.
- If it names no company but continues a conversation already about one - "and
  the year before?", "what about 2024?" - set ticker to that same company. Do
  this even if the new period looks out of range; let the pipeline decide what
  the filing actually holds.
- Only when the message and the conversation so far name no company at all, set
  ticker to null and write reply. Answer greetings, what this demo does, and
  which companies are covered directly, in two to four warm, plain sentences. If
  a figure is asked for with no company in view, name the available companies and
  ask which one is meant; never invent a figure. Plain text only.
"""


class RouterUnavailable(RuntimeError):
    """Raised when the router's own model call failed, not just its decision."""


class RoutingDecision(BaseModel):
    """Report the company one message is about, or the reply to send instead."""

    model_config = ConfigDict(extra="forbid")

    ticker: str | None = Field(
        default=None,
        description=(
            "Ticker of the available company this message is about, exactly as "
            "list_available_data reported it, or null when it is about none."
        ),
    )
    reply: str | None = Field(
        default=None,
        description=(
            "Short plain-text answer, set only when ticker is null, and never "
            "containing a financial figure."
        ),
    )


def _short_name(legal_name: str) -> str:
    """Shorten one legal company name to the name people actually use."""
    name = re.sub(r"^the\s+", "", legal_name.strip(), flags=re.IGNORECASE)
    while True:
        trimmed = _LEGAL_SUFFIX.sub("", name).strip()
        if trimmed == name:
            break
        name = trimmed
    return re.sub(r"\.com$", "", name, flags=re.IGNORECASE).strip() or legal_name


@lru_cache(maxsize=1)
def catalog() -> tuple[dict[str, Any], ...]:
    """Describe every company the frozen dataset can answer questions about.

    Built from the frozen filing manifest and the normalized company facts, so the
    demo carries no list of companies of its own.

    Returns:
        One entry per company, in ticker order.
    """
    tickers = {source.ticker for source in load_filing_sources()}
    entries: list[dict[str, Any]] = []
    for ticker in sorted(tickers):
        facts = load_company_facts(ticker)
        if not facts:
            continue
        legal_name = str(facts[0]["company"])
        entries.append(
            {
                "ticker": ticker,
                "company": legal_name,
                "common_name": _short_name(legal_name),
            }
        )
    return tuple(entries)


@lru_cache(maxsize=1)
def short_names() -> dict[str, str]:
    """Map each covered ticker to the name the demo shows for it."""
    return {entry["ticker"]: entry["common_name"] for entry in catalog()}


def fallback_reply() -> str:
    """Compose the reply used when the router itself cannot be reached."""
    names = list(short_names().values())
    listed = " and ".join(filter(None, [", ".join(names[:-1]), names[-1]]))
    return (
        f"I can answer questions about the SEC filings of {listed} — for example, "
        f"“What was {names[0]}'s net income in fiscal 2024?” "
        "Which of them would you like to ask about?"
    )


class RouterTools:
    """Expose the covered companies, and only their identity, as the router's tool.

    Deliberately omits ``catalog()``'s report years and concepts: the router's one
    job is naming the company, and handing it coverage details invited it to
    decide from that alone whether a period or figure exists - a judgment that
    belongs to the pipeline that actually reads the filing, not to routing.
    """

    @tool
    def list_available_data(self) -> dict[str, Any]:
        """List every company this pipeline holds a frozen SEC filing for.

        Returns:
            One entry per company: its ticker, legal name, and common name.
        """
        return {
            "companies": [
                {
                    "ticker": entry["ticker"],
                    "company": entry["company"],
                    "common_name": entry["common_name"],
                }
                for entry in catalog()
            ]
        }


_TOOLS = RouterTools()


@lru_cache(maxsize=1)
def _router_model() -> Any:
    """Build the router's model once; the agent around it is per call."""
    return build_model(
        provider="bedrock",
        model_id=BEDROCK_SONNET_MODEL_ID,
        region_name=AWS_REGION,
        timeout_seconds=BEDROCK_MANTLE_TIMEOUT_SECONDS,
        max_tokens=ROUTER_MAX_TOKENS,
        guardrail_config=BEDROCK_GUARDRAIL_CONFIG,
    )


def _router_agent(on_event: Callable[[dict[str, Any]], None] | None) -> Agent:
    """Build one router agent, reporting its tool calls to this turn's listener."""
    progress = ProgressPlugin(on_event) if on_event is not None else None
    if progress is not None:
        progress.set_stage(STAGE)
    return Agent(
        model=_router_model(),
        system_prompt=ROUTER_PROMPT,
        tools=[_TOOLS.list_available_data],
        callback_handler=None,
        name="demo-company-router",
        plugins=[progress] if progress is not None else [],
        structured_output_model=RoutingDecision,
        trace_attributes={"architecture": "demo", "stage": STAGE},
    )


def _prior_messages(history: list[dict[str, str]] | None) -> list[dict[str, Any]]:
    """Turn recent answered turns into the messages the router reasons over."""
    messages: list[dict[str, Any]] = []
    for turn in (history or [])[-ROUTER_CONTEXT_TURNS:]:
        for role, text in (
            ("user", turn.get("question")),
            ("assistant", turn.get("answer")),
        ):
            if (text or "").strip():
                messages.append({"role": role, "content": [{"text": text.strip()}]})
    return messages


def _prompt(question: str, current_ticker: str | None) -> str:
    """Frame the message to route, saying what the thread is currently about.

    The current company is context the router may override, not a default it must
    keep, so a message naming another company re-scopes the thread.
    """
    if not current_ticker:
        return f"Latest message: {question}"
    return f"Conversation so far is about: {current_ticker}\nLatest message: {question}"


def route(
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    current_ticker: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str | None, str | None]:
    """Decide which company one message is about, or what to reply instead.

    Args:
        question: The latest message typed on the Ask page.
        history: Earlier answered turns, oldest first, as ``{"question", "answer"}``
            pairs, so a follow-up that names no company can inherit one.
        current_ticker: Company the conversation is currently about, offered as
            context the router may override.
        on_event: Optional listener receiving the same progress events the
            pipeline stages emit, so routing appears in the audit trail.

    Returns:
        ``(ticker, None)`` when a covered company is resolved, or ``(None, reply)``
        when none is and the router's own call succeeded. Keeps the company the
        conversation is already about, or replies plainly.

    Raises:
        RouterUnavailable: If the router's model call itself failed (rather than
            simply naming no company), so the caller can report that plainly
            instead of a "which company?" reply that would misrepresent it.
    """
    covered = short_names()
    agent = _router_agent(on_event)
    agent.messages = _prior_messages(history)
    decision: RoutingDecision | None = None
    error: Exception | None = None
    if on_event is not None:
        on_event({"type": "stage_started", "stage": STAGE, "attempt": 1})
    timer = perf_counter()
    try:
        # The model client is shared, so route one turn at a time.
        with _route_lock:
            result = agent(_prompt(question, current_ticker))
        if isinstance(result.structured_output, RoutingDecision):
            decision = result.structured_output
    except Exception as caught:
        error = caught
    if on_event is not None:
        on_event(
            {
                "type": "stage_finished",
                "stage": STAGE,
                "attempt": 1,
                "duration_ms": round((perf_counter() - timer) * 1000, 2),
                "error": str(error) if error else None,
            }
        )
    if error is not None:
        raise RouterUnavailable("The router's model call failed") from error
    if decision is None:
        if current_ticker in covered:
            return current_ticker, None
        return None, fallback_reply()
    # Hold the router to a ticker the tool reported, so an unrecognised value
    # becomes a reply rather than a failed lookup.
    ticker = (decision.ticker or "").strip().upper() or None
    if ticker not in covered:
        return None, (decision.reply or "").strip() or fallback_reply()
    return ticker, None

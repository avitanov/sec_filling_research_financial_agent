"""Handle model-invocation events and persist cumulative model usage."""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import MODEL_TOKEN_PRICES, MODEL_USAGE_DIR


def _usage_value(token_usage: dict[str, Any], *names: str) -> int:
    for name in names:
        value = token_usage.get(name)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _reasoning_tokens(token_usage: dict[str, Any]) -> int:
    direct = _usage_value(token_usage, "reasoningTokens", "reasoning_tokens")
    if direct:
        return direct
    output_details = token_usage.get("output_tokens_details")
    if isinstance(output_details, dict):
        return _usage_value(output_details, "reasoning_tokens")
    return 0


def _usage_path(model_id: str) -> Path:
    model_name = re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")
    return MODEL_USAGE_DIR / model_name / "usage.json"


def _empty_usage(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "total_invocations": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_reasoning_tokens": 0,
        "usd_spent_input": None,
        "usd_spent_output": None,
        "usd_spent_total": None,
        "updated_at": None,
    }


def _read_usage(path: Path, model_id: str) -> dict[str, Any]:
    if not path.exists():
        return _empty_usage(model_id)
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _cost(amount: int, price: Decimal) -> Decimal:
    return Decimal(amount) * price / Decimal("1000000")


def model_cost(
    token_usage: dict[str, int],
    input_usd_per_million_tokens: Decimal | None,
    output_usd_per_million_tokens: Decimal | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Calculate one invocation's token prices and cost.

    Args:
        token_usage: Provider-reported token totals.
        input_usd_per_million_tokens: Configured input-token price.
        output_usd_per_million_tokens: Configured output-token price.

    Returns:
        Pricing metadata and calculated input, output, and total cost.
    """
    if input_usd_per_million_tokens is None or output_usd_per_million_tokens is None:
        return {}, {}
    input_cost = _cost(token_usage.get("inputTokens", 0), input_usd_per_million_tokens)
    output_cost = _cost(
        token_usage.get("outputTokens", 0), output_usd_per_million_tokens
    )
    return (
        {
            "input": str(input_usd_per_million_tokens),
            "output": str(output_usd_per_million_tokens),
        },
        {
            "input": str(input_cost),
            "output": str(output_cost),
            "total": str(input_cost + output_cost),
        },
    )


def handle_model_invocation(
    model_id: str, token_usage: dict[str, Any]
) -> dict[str, Any]:
    """Handle one completed model invocation.

    Args:
        model_id: Provider model identifier used for the invocation.
        token_usage: Provider token-usage mapping returned for the invocation.

    Returns:
        Updated cumulative usage record.
    """
    path = _usage_path(model_id)
    usage = _read_usage(path, model_id)
    input_tokens = _usage_value(token_usage, "inputTokens", "input_tokens")
    output_tokens = _usage_value(token_usage, "outputTokens", "output_tokens")
    reasoning_tokens = _reasoning_tokens(token_usage)
    usage["total_invocations"] += 1
    usage["total_input_tokens"] += input_tokens
    usage["total_output_tokens"] += output_tokens
    usage["total_reasoning_tokens"] += reasoning_tokens

    prices = MODEL_TOKEN_PRICES.get(model_id)
    if prices is not None:
        input_price, output_price = prices
        previous_input = Decimal(str(usage["usd_spent_input"] or "0"))
        previous_output = Decimal(str(usage["usd_spent_output"] or "0"))
        total_input = previous_input + _cost(input_tokens, input_price)
        total_output = previous_output + _cost(output_tokens, output_price)
        usage["usd_spent_input"] = str(total_input)
        usage["usd_spent_output"] = str(total_output)
        usage["usd_spent_total"] = str(total_input + total_output)
    usage["updated_at"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
    return usage

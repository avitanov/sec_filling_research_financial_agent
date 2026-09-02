"""Define the Bedrock guardrail for the read-only SEC research domain."""

from typing import Any

GUARDRAIL_NAME = "auditable-sec-research-read-only"
GUARDRAIL_TAGS = [{"key": "Project", "value": "auditable-sec-filing-research"}]
GUARDRAIL_DESCRIPTION = (
    "Allows factual SEC research while blocking investment recommendations and "
    "market transactions."
)
GUARDRAIL_BLOCKED_MESSAGE = (
    "I can only help with factual, read-only SEC filing research."
)


def guardrail_policy() -> dict[str, Any]:
    """Return the AWS Bedrock policy used by this project."""
    return {
        "name": GUARDRAIL_NAME,
        "description": GUARDRAIL_DESCRIPTION,
        "blockedInputMessaging": GUARDRAIL_BLOCKED_MESSAGE,
        "blockedOutputsMessaging": GUARDRAIL_BLOCKED_MESSAGE,
        "topicPolicyConfig": {
            "topicsConfig": [
                {
                    "name": "Personalized Investment Advice",
                    "definition": (
                        "Recommendations telling a person whether, when, or how "
                        "much to buy, sell, hold, short, or allocate to securities "
                        "or investments for financial gain."
                    ),
                    "examples": [
                        "Should I buy Coca-Cola stock today?",
                        "Tell me which stock to sell from my portfolio.",
                        "How much should I invest in Amazon shares?",
                        "Give me a buy, sell, or hold recommendation.",
                        "Build me a stock portfolio for maximum returns.",
                    ],
                    "type": "DENY",
                    "inputAction": "BLOCK",
                    "outputAction": "BLOCK",
                    "inputEnabled": True,
                    "outputEnabled": True,
                },
                {
                    "name": "Trade Execution and Market Actions",
                    "definition": (
                        "Requests to execute, place, schedule, automate, or "
                        "facilitate orders or transactions involving stocks, "
                        "securities, derivatives, or investments."
                    ),
                    "examples": [
                        "Buy 100 shares of KO for me.",
                        "Place a limit order for Coca-Cola stock.",
                        "Sell all the Amazon shares in my account.",
                        "Connect to my broker and execute this trade.",
                        "Automatically trade this stock when the price drops.",
                    ],
                    "type": "DENY",
                    "inputAction": "BLOCK",
                    "outputAction": "BLOCK",
                    "inputEnabled": True,
                    "outputEnabled": True,
                },
            ]
        },
        "contentPolicyConfig": {
            "filtersConfig": [
                {
                    "type": "PROMPT_ATTACK",
                    "inputStrength": "HIGH",
                    "outputStrength": "NONE",
                    "inputAction": "BLOCK",
                    "outputAction": "NONE",
                    "inputEnabled": True,
                    "outputEnabled": False,
                }
            ]
        },
    }

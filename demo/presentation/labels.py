"""Presentation vocabulary: the product names and readable prose the demo shows.

Saved records use the study's internal vocabulary. The demo is shown to people
outside the project, so presentation text is translated on the way out. The
stored records themselves are never modified.
"""

from __future__ import annotations

import re

# The demo shows product names, never provider model or pipeline identifiers.
MODEL_LABELS = {
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": "Claude Sonnet 4.5",
    "us.anthropic.claude-sonnet-4-5-20251001-v1:0": "Claude Sonnet 4.5",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": "Claude Haiku 4.5",
    "amazon.nova-lite-v1:0": "Amazon Nova Lite",
    "openai.gpt-5.6-luna": "GPT-5.6 Luna",
}
PIPELINE_LABELS = {
    "verification_with_repair": "Verification-first pipeline",
    "verification_without_repair": "Verification-first pipeline, no repair",
    "no_verification": "Research and calculation only",
}
CATEGORY_LABELS = {
    "direct_extraction": "Direct extraction",
    "single_formula": "Single formula",
    "period_comparison": "Period comparison",
    "multi_step": "Multi-step calculation",
    "adhoc": "Custom question",
}
STRUCTURED_OUTPUT_NAMES = {
    "RoutingDecision": "picks the company to answer about",
    "ResearchSubmission": "submits the collected evidence",
    "MathSubmission": "submits the calculation plan",
    "VerifierSubmission": "submits the verification decision",
    "MonolithicAnswer": "submits the final answer",
}
STAGE_AGENTS = {
    "routing": "Router",
    "research": "Research Agent",
    "math": "Math Agent",
    "verifier": "Verifier",
}
CHECK_LABELS = {
    "numerical_correct": "Numerical correctness",
    "evidence_valid": "Evidence validity",
    "evidence_complete": "Evidence completeness",
    "value_evidence_valid": "Per-value evidence",
    "periods_valid": "Period validity",
    "formula_correct": "Formula correctness",
    "formatting_correct": "Answer formatting",
    "audit_trail_complete": "Audit-trail completeness",
}
CHECK_HINTS = {
    "numerical_correct": "Every gold value must be reported, and nothing extra.",
    "evidence_valid": "Cited evidence must come from a successful tool call.",
    "evidence_complete": "Every reported value must cite the evidence it came from.",
    "value_evidence_valid": "A value may only cite evidence that was returned to it.",
    "periods_valid": "A value's fiscal periods must match its evidence.",
    "formula_correct": "The accepted calculator calls must reproduce the gold formula.",
    "formatting_correct": "The answer text must print each structured value as a "
    "plain number, with no magnitude words such as 'billion'.",
    "audit_trail_complete": "Answer, evidence, periods, and calculations must all "
    "hold together.",
}
# The checks notebook 04 uses to decide whether a question needs review.
REVIEWED_CHECKS = (
    "numerical_correct",
    "evidence_valid",
    "evidence_complete",
    "value_evidence_valid",
    "periods_valid",
    "formatting_correct",
    "audit_trail_complete",
)
CHECK_DETAILS = {
    "numerical_correct": ("numerical_issues", "unexpected_values"),
    "evidence_valid": ("invalid_evidence_ids",),
    "evidence_complete": ("missing_evidence_ids",),
    "value_evidence_valid": ("invalid_value_evidence",),
    "formatting_correct": ("unsupported_values",),
    "audit_trail_complete": ("unsupported_values",),
}

# Rewrite the study's internal prose in the language the demo presents.
PRESENTATION_TERMS = (
    (re.compile(r"\(\s*calc-[a-z]{2,6}-\d+-\d+\s*equivalent\s*\)"), ""),
    (re.compile(r"\bcalc-[a-z]{2,6}-\d+-\d+\b"), "the reference calculation"),
    (
        re.compile(
            r"\bgold (answer|value|values|requirement|requirements|"
            r"specification|specifications|output schema|schema)\b"
        ),
        lambda match: f"reference {match.group(1)}",
    ),
    (re.compile(r"\bthe gold\b"), "the reference"),
    (
        re.compile(r"\bdecomposed (workflow|pipeline|architecture)\b"),
        lambda match: f"staged {match.group(1)}",
    ),
    (
        re.compile(r"\bmonolithic (workflow|pipeline|architecture|agent)\b"),
        lambda match: f"single-agent {match.group(1)}",
    ),
    (re.compile(r"\bMath Agent\b"), "Calculation agent"),
    (re.compile(r"\bmath stage\b"), "calculation stage"),
    (re.compile(r"\bResearch Agent\b"), "Research agent"),
    (
        re.compile(
            r"\b(numerical_correct|evidence_valid|evidence_complete|"
            r"value_evidence_valid|periods_valid|formula_correct|"
            r"formatting_correct|audit_trail_complete|unexpected_values|"
            r"numerical_issues|unsupported_values)\b"
        ),
        lambda match: match.group(1).replace("_", " "),
    ),
)


def presentable(text: str | None) -> str | None:
    """Rewrite stored evaluation prose in the language the demo presents.

    Args:
        text: Saved reviewer or curator prose, or None.

    Returns:
        The same text with internal vocabulary and record identifiers replaced.
    """
    if not text:
        return text
    for pattern, replacement in PRESENTATION_TERMS:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s{2,}", " ", text).replace(" .", ".").strip()


def model_label(model_id: str | None) -> str:
    """Return the product name for one provider model identifier."""
    return MODEL_LABELS.get(model_id or "", "the configured model")


def pipeline_label(variant: str | None) -> str:
    """Return the readable name of one pipeline variant."""
    return PIPELINE_LABELS.get(variant or "", "Research pipeline")


def category_label(category: str | None) -> str:
    """Return the readable name of one question type."""
    return CATEGORY_LABELS.get(category or "", "Question")

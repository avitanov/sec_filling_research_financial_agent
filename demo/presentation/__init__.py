"""Build presentation data for saved runs of the selected architecture.

The public surface is unchanged: import names straight from ``demo.presentation``.
The implementation is split into logical units — presentation vocabulary
(``labels``), the Ask-page configuration (``askconfig``), saved-data loading
(``store``), timeline building (``timeline``), the evaluation panel
(``evaluation``), and the replay stream (``replay``).
"""

from __future__ import annotations

from .askconfig import (
    RECOMMENDED_QUESTIONS,
    company_choices,
    recommendations,
)
from .evaluation import (
    answer_issues,
    answer_payload,
    evaluation_panel,
)
from .labels import (
    STAGE_AGENTS,
    STRUCTURED_OUTPUT_NAMES,
    category_label,
    model_label,
    pipeline_label,
    presentable,
)
from .replay import replay_stream
from .store import story
from .timeline import describe_tool_payload, parse_tool_payload

__all__ = [
    "RECOMMENDED_QUESTIONS",
    "STAGE_AGENTS",
    "STRUCTURED_OUTPUT_NAMES",
    "answer_issues",
    "answer_payload",
    "category_label",
    "company_choices",
    "describe_tool_payload",
    "evaluation_panel",
    "model_label",
    "parse_tool_payload",
    "pipeline_label",
    "presentable",
    "recommendations",
    "replay_stream",
    "story",
]

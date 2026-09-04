# Presentation candidates

`candidates.json` contains one held-out Coca-Cola example for each question type.
All four come from the final Sonnet 4.5 decomposed pipeline with verification,
which was selected as the best overall architecture in Notebook 04.

Each candidate is ready for a frontend timeline. Events show only observable
model actions, tool calls, tool results, stage handoffs, verification, and the
final answer. They do not contain or reconstruct hidden chain-of-thought. Each
record also includes every metric saved at question level, grouped under
`evaluation`: deterministic checks, workflow state, tool and retrieval metrics,
reliability, performance and cost, and Haiku judge scores. Metrics that exist
only for a complete batch are not presented as if they belonged to one question.

Suggested presentation order:

1. `ko-04` — direct extraction from filing search;
2. `ko-07` — one percentage calculation;
3. `ko-09` — two growth rates, a visible correction, and comparison;
4. `ko-15` — a chained three-year average and comparison.

Show `expected_answer` beside `generated_answer`, with `evaluation.note` as the
caveat line. In `ko-15`, the user-facing answer matches the gold answer, but
strict numerical evaluation failed because the structured response also included
the valid FY2025 input as an additional value. Keeping this caveat makes the demo
honest and illustrates the difference between answer quality and schema-level
scoring.

The `demo/` package renders these records; see `demo/README.md`.

# Evaluation

## Strategy evolution

At first, the evaluator expected the agent to perform every step in exactly the
same order as the example answer. This was too strict: looking up 2025 before
2024 is still correct when both values are needed. The evaluator now accepts a
different order when the same required work was completed correctly.

Changes made after the initial implementation:

- We now check the number itself separately from its formatting. For example,
  `$1,000` and `1000` mean the same value, but a badly formatted structured
  field is reported as a formatting problem rather than a wrong calculation.
- The agent may show source values and intermediate calculations while working.
  These are no longer mistaken for extra final answers.
- The calculator keeps all available decimal places, while the final displayed
  percentage is checked at the precision requested by the question.
- Every final number must point to the evidence that supports it, including the
  correct company and year.
- If the agent was expected to search the filing but did not search it, the
  retrieval score is now correctly zero instead of being skipped.
- Tool and retrieval scores use what actually happened in the saved run. We do
  not run a new search later and accidentally score a different result.
- We added separate measures for formatting, model-output repairs, and whether a
  USD cost is known for the model used.
- Haiku 4.5 adds easy-to-read 1–5 grades for whether the final explanation and
  visible workflow make sense. It cannot make an incorrect financial number pass.

These changes are recomputed from saved records; the evaluator never repairs
model output.

## Deterministic safeguards

- USD values must exactly equal gold after unit normalization; any non-zero difference fails.
- Structured USD values are absolute plain decimals such as `"4300000000"`; the schema has no scale field. Prose renders them as `$4,300,000,000`.
- Percentages use two decimal places by default unless the question asks for a different precision. Calculator records retain full precision.
- Every structured value identifies direct evidence. Its evidence must exist, have the right company and period, and have been returned in the saved run.
- Evidence Recall@5 and irrelevant-retrieval rate use the recorded `search_filing` event, never a new retrieval call during evaluation.
- Unknown model prices remain unknown and are reflected by `cost_coverage_rate`; they are never silently counted as zero.

## Evaluation split

| Area | Method |
| --- | --- |
| Final values, units, periods | Exact deterministic comparison |
| Structured formatting | Deterministic plain-decimal check |
| Calculation coverage | Deterministic one-to-one matching |
| Evidence, tools, retrieval | Deterministic saved-event checks |
| Tokens, latency, retries, repairs, cost | Deterministic saved-run measurements |
| Final-answer semantics and workflow quality | Haiku 4.5, 1–5 grades |

The judge cannot override a deterministic numeric or period failure. A wrong answer may still receive a high auditability grade if its trace makes the mistake easy to inspect.

## Reproducibility

Scored run records are immutable. Development runs can be used to settle the prompt and schema before the experiment. After that, evaluation changes are recomputed from saved records; outputs are never manually repaired.

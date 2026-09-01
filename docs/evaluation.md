# Evaluation

## Purpose

The evaluation answers two questions:

1. Did the agent give the correct financial answer?
2. Can we prove where the answer came from and how it was calculated?

The evaluator uses the saved run exactly as produced. It does not fix answers,
repeat questions, or retrieve new evidence.

## How the evaluation evolved

The first evaluator expected one exact order of lookups and calculations. That
was too strict because looking up fiscal 2025 before fiscal 2024 does not change
the answer.

It was improved over time:

- Required events may appear in a different order, but each approved calculation
  must still use the expected operation, inputs, and result.
- Numerical correctness and formatting are separate. A correct number can pass
  even when it is presented badly.
- Only structured final values are compared with gold. Intermediate values are
  ignored, but missing or unexpected final values cause failure.
- Gold calculations keep full decimal precision. Only final answers are rounded
  to the precision requested by the question.
- Every final value must point to evidence returned for the correct company and
  period. Approved alternative filing evidence is allowed.
- Retrieval is scored from every saved search call, using its first five results.
  No new search is performed during evaluation.
- Failures, retries, repairs, latency, tokens, and cost are now recorded.
- For a decomposed run, calculation checks use only the Math attempt accepted for
  the final answer. Rejected attempts remain visible and still count toward usage.

Because evaluation reads saved runs, these improvements can be applied without
paying to run the research agent again.

## What each score means

| Score | Meaning | Example |
| --- | --- | --- |
| Numerical correctness | All required final numbers and units match gold, with no unexpected extra values. | Gold is `32.21 percent` and the answer is `32.21 percent`: pass. Returning `132.21 percent`: fail. |
| Formatting correctness | Structured numbers and the written answer use the required format and agree. | Structured `15000000 USD` with prose `$15,000,000`: pass. Prose `$15 million`: fail. |
| Evidence validity | Every cited evidence ID was returned by a successful tool call. | Citing `fact-amzn-revenue-fy2025` passes only if that ID appeared in a saved tool result. |
| Evidence completeness | Every final value points to supporting evidence. | A margin calculated from revenue and net income must reference both source evidence IDs. |
| Period validity | The evidence covers the fiscal period stated for the value. | An FY2025 answer supported only by an FY2024 fact fails. |
| Formula correctness | All required calculator steps have the expected operation, inputs, and full-precision result. | Growth requires `new - old`, followed by `(difference / old) × 100`; returning only `new / old` fails. |
| Audit-trail completeness | The answer, evidence, periods, and calculations form a complete reproducible record. | A correct 32.21% answer fails the audit if its saved calculator event shows only 132.21%. |
| Tool metrics | Required tools were used, unnecessary tools were avoided, and calls succeeded. | A normalized revenue question should use `lookup_fact`; calling only `search_filing` lowers the tool score. |
| Retrieval metrics | Relevant evidence appeared in the first five results and unrelated results were limited. | Finding the required filing table at rank 3 passes Evidence Recall@5. |
| Reliability and cost | Successes, failures, retries, repairs, latency, tokens, and known costs are reported. | A Bedrock timeout is recorded as a failed run with its retry count and consumed tokens. |

Formula correctness applies only to the 12 calculation questions per company.
The four direct-extraction questions are excluded from its denominator.

## Examples

### Filing values reported in millions

If a filing shows `15` under a heading saying the values are in millions, the
answer must use the absolute amount:

```text
Structured value: 15000000 USD
Written answer:    $15,000,000
Wrong:             15 USD or $15 million
```

The wrong version fails numerical and formatting correctness even if the right
filing was found.

### Correct number but incomplete proof

Suppose the correct asset growth is `32.21%`, but the only saved calculation is:

```text
new assets / old assets = 132.21%
```

If the final answer states `32.21%` without a recorded step converting the ratio
to growth, numerical correctness passes, but formula correctness and audit-trail
completeness fail. The answer is right, but the saved work does not prove it.

### A different formula path

Order does not matter when the same required calculations are present. However,
the current formula metric still expects the approved calculation steps. A
`divide` followed by a percentage conversion can give the same answer as one
direct `percentage` call, but it will not match gold when gold requires the
direct call. Numerical correctness may therefore pass while formula correctness
fails. This limitation should be considered when reading the formula score.

### Repaired Math attempt

Suppose a decomposed run has two Math attempts:

```text
Attempt 1: used the wrong denominator and was rejected
Attempt 2: used the correct denominator and was accepted
```

Formula and supported-number checks use attempt 2. Attempt 1 remains visible in
the timeline and still counts toward tool calls, latency, tokens, and cost.

## Retrieval

Only questions requiring filing search are included. If no required search is
made, recall is zero. A question passes Evidence Recall@5 if any saved search
finds an approved chunk in its first five results. Irrelevant Retrieval Rate@5 is
averaged across all searches, so a useful second search receives credit without
hiding noise from the first search.

## Saved results

`runs.jsonl` is the official record. It stores the question, observable model and
tool calls, evidence, calculations, final answer, failures, usage, latency,
retries, and cost.

Evaluation writes:

- `question_metrics.csv` for per-question scores;
- `retrieval_metrics.csv` for per-search retrieval scores;
- `summary.json` for batch totals and averages.

The notebook saves a batch before evaluating it. `latest=True` selects the newest
saved batch for the chosen architecture and company. A run ID can select an older
batch. Neither option reruns the research pipeline.

Unknown prices remain unknown and reduce `cost_coverage_rate`; they are not
silently counted as free.

## Exploratory LLM review

The code can optionally use Haiku 4.5 to give descriptive 1–5 grades for answer
quality and workflow. These subjective grades help explain development runs but
are not part of the official deterministic score and cannot override a failed
number, period, formula, or audit check.

## Main rule

A correct number without reproducible evidence and calculations is not a complete
auditable answer. A clear trace of a wrong calculation does not make it correct.

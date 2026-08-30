# Evaluation Strategy Evolution

## Purpose

This document records how evaluation changed while testing the monolithic
financial agent. The goal is to keep exact financial scoring deterministic while
allowing valid tool and calculation orders and separately judging workflow quality.

## Evolution

```text
version 1            version 2             version 3             current
strict order  ──►    hybrid scoring  ──►    batch fixes    ──►    stable contract
```

### Version 1: strict deterministic sequence

The first evaluator compared generated calculations with gold calculations in the
same list order. This incorrectly penalized valid workflows such as retrieving
FY2025 before FY2024 when both facts were available before the comparison.

### Version 2: deterministic precision plus an LLM judge

Exact values, units, periods, evidence IDs, tool events, retrieval, tokens, latency,
and cost remained deterministic. Haiku 4.5 was added for 1–5 scores of answer
correctness, workflow correctness, chronology, efficiency, and auditability.
Required calculations were matched one-to-one without requiring one global order.

### Version 3: fixes found by the first complete batch

The 16-question AMZN run exposed evaluation problems that made the initial 0%
numerical score misleading:

- correct requested values failed when the answer also listed source inputs;
- exact label matching rejected equivalent wording, so semantic concept matching
  moved to the judge;
- rounded final values were incorrectly marked unsupported by exact calculator
  outputs;
- retrieval questions with no `search_filing` call were omitted instead of scored
  as zero recall;
- structured-output repairs were hidden inside model cycles and not counted.

After correcting those rules against the same saved runs, numerical correctness
changed from 0% to 18.75%, Evidence Recall@5 changed from 33.33% to 25%, and
Irrelevant Retrieval Rate@5 changed from 93.33% to 95%. The lower retrieval result
is more honest because the missing search for `amzn-03` is now included.

### Current version: one stable answer contract

Repeated four-question dry runs exposed representation errors separately from
financial errors. The agent sometimes returned scaled values, full-precision
percentages, or different numbers in prose and structured output. The final contract
is deliberately small:

- USD is an absolute numeric string in `values`, such as `"1000000"`;
- readable prose uses `$1,000,000`;
- percentages use two decimal places unless the question requests another precision;
- prose and the structured percentage must agree;
- calculator records retain their full precision;
- `scale` was removed because all structured values are absolute.

Formatting is scored separately from numerical correctness. The deterministic
numerical check compares the value and unit with gold, while
`formatting_correctness_rate` checks the plain structured output contract. Semantic
concept and conclusion quality remain separate from numeric scoring.

### Clean AMZN development checkpoint

After deleting exploratory runs, all 16 AMZN questions were run from scratch with
Nova Lite and judged with Haiku 4.5. All runs completed, all percentage fields used
two decimals, and no answer contained `scale`. Numerical correctness was 37.50%:
direct extraction 50%, single formula 100%, period comparison 0%, and multi-step 0%.
The trace showed a repeated growth error where the agent reported `new / old × 100`
instead of `(new - old) / old × 100`. This is a model-workflow failure preserved in
the run record, not an evaluation repair.

## Current Evaluation Split

| Area | Evaluation |
| --- | --- |
| Final numbers, units, periods | Exact deterministic comparison; any non-zero difference fails |
| Structured value formatting | Separate deterministic check for plain decimal structured values |
| Required calculation coverage | Deterministic one-to-one matching; independent order is allowed |
| Evidence validity and completeness | Deterministic evidence-ID checks |
| Tool and retrieval metrics | Deterministic canonical-event checks |
| Tokens, latency, retries, repairs, cost | Deterministic saved-run measurements |
| Semantic answer correctness and workflow quality | Haiku 4.5 scores from 1–5 |

Numerical and period checks remain deterministic in the per-question metric rows.
`llm_answer_correctness_grade` is Haiku's independent 1–5 semantic grade and never
overrides a deterministic failure.

The judge cannot override a deterministic numeric or period failure. Its
auditability score is independent of correctness: a wrong answer may still be easy
to audit when its trace clearly exposes the mistake.

### Multi-period evidence

One filing table can support several periods under one stable evidence ID. Period
validation therefore keeps every period associated with that ID instead of letting
the last gold input overwrite earlier ones. For example, one approved AWS table can
support both FY2024 and FY2025. It passes a FY2024-to-FY2025 answer, but fails if the
answer requests a period that the table does not contain. Retrieved alternatives are
not accepted automatically; they must first be manually registered as gold evidence.

## What the Agent Trace Revealed

The first batch produced 16 runs, 60 model cycles, 71 project-tool calls, and 12
structured-output repair cycles. Repairs occurred mainly because the first final
output omitted required units, periods, or conclusions. Tool responses also carried
large unused metadata, causing later model inputs to grow substantially.

The implementation now:

- requires every USD output as absolute dollars;
- asks for only requested final values in `values`;
- explains that calculated values cite input evidence, not invented calculation IDs;
- defines growth as `(new - old) / old × 100`;
- requires calculated differences and direction words to agree with the numbers;
- compacts fact and filing metadata returned to the model;
- records structured-output repairs separately from provider retries.

## Reproducibility Rule

Scored experiment records are immutable. The runs described above were development
dry runs used to settle the prompt and schema, so they were removed before the clean
experiment. Once scored runs begin, evaluation changes are recomputed from the same
records and outputs are never manually repaired.

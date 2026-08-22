# Evaluation dataset

This directory contains the source-reviewed benchmark built from the six frozen
SEC files listed in `data/manifest.csv`.

## Fixed splits

| Company | Role | Questions |
| --- | --- | ---: |
| Amazon.com, Inc. (`AMZN`) | Train/development | 16 |
| Alphabet Inc. (`GOOGL`) | Validation | 16 |
| The Coca-Cola Company (`KO`) | Test/held-out | 16 |

Do not use Coca-Cola run results to change prompts, retrieval, tools, formulas,
verification rules, or model settings.

## Files

- `AMZN/` contains Amazon's development/training data.
- `GOOGL/` contains Alphabet's validation data.
- `KO/` contains Coca-Cola's held-out/test data.

Each company directory has:

- `company_facts.jsonl`: the normalized, verified Company Facts observations
  used for that company. Each observation has a stable evidence ID, value,
  fiscal period, accession, source checksum, printed filing page, and
  financial-statement name. All fact values in this MVP are USD. Pages and
  statement names are the human-readable filing locators.
- `questions.jsonl`: the 16 questions for that company and split.

Each file has four direct extraction, four single-formula, four
period-comparison, and four multi-step questions. Four questions per company
require `search_filing`; the rest use normalized facts, calculations, or both.
This keeps the benchmark from testing `lookup_fact` alone.

Gold records specify the minimum required tool-name set, approved evidence IDs,
calculation graph, printed filing pages, and filing sections. The fields used by
deterministic evaluation are:

- `required_tools`: minimum tool names needed for Tool Precision, Recall, F1,
  Exact Match, Invocation, and Conditional Execution metrics. It intentionally
  does not prescribe call order or the number of calls;
- `gold_evidence_ids`: minimum fact and filing records needed to support the
  answer and calculation audit trail;
- `gold_retrieval_evidence_ids`: canonical filing chunks used for Evidence
  Recall@5;
- `relevant_retrieval_evidence_ids`: all accepted relevant filing chunks used
  to calculate Irrelevant Retrieval Rate@5 without marking an equivalent page
  and table representation as irrelevant;
- `gold_numeric_inputs`: filing-derived values, periods, units, and evidence
  links used when a calculation depends on a value that is not in normalized
  Company Facts;
- `gold_calculations`: the expected operation graph and intermediate results.

The retrieval fields appear only on the four questions per company that require
`search_filing`. Runtime-only metrics—success, failure category, latency,
tokens, retries, and cost—come from saved run records rather than gold data.

The `answer.response_type` field defines the scoring shape:

- `number` and `labeled_number` contain directly scoreable values;
- `paragraph` contains directly scoreable required values plus concise expected
  comparison claims for audit inspection.

Every question has at least one numeric gold value, so numerical correctness is
deterministic and does not require an LLM judge. Claim wording is not used as a
semantic grading metric.

The question text states only the research question. Calculation procedures,
tool choices, evidence targets, and scoring expectations remain in gold
metadata and are not exposed as hints in the prompt.

There are 43 period observations across 15 fact series. Income-statement and
cash-flow facts cover fiscal 2023–2025. Alphabet and Coca-Cola total assets cover
2024–2025 because their frozen balance sheets present two comparative year ends;
Amazon also reports a 2023 asset observation in its frozen filing.

`net_income` means income attributable to the parent (`us-gaap:NetIncomeLoss`).
This avoids mixing Coca-Cola's consolidated `ProfitLoss` value with the amount
attributable to Coca-Cola shareowners.

## Dataset status

The normalized facts, filing-derived numeric inputs, calculation graphs, and
stable chunk evidence IDs are reviewed against the frozen sources. Before
scored runs, freeze this dataset together with the chunk parser and retrieval
configuration. After that freeze, do not modify held-out records based on
experiment results.

The selected facts use only observations whose `accn` equals the frozen 10-K
accession. They do not use the latest Company Facts observation because the
downloaded JSON may also contain later filings. Each selected value was checked
against the corresponding frozen 10-K.

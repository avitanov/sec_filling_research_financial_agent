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
- `gold_evidence_ids`: canonical fact and filing records for the reviewed gold
  path. Filing IDs in this list are the required targets for Evidence Recall@5;
- `alternative_retrieval_evidence_ids`: additional filing chunks accepted as
  relevant for Irrelevant Retrieval Rate@5, such as a supporting page or a
  duplicate table containing the same reviewed values;
- `gold_numeric_inputs`: filing-derived values, periods, units, and evidence
  links used when a calculation depends on a value that is not in normalized
  Company Facts;
- `gold_calculations`: the expected operation graph and intermediate results.

Every question record has the same top-level fields. Alternative retrieval IDs
and filing-derived numeric inputs use `[]` when they do not apply. Runtime-only
metrics—success, failure category, latency, tokens, retries, and cost—come from
saved run records rather than gold data.

## Question record schema

| Attribute | Purpose | Used by evaluation |
|---|---|---|
| `question_id` | Stable identifier such as `amzn-12`. | Matches a saved run to its gold record. |
| `question` | Natural-language question sent to the agent. | Identifies the task being evaluated. |
| `ticker` | Company ticker (`AMZN`, `GOOGL`, or `KO`). | Loads the company facts and matches the run company. |
| `company` | Full company name. | Describes the target company and is included in the runtime question. |
| `category` | `direct_extraction`, `single_formula`, `period_comparison`, or `multi_step`. | Produces per-category metric summaries. |
| `split` | `development`, `validation`, or `held_out`. | Keeps experiment partitions separate. |
| `required_tools` | Minimum tool names needed for the question. | Computes tool precision, recall, F1, exact match, invocation, and execution metrics. |
| `answer` | Gold answer shape and expected values. | Computes numerical and formatting correctness. |
| `gold_evidence_ids` | Canonical facts and filing records for the reviewed gold path. | Supplies calculation sources and required filing retrieval targets. The agent may cite another returned source for its final values. |
| `alternative_retrieval_evidence_ids` | Additional filing IDs accepted as relevant, such as a parent page or duplicate table. | Prevents equivalent retrieval results from being counted as irrelevant. |
| `gold_numeric_inputs` | Exact manually verified values from filing tables that are absent from Company Facts. | Resolves filing-specific calculation inputs and validates supported values. |
| `gold_calculations` | Expected calculation operations, inputs, and results. | Computes formula correctness and verifies the calculation audit trail. |
| `gold_filing_pages` | Printed filing pages containing the source information. | Human review and provenance; not used directly in metric formulas. |
| `gold_filing_statements` | Filing statements or sections containing the source information. | Human review and provenance; not used directly in metric formulas. |

### Nested `answer` fields

| Attribute | Purpose |
|---|---|
| `response_type` | Defines the answer shape: `number`, `labeled_number`, or `paragraph`. |
| `value` | Expected numeric result for a scalar answer. |
| `unit` | Expected unit, normally `USD` or `percent`. |
| `rounding` | Documents the intended gold precision. |
| `label` | Name of a labeled value, such as `AWS`. |
| `required_values` | Expected numeric values for a paragraph answer. |
| `required_claims` | Expected factual conclusions for a paragraph answer. |

### Nested `gold_numeric_inputs` fields

| Attribute | Purpose |
|---|---|
| `evidence_id` | Filing evidence record containing the value. |
| `input_id` | Identifier referenced by a gold calculation. |
| `label` | Human-readable input name. |
| `period` | Fiscal period of the input. |
| `unit` | Unit of the input. |
| `value` | Exact frozen numeric value. |

### Nested `gold_calculations` fields

| Attribute | Purpose |
|---|---|
| `calculation_id` | Stable identifier for one calculation step. |
| `input_refs` | Fact or previous-calculation IDs used as inputs. |
| `constant_inputs` | Direct numeric constants such as `3` when averaging three years. |
| `operation` | Allowed operation, such as `add`, `subtract`, `divide`, or `percentage`. |
| `result` | Exact result returned by the canonical Decimal calculator. Intermediate results are never rounded; display rounding belongs only in `answer`. |

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

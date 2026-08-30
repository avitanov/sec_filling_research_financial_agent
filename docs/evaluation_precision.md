# Evaluation precision safeguards

- **USD values must exactly equal gold.** A difference of `0.001` fails after unit
  normalization.
- **Percentage outputs use two decimal places by default.** A question may request
  another precision. Calculator records retain full precision, while the final
  structured value and prose use the same rounded result.
- **New answers use absolute numbers.** The prompt requires `4300000000` with
  `unit="USD"`, rather than `4.3 billion`. The answer schema has no `scale` field.
- **Every answer value names its direct evidence.** `source_evidence_ids` ties a
  number to the returned source and its period, instead of relying on one broad
  list of citations for the whole answer. For example, an FY2025 value linked only
  to FY2024 evidence fails the period check.
- **Valid calculation order is flexible.** Required calculations are matched
  one-to-one, so independent steps may be reordered. Missing required calculations
  fail; extra calculations affect the judge's efficiency score instead of turning
  a correct result into an incorrect one.
- **Retrieval metrics use the saved run.** Evidence Recall@5 and irrelevant-result
  rate now read the recorded `search_filing` outputs. Re-running retrieval during
  evaluation could have measured a different result than the agent actually used.
- **Unknown costs stay unknown.** Missing pricing is excluded from cost totals and
  reported through `cost_coverage_rate`, rather than being silently counted as $0.
- **Semantics and workflow are judged separately.** Haiku 4.5 returns 1–5 scores
  for answer correctness, workflow correctness, chronology, efficiency, and
  auditability. It cannot turn a deterministic numeric failure into a correct
  answer.

# Measured evidence

Tracked files contain aggregate engineering and model evaluation evidence. Detailed district demand, allocation and intermediate diagnostic files remain local. Dashboard data is released only through the protected serving exporter.

Checkpoint status: the current forecast/detection CSVs describe the SF=0.01 incremental demo. The current scalability files contain the two completed local baseline profiles, not a completed three-scale or SF=1 experiment. Final cloud results will replace these working summaries after actual execution; see [resume notes](../docs/RESUME.md).

- `scalability.json`: fresh generated-feed and lake runs at SF 0.25, 0.5 and 1, with actual stage timing, input/output size and hardware. Public source downloads are cached; this is explicit in the record.
- `scalability_baseline.json`: two completed profiles before the performance revision. That experiment stopped before SF=1 generation and is not a full-scale timing result.
- `evaluation_summary.json`, `forecast_summary.csv`, `detection_tradeoff.csv`: national synthetic-data evaluations against stated baselines.
- `incremental_acceptance.json`: fourteen-day acceptance feed, late-week update, invalid/duplicate quarantine, source lineage and exact-repeat checks. The fixture is separate from the instructor's hidden assessment.
- `performance/`: before/after Spark query plans and measured repeated query timings.
- `validation_summary.json`: final executed checks and their evidence files.

Files are added only after the corresponding checks execute. A missing file is not evidence of a completed experiment. Screenshots and browser checks are in `docs/evidence/`. Report, policy brief and presentation work remain deferred.

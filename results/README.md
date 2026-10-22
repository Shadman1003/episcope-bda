# Measured evidence

Tracked files contain aggregate engineering and model evaluation evidence. Detailed district demand, allocation and intermediate diagnostic files remain local. Dashboard data is released only through the protected serving exporter.

Current release: the forecast/detection CSVs and evaluation summary describe the validated SF=1 study. `scalability.json` contains the three completed primary cloud profiles; `scalability_baseline.json` preserves the earlier two-profile local experiment. Corrected query timings and complete live UI evidence come from an independent SF=1 study on identical recorded inputs. Sources, timer scopes and corrections are recorded in [validation notes](../docs/validation.md).

- `scalability.json`: fresh generated-feed and lake runs at SF 0.25, 0.5 and 1, with actual stage timing, input/output size and hardware. Public source downloads are cached; this is explicit in the record.
- `scalability_baseline.json`: two completed profiles before the performance revision. That experiment stopped before SF=1 generation and is not a full-scale timing result.
- `evaluation_summary.json`, `forecast_summary.csv`, `detection_tradeoff.csv`: national synthetic-data evaluations against stated baselines.
- `incremental_acceptance.json`: fourteen-day acceptance feed, late-week update, invalid/duplicate quarantine, source lineage and exact-repeat checks. The fixture is separate from the instructor's hidden assessment.
- `performance/`: before/after Spark query plans and measured repeated query timings.
- `validation_summary.json`: final executed checks and their evidence files.

Files are added only after the corresponding checks execute. A missing file is not evidence of a completed experiment. Screenshots and browser checks are in `docs/evidence/`. Report, policy brief and presentation work remain deferred.

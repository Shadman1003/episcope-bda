# Validation record

Updated 22 October 2026. Implementation validation is complete: three fresh scale profiles, an independent SF=1 Spark query/UI study, 41 Python tests, incremental acceptance, and the final protected dashboard's browser/API checks. The report, policy brief and presentation remain deferred.

## Full-scale execution

Primary experiment: [run 39705416049](https://github.com/Shadman1003/episcope-bda/actions/runs/39705416049), source `b25ea19783c9ad49a7d55fb206a7311abe079c66`. All three full pipeline commands completed successfully. Its workflow later failed in an additional test step because a Spark worker selected system Python; that environment issue was corrected and the complete suite passed separately. This does not relabel the failed workflow as successful.

| SF | Submitted encounters | References (s) | Generation (s) | Pipeline (s) | Analytics (s) | Export (s) | Complete command (s) | Lake bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 6,412,101 | 1.676 | 35.361 | 184.588 | 81.438 | 4.084 | 319.112 | 959,030,026 |
| 0.5 | 12,858,721 | 1.810 | 72.239 | 297.057 | 85.554 | 3.989 | 470.751 | 1,889,522,728 |
| 1 | 25,708,357 | 1.530 | 119.146 | 466.473 | 73.204 | 3.787 | 674.915 | 3,729,563,409 |

SF=1 generated **25,204,173 unique encounters plus 504,184 resubmissions**, 640 facilities and 1,168,640 stock snapshots. The complete command took **674.915 seconds (11.25 minutes)**, below 45 minutes. Public downloads were cached; encounter feeds and lake transformations were fresh. The outer timer includes Python/JVM startup and shutdown, excluding dependency installation and cache extraction. Stage totals exclude some orchestration overhead.

Runners used Python 3.12.3, Java 11, Spark 3.5.3, four logical CPUs, approximately 16.76 GB RAM, `local[4]`, 4 GiB driver memory and 16 shuffle partitions. SF 0.25/0.5 used AMD EPYC 7763; SF=1 used Intel Xeon Platinum 8573C. These are separate cloud VMs, not one controlled laptop. Hardware and cache differences prevent attributing cross-host runtime changes solely to code. Exact hardware, input/output sizes and stages are in [scalability.json](../results/scalability.json).

The earlier 8 GB WSL baseline completed SF 0.25 in 1,143.664 s and SF 0.5 in 1,878.403 s, then stopped before SF=1. It remains in [scalability_baseline.json](../results/scalability_baseline.json). The initial cloud experiment also completed; its SF=1 command took 534.106 s on a different Intel host. The primary table above records the later corrected-source experiment.

## Automated checks

- **41 passed in 36.36 seconds**, [test run 39706621713](https://github.com/Shadman1003/episcope-bda/actions/runs/39706621713), source `bb16e184758956f8fbb1c6c497420f82ff7648d9`. See [actual test log](evidence/python-tests.txt) and [tested revision](evidence/python-tests-source-revision.txt).
- The preceding local suite passed 37 tests in 127.06 s; its log is preserved in [python-tests-wsl.txt](evidence/python-tests-wsl.txt). Four regression checks now reject reused query executions, reused physical plans and changing sample results.
- The original cached-feed SF=0.01 end-to-end run took 495.682 s. The fourteen-day incremental CLI took 348.137 s. These local demo timings are separate from the fresh full-scale measurements.

## Incremental acceptance

The local fourteen-day fixture is separate from the instructor's hidden assessment. The completed CLI run extended the onset range through 14 January 2026 and regenerated analytics and the dashboard. A subsequent acceptance harness added orphan, negative and duplicate stock examples, then checked the production pipeline and its exact rerun.

The acceptance record contains:

- **248,507** valid encounters, including **610** new valid encounter identities, and **1,168,649** valid stocks, including **9** new facility/date snapshots.
- Six defective encounter rows and twelve duplicate fixture rows in encounter quarantine; one orphan stock row, one negative stock row and one duplicate stock snapshot in the stock quarantine tables.
- A late influenza onset in the week beginning **22 December 2025** changing the checked district/week count from **0 to 1**.
- An exact repeat returning `idempotent_noop: true`, zero new batches and **0.0781248 s**, with encounter counts unchanged. This is the pipeline no-op timing; it does not include another analytics/export run.
- Unique retained encounter IDs and removal of `patient_id` and `age_years` from silver encounters.
- A `lineage_id` column in every gold table, sampled lineage IDs resolving to saved manifests, and the restored `gold_weekly` SQL view matching the persisted table count.

The acceptance pipeline reconciled 1,426,738 encounter/stock input rows to 1,417,156 retained rows and 9,582 quarantined rows. **Duplicates are a subset of quarantine**: 4,993 duplicate or superseded rows are included in that 9,582 and must not be added again. Rule counts can overlap when one row violates several rules. The older first-run quality summary predates combined stock/duplicate accounting and should not be compared as though its quarantine definition were identical.

Each published gold table points to the manifest used when that table was written. The final acceptance harness refreshed pipeline marts without rerunning analytics; its record therefore contains different valid lineage IDs for those stages. It verifies resolvable dependencies, not that every table was republished in one transaction.

## Final dashboard evidence

The shipped release has SHA256 `981094cba6b256b1dc3d429eb2130c46dc5b5251985627bbc8588322a89b3416`, **2,568,942 bytes**, 64 districts and four diseases. Three actual browser scripts passed **38 checks in 61.53 s**, using this exact release and the frontend hashes in [run-context.json](evidence/run-context.json).

Checks covered all five linked views, map/brush/zoom interactions, detector and allocation controls, 14 detector points, 24 allocation scenarios, six budget curves, keyboard tooltips with scroll/Escape behavior, 390/768/1440-pixel layouts, scoped API responses, invalid tokens, denied static data and traversal attempts. Desktop, mobile and policy screenshots were visually inspected. [Browser/API record](evidence/dashboard-browser-qa.json) and [script results](evidence/browser-run-results.json) retain the actual outputs. The initial demo images were backed up locally; current generic dashboard screenshots describe this SF=1 release.

## Controlled Spark optimisation study

The complete independent [SF=1 study 39707413847](https://github.com/Shadman1003/episcope-bda/actions/runs/39707413847), source `2e35c285f9b1d2e5486d868039e9d7130187e7c9`, used the same recorded generated inputs as the primary SF=1 profile. All input file metadata and reference hashes were checked. Its preceding fresh full command took 765.467 s on AMD EPYC 7763 with four logical CPUs and about 16.76 GB RAM. This additional run does not replace the primary scaling timers or dashboard.

| Controlled comparison | Before median (s) | After median (s) | Ratio |
|---|---:|---:|---:|
| Registry join: merge to broadcast | 6.640693 | 1.447631 | 4.59x |
| Year predicate: Python UDF to native pruning | 19.749418 | 0.062374 | 316.63x |

Every case has three fresh DataFrame/QueryExecution samples, twelve distinct execution IDs and twelve distinct physical-plan IDs overall, matching sample and before/after aggregate results, twelve sample plans and four representative plans. Query timing includes collect, lazy physical planning and result conversion; factory construction, plan export, browser startup and screenshots are excluded. AQE and automatic broadcast selection are disabled for the controlled comparison. Fixed case order, JVM/OS caches and contention remain confounders; these ratios are query-specific, not whole-pipeline speedups.

The native predicate selects the latest ISO-year edge partition, **21,904 records**, so its large ratio should not be generalized to a typical full year. Authentic UI captures show SortMergeJoin, BroadcastHashJoin, BatchEvalPython and the native `epi_year` partition filter for application `local-1792634314841`. The capture record, physical-operator checks, screenshot hashes and source hash are in [capture_manifest.json](evidence/optimisations/capture_manifest.json); timings and all samples are in [optimisations.json](../results/performance/optimisations.json).

The first timing harness reused a DataFrame's materialized shuffle between repeated collections; its median ratios were rejected. The corrected study in run 39705416049 had valid timings but its 12-second Chrome startup limit expired. The final independent study extended browser startup/capture budgets, performed untimed browser preflight, and captured all four actual cases. These failures and corrections are retained in history rather than represented as successful initial checks.

The [quarter-scale consistency comparison](../results/performance/pipeline_comparison.json) compares the earlier local baseline with the first cloud profile: recorded input hashes, quality counts and detector summaries match; forecast and LP metrics agree within 1e-9; five protected clinical dashboard tables are equal. It does not claim raw/silver record equality or equality of every historical daily signal.

## Synthetic-data analytical results

The rolling-origin backtest scores 52 mature target weeks for four diseases and horizons 1-4, with 53,248 district-week prediction records per model. Pooled ridge MAE is **56.730** versus **97.564** for seasonal naive; WIS is **47.154** versus **81.991**. Measured interval coverage is **0.7782**, below nominal 0.80, versus baseline **0.7710**. These are pooled district forecast errors, not errors of summed national forecasts. Influenza intervals under-cover, and its horizon-four WIS is slightly worse than baseline; pooled gains do not mean every task improves.

Calibration selected CUSUM threshold 10: 26/26 calibration events and 10/10 holdout events detected, with seven-day median issue-date delay and 4.562 false-alert episodes per district-year on holdout. Only ten constructed events form that holdout. The reporting-artefact window produced four naive report-date C2 alert rows and zero mature-onset alert rows. Those flags are not the false-alert episode counting unit.

The selected 74,979.584-unit budget with a 20% equity floor reduces weighted unmet need from 38,843.235 to 20,559.521, a **47.07%** improvement over the population baseline. The 24-scenario grid spans six budgets and four equity floors. Generic costs, continuous capacity and constructed clinical mechanisms require validation before operational use. Full metrics and limitations are in [evaluation_summary.json](../results/evaluation_summary.json).

## Retained evidence and local state

[validation-evidence.zip](../resources/validation-evidence.zip) retains the verified aggregate profiles, independent query/UI study and successful test proof; its companion manifest records every member's size and SHA256. It contains no raw encounter feed, lake table, salt or credential. The public release is ready for `python -m serving.server --port 8765` without Spark dependencies. Readable provenance copies are under [evidence/cloud/](evidence/cloud/).

The D: working copy preserves its SF=0.01 incremental backend under `data/`; downloaded full-scale aggregate evidence is under `results/cloud/`. Its shipped dashboard is SF=1. Native WSL remains unresponsive on the implementation host; full-scale backend verification completed in Linux CI, while the dashboard/browser checks ran with Windows Python/Chrome. No system restart or disk cleanup was performed during this final phase.

## Scope boundaries and remaining validation

The optional Docker files are **untested**: no image build, Compose execution or container performance run has completed on this host. See [`docs/docker.md`](docker.md). The executed environment above is Ubuntu WSL.

Local filesystem Parquet is the working storage backend. HDFS/MinIO, Structured Streaming, ACID time travel, calibrated SEIR scenarios, record-level extracts and cross-facility patient-episode deduplication are not implemented claims. Exact encounter-ID resubmissions and superseded valid versions are handled separately from cross-facility episode matching. As-of analytical cohorts use retained report dates; they do not reconstruct overwritten ingestion/revision history.

The API provides optional local bearer-token roles, rather than production authentication. Facility completeness is a relative reporting-activity proxy: an encounter line list alone cannot distinguish a genuine zero-case submission from an absent report. Treatment conversions and budget units require local validation, and continuous bed/team capacity is not a dispatch manifest.

The technical report, policy brief, slides, signed contribution records, individual reflections and recorded presentation remain deferred. No human usability sessions or unexecuted optional features should be inferred from the automated evidence.
